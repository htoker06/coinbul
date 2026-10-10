"""(12) Geçmiş veriyle test: ajanın kurallarını geçmiş mumlar üzerinde dener.

Kullanım:
    python backtest.py --days 90 --top 20
    python backtest.py --days 180 --symbols BTC_TRY,ETH_TRY,AVAX_TRY

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
import json
import time
from collections import defaultdict

from analysis import btc_regime, evaluate, resample
from config import Config
from exchange import BinanceTR, market_symbol
from risk import Risk, entry_plan, new_state

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


def run_backtest(cfg, history, btc=None, start_ms=None):
    """history: {sembol: 15dk mumlar}. Döner: (özet, işlemler)."""
    series = {s: Series(c) for s, c in history.items() if len(c) > 200}
    btc_s = Series(btc) if btc else None
    state = new_state(cfg)
    risk = Risk(cfg, state)
    positions, trades = {}, []
    equity = peak_equity = cfg.paper_start_try
    max_dd = 0.0

    times = sorted({k[0] + STEP_MS for s in series.values() for k in s.tf["15m"]})
    if start_ms:
        times = [t for t in times if t >= start_ms]

    def close(sym, pos, price, reason, t, frac=1.0):
        nonlocal equity
        qty = pos["qty"] * frac
        proceeds = qty * price * (1 - cfg.fee_rate)
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
        signals = []
        for sym, s in series.items():
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
            signals.append(sig)
            pos = positions.get(sym)
            if pos and sig.label in ("SATIŞ BASKISI", "ŞÜPHELİ HACİM"):
                close(sym, pos, day[-1][4], f"sinyal döndü: {sig.label}", t)
        signals.sort(key=lambda x: x.score, reverse=True)
        risk.update_streaks(signals)
        state["positions"] = {k: True for k in positions}

        # 3) Alımlar: bir sonraki mumun açılışından
        regime = None
        if btc_s and cfg.btc_filter:
            regime = btc_regime(btc_s.window("4h", t), btc_s.window("1d", t), btc_s.window("1h", t),
                                cfg.btc_max_drop_1h_pct)
        if not risk.global_block(now, False, regime):
            for sig in signals:
                if len(positions) >= cfg.max_open_positions:
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
                if state["cash_try"] < plan["size_try"]:
                    break
                entry = s.tf["15m"][i + 1][1] * (1 + ENTRY_SLIPPAGE)
                qty = plan["size_try"] / (1 + cfg.fee_rate) / entry
                state["cash_try"] -= plan["size_try"]
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
    for sym, pos in list(positions.items()):
        last = series[sym].tf["15m"][-1]
        close(sym, pos, last[4], "test sonu", last[0] + STEP_MS)

    return summarize_trades(trades, cfg.paper_start_try, max_dd), trades


def summarize_trades(trades, start, max_dd):
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
        "toplam_kar_zarar_try": round(total, 2),
        "getiri_%": round(total / start * 100, 2),
        "kazanma_orani_%": round(len(wins) / len(results) * 100, 1) if results else 0.0,
        "ortalama_kazanc_try": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "ortalama_kayip_try": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "kar_faktoru": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "en_buyuk_dusus_try": round(max_dd, 2),
        "satis_sebepleri": dict(reasons),
    }


def main():
    p = argparse.ArgumentParser(description="Geçmiş veriyle test")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--top", type=int, default=20, help="hacmi en yüksek kaç coin")
    p.add_argument("--symbols", default="", help="virgülle, örn. BTC_TRY,ETH_TRY")
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

    start_ms = int((time.time() - args.days * 86400) * 1000)
    summary, trades = run_backtest(cfg, history, btc, start_ms)

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
