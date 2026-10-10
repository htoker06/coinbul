"""(12) Geçmiş veriyle test: ajanın kurallarını geçmiş mumlar üzerinde dener.

Kullanım:
    python backtest.py --days 90 --top 20
    python backtest.py --days 180 --symbols BTC_TRY,ETH_TRY,AVAX_TRY
    python backtest.py --days 90 --top 20 --karsilastir   # farklı ayarları yan yana dener

Nasıl çalışır:
  * 15 dakikalık mumlar indirilir (data/history/ altına kaydedilir), 30dk/1s/4s/günlük mumlar
    bunlardan üretilir. Her 15 dakikada bir, o ana kadar kapanmış mumlarla karar verilir
    (geleceğe bakılmaz).
  * Alım bir sonraki mumun açılışından, %0,1 kayma + komisyonla yapılır.
  * Aynı mumda hem stop hem hedef görülürse önce stopun çalıştığı varsayılır (kötümser).
Sınırlar: Saniyelik işlem akışı, emir defteri, sahte emir duvarı, global karşılaştırma ve
1dk/5dk pump kontrolü geçmiş veride yoktur; bunlar sadece sanal/canlı modda çalışır.
Bu yüzden gerçek sonuçlar testten farklı olabilir.
"""
import argparse
import bisect
import csv
import dataclasses
import json
import time
from collections import defaultdict

from analysis import btc_regime, evaluate, resample
from config import Config
from exchange import BinanceTR, market_symbol
from risk import Risk, edge_ok, entry_plan, new_state

TF_MINUTES = {"15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
STEP_MS = 15 * 60_000
ENTRY_SLIPPAGE = 0.001


def download(client, symbol, days, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{symbol}_15m_{days}g.json"
    if path.exists() and time.time() - path.stat().st_mtime < 6 * 3600:
        return json.loads(path.read_text())
    start = int((time.time() - days * 86400) * 1000) - 60 * 86400 * 1000  # +60 gün ısınma
    out = []
    while True:
        batch = client.klines(symbol, "15m", 1000, start_ms=start)
        if not batch:
            break
        out.extend(batch)
        start = int(batch[-1][0]) + STEP_MS
        if len(batch) < 1000:
            break
        time.sleep(0.2)
    path.write_text(json.dumps(out))
    return out


class Series:
    """Bir coinin tüm zaman dilimleri; t anında kapanmış mumları geleceğe bakmadan verir."""

    def __init__(self, candles15):
        self.tf = {"15m": [list(map(float, k[:12])) for k in candles15]}
        for tf, mins in TF_MINUTES.items():
            if tf != "15m":
                self.tf[tf] = resample(self.tf["15m"], mins)
        self.close_times = {tf: [k[0] + TF_MINUTES[tf] * 60_000 for k in ks] for tf, ks in self.tf.items()}

    def window(self, tf, t, n=60):
        ks = self.tf[tf]
        k = bisect.bisect_right(self.close_times[tf], t)
        if k == 0:
            return []
        w = ks[max(0, k - n):k]
        return w + [w[-1]]  # tf_metrics son (kapanmamış) mumu atar

    def index_at(self, t):
        return bisect.bisect_right(self.close_times["15m"], t) - 1


class Precomputed:
    """Sinyaller ayarlardan bağımsızdır; bir kez hesaplanıp her ayar denemesinde yeniden kullanılır."""

    def __init__(self, history, btc=None, start_ms=None):
        self.series = {s: Series(c) for s, c in history.items() if len(c) > 200}
        self.btc = Series(btc) if btc else None
        self.times = sorted({k[0] + STEP_MS for s in self.series.values() for k in s.tf["15m"]
                             if not start_ms or k[0] + STEP_MS >= start_ms})
        self.signals = {}
        self._regime = {}
        for t in self.times:
            out = []
            for sym, s in self.series.items():
                i = s.index_at(t)
                if i < 96 or s.close_times["15m"][i] != t:
                    continue
                tfs = {tf: s.window(tf, t) for tf in TF_MINUTES}
                if any(len(w) < 4 for w in tfs.values()):
                    continue
                day = s.tf["15m"][i - 95:i + 1]
                ticker = {"quoteVolume": sum(k[7] for k in day), "lastPrice": day[-1][4],
                          "priceChangePercent": (day[-1][4] / day[0][1] - 1) * 100}
                sig = evaluate(sym, tfs, [], None, ticker)
                sig.spread_pct = 0.0
                sig.listing_days = len(tfs["1d"]) - 1
                out.append(sig)
            out.sort(key=lambda x: x.score, reverse=True)
            self.signals[t] = out

    def regime(self, t, max_drop):
        key = (t, max_drop)
        if key not in self._regime:
            b = self.btc
            self._regime[key] = btc_regime(b.window("4h", t), b.window("1d", t), b.window("1h", t), max_drop)
        return self._regime[key]


def run_backtest(cfg, history=None, btc=None, start_ms=None, end_ms=None, pre=None):
    """history: {sembol: 15dk mumlar}. Döner: (özet, işlemler).
    pre: daha önce hesaplanmış Precomputed (karşılaştırmada hız için)."""
    pre = pre or Precomputed(history, btc)
    series = pre.series
    state = new_state(cfg)
    risk = Risk(cfg, state)
    positions, trades = {}, []
    equity = peak_equity = cfg.paper_start_try
    max_dd = 0.0
    costs = 0.0  # komisyon + kayma toplamı

    times = [t for t in pre.times if (not start_ms or t >= start_ms) and (not end_ms or t < end_ms)]

    def close(sym, pos, price, reason, t, frac=1.0):
        nonlocal equity, costs
        qty = pos["qty"] * frac
        gross = qty * price
        proceeds = gross * (1 - cfg.fee_rate)
        costs += gross * cfg.fee_rate
        cost = pos["cost"] * frac
        pnl = proceeds - cost
        pos["qty"] -= qty
        pos["cost"] -= cost
        final = frac >= 1.0
        risk.record_close(sym, pnl, t / 1000, final=final)
        state["cash_try"] += proceeds
        equity += pnl
        trades.append({"zaman": time.strftime("%Y-%m-%d %H:%M", time.gmtime(t / 1000)), "sembol": sym,
                       "islem": "SAT" if final else "KISMİ SAT", "giris": round(pos["entry"], 8),
                       "cikis": round(price, 8), "kar_zarar_try": round(pnl, 2), "sebep": reason,
                       "sinyal": pos["label"], "pozisyon_id": pos["id"]})
        if final:
            del positions[sym]

    for t in times:
        now = t / 1000
        risk.roll_periods(now)

        # 1) Açık pozisyonlar: bu 15dk mumun yüksek/düşük değerleriyle stop/hedef kontrolü
        for sym, pos in list(positions.items()):
            s = series[sym]
            i = s.index_at(t)
            if i < 0 or s.tf["15m"][i][0] < pos["opened_ms"]:
                continue
            _, o, h, lo, c = s.tf["15m"][i][:5]
            if lo <= pos["stop"]:
                price = min(o, pos["stop"])
                kind = "zarar durdur" if pos["stop"] < pos["entry"] else "iz süren stop"
                close(sym, pos, price, kind, t)
                continue
            tp = pos["entry"] * (1 + pos["tp_pct"] / 100)
            if h >= tp:
                close(sym, pos, max(o, tp), "kâr al", t)
                continue
            part = pos["entry"] * (1 + cfg.partial_take_r * pos["stop_pct"] / 100)
            if not pos["partial"] and h >= part:
                pos["partial"] = True
                close(sym, pos, part, "kademeli kâr al", t, frac=cfg.partial_fraction)
                pos["stop"] = max(pos["stop"], pos["entry"] * (1 + 2 * cfg.fee_rate))
            pos["peak"] = max(pos["peak"], h)
            if (pos["peak"] / pos["entry"] - 1) * 100 >= cfg.trailing_start_r * pos["stop_pct"]:
                pos["stop"] = max(pos["stop"], pos["peak"] * (1 - pos["trail_pct"] / 100))
            if (t - pos["opened_ms"]) / 3_600_000 >= cfg.max_hold_hours:
                close(sym, pos, c, "süre doldu", t)

        # 2) Sinyaller (mum kapanışında)
        signals = pre.signals.get(t, [])
        risk.update_streaks(signals)
        risk.update_sell_streaks(signals)
        for sig in signals:
            pos = positions.get(sig.symbol)
            if pos and risk.signal_exit(sig.symbol, sig.price < pos["entry"]):
                close(sig.symbol, pos, sig.price, f"sinyal döndü: {sig.label}", t)
        state["positions"] = {k: True for k in positions}

        # 3) Alımlar: bir sonraki mumun açılışından
        regime = pre.regime(t, cfg.btc_max_drop_1h_pct) if (pre.btc and cfg.btc_filter) else None
        if not risk.global_block(now, False, regime):
            for sig in signals:
                if len(positions) >= cfg.max_open_positions or risk.global_block(now, False, regime):
                    break
                if sig.quote_volume_24h < cfg.min_quote_volume_try:
                    continue
                if risk.symbol_block(sig, {}, now) or sig.listing_days < cfg.min_listing_days:
                    continue
                s = series[sig.symbol]
                i = s.index_at(t)
                if i + 1 >= len(s.tf["15m"]):
                    continue
                plan = entry_plan(cfg, sig.atr_pct)
                if not edge_ok(cfg, plan):
                    continue
                if state["cash_try"] < plan["size_try"]:
                    break
                open_price = s.tf["15m"][i + 1][1]
                entry = open_price * (1 + ENTRY_SLIPPAGE)
                qty = plan["size_try"] / (1 + cfg.fee_rate) / entry
                costs += plan["size_try"] - plan["size_try"] / (1 + cfg.fee_rate) + qty * (entry - open_price)
                state["cash_try"] -= plan["size_try"]
                risk.record_buy()
                positions[sig.symbol] = {
                    "id": f"{sig.symbol}-{t}", "qty": qty, "cost": plan["size_try"], "entry": entry,
                    "peak": entry, "stop": entry * (1 - plan["stop_pct"] / 100), "partial": False,
                    "opened_ms": t, "label": sig.label, **plan}
                state["positions"][sig.symbol] = True
                trades.append({"zaman": time.strftime("%Y-%m-%d %H:%M", time.gmtime(t / 1000)),
                               "sembol": sig.symbol, "islem": "AL", "giris": round(entry, 8), "cikis": "",
                               "kar_zarar_try": "", "sebep": f"puan {sig.score}", "sinyal": sig.label,
                               "pozisyon_id": positions[sig.symbol]["id"]})

        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, peak_equity - equity)

    # Test sonunda açık kalanlar son fiyattan kapatılır
    last_t = times[-1] if times else 0
    for sym, pos in list(positions.items()):
        s = series[sym]
        i = s.index_at(last_t)
        close(sym, pos, s.tf["15m"][i][4], "test sonu", last_t)

    days = (times[-1] - times[0]) / 86_400_000 if len(times) > 1 else 0
    return summarize_trades(trades, cfg.paper_start_try, max_dd, costs, days), trades


def summarize_trades(trades, start, max_dd, costs=0.0, days=0.0):
    per_pos = defaultdict(float)
    reasons = defaultdict(int)
    for tr in trades:
        if tr["islem"] != "AL":
            per_pos[tr["pozisyon_id"]] += tr["kar_zarar_try"]
        if tr["islem"] == "SAT":
            reasons[tr["sebep"].split(":")[0]] += 1
    results = list(per_pos.values())
    wins = [x for x in results if x > 0]
    losses = [x for x in results if x <= 0]
    total = sum(results)
    return {
        "islem_sayisi": len(results),
        "gunluk_islem": round(len(results) / days, 1) if days else None,
        "toplam_kar_zarar_try": round(total, 2),
        "komisyon_ve_kayma_try": round(costs, 2),
        "komisyon_oncesi_kar_try": round(total + costs, 2),
        "getiri_%": round(total / start * 100, 2),
        "kazanma_orani_%": round(len(wins) / len(results) * 100, 1) if results else 0.0,
        "ortalama_kazanc_try": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "ortalama_kayip_try": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "kar_faktoru": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "en_buyuk_dusus_try": round(max_dd, 2),
        "satis_sebepleri": dict(reasons),
    }


# Karşılaştırılan ayarlar. "eski" ilk sürümün davranışıdır (karşılaştırma için).
VARIANTS = [
    ("eski ayarlar", {"buy_score": 0.45, "reentry_cooldown_hours": 0, "max_daily_buys": 9999,
                      "signal_exit_rounds": 1, "signal_exit_only_loss": False, "min_edge_mult": 0}),
    ("yeni varsayılan", {}),
    ("kademeli satış kapalı", {"partial_take_r": 999}),
    ("hedef 3 kat", {"reward_risk": 3.0}),
    ("puan 0.7", {"buy_score": 0.7}),
    ("sinyal satışı kapalı", {"signal_exit_rounds": 10**9}),
    ("geniş stop (min %3)", {"min_stop_pct": 3.0}),
    ("günde 2 alım", {"max_daily_buys": 2}),
]


def print_compare(rows, first_days, last_days):
    print(f"\nAyar karşılaştırması (ilk {first_days} gün = ayar seçme, son {last_days} gün = sınama)")
    head = f"{'AYAR':<24}{'İŞLEM':>7}{'KÂR F.':>8}{'KÂR TL':>9}{'MALİYET':>9}  |{'İŞLEM':>7}{'KÂR F.':>8}{'KÂR TL':>9}{'MALİYET':>9}"
    print(head)
    print("-" * len(head))
    for name, a, b in rows:
        def cells(x):
            pf = x["kar_faktoru"] if x["kar_faktoru"] is not None else 0
            return f"{x['islem_sayisi']:>7}{pf:>8.2f}{x['toplam_kar_zarar_try']:>9.0f}{x['komisyon_ve_kayma_try']:>9.0f}"
        print(f"{name:<24}{cells(a)}  |{cells(b)}")
    good = [n for n, a, b in rows if (a["kar_faktoru"] or 0) >= 1.2 and (b["kar_faktoru"] or 0) >= 1.2]
    print()
    if good:
        print("İki dönemde de kâr faktörü 1.2'nin üstünde olan ayarlar: " + ", ".join(good))
    else:
        print("Hiçbir ayar iki dönemde de kâr faktörü 1.2'ye ulaşmadı. Bu kurallarla canlıya geçmeyin.")
    print("MALİYET = komisyon + kayma. KÂR F. 1'in altındaysa o ayar zarar ettirmiş demektir.")


def main():
    p = argparse.ArgumentParser(description="Geçmiş veriyle test")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--top", type=int, default=20, help="hacmi en yüksek kaç coin")
    p.add_argument("--symbols", default="", help="virgülle, örn. BTC_TRY,ETH_TRY")
    p.add_argument("--karsilastir", action="store_true", help="farklı ayarları yan yana dene")
    args = p.parse_args()

    cfg = Config.from_env()
    client = BinanceTR(base_url=cfg.base_url, market_url=cfg.market_url)
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        tr = {r["symbol"] for r in client.symbols() if r.get("quoteAsset") == "TRY" and r.get("type") in (1, None)}
        tickers = {t["symbol"]: t for t in client.tickers_24h()}
        symbols = sorted((s for s in tr if market_symbol(s) in tickers),
                         key=lambda s: float(tickers[market_symbol(s)]["quoteVolume"]), reverse=True)[: args.top]
    cache = cfg.data_dir / "history"
    history = {}
    for sym in symbols:
        print(f"İndiriliyor: {sym}")
        history[sym] = download(client, sym, args.days, cache)
    btc = history.get("BTC_TRY") or download(client, "BTC_TRY", args.days, cache)

    print("Sinyaller hesaplanıyor (birkaç dakika sürebilir)...")
    start_ms = int((time.time() - args.days * 86400) * 1000)
    pre = Precomputed(history, btc, start_ms)

    if args.karsilastir:
        first = args.days * 2 // 3
        split_ms = start_ms + first * 86_400_000
        rows = []
        for name, overrides in VARIANTS:
            v = dataclasses.replace(cfg, **overrides)
            a, _ = run_backtest(v, start_ms=start_ms, end_ms=split_ms, pre=pre)
            b, _ = run_backtest(v, start_ms=split_ms, pre=pre)
            rows.append((name, a, b))
            print(f"  denendi: {name}")
        print_compare(rows, first, args.days - first)
        return

    summary, trades = run_backtest(cfg, start_ms=start_ms, pre=pre)
    out = cfg.data_dir / "backtest_islemler.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(trades[0]) if trades else ["bos"])
        w.writeheader()
        w.writerows(trades)
    print(f"\nGeçmiş veri testi ({args.days} gün, {len(history)} coin)")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"İşlemler: {out}")
    print("Not: Saniyelik akış, emir defteri ve global kontroller geçmiş veride yok; sonuçlar yaklaşık değerdir.")


if __name__ == "__main__":
    main()
