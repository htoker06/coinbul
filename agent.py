"""Binance TR TRY piyasası alım-satım ajanı.

Kullanım:
    python agent.py --scan       # sadece tara, rapor yaz, işlem yapma
    python agent.py --once       # bir tur tara + (sanal/gerçek) işlem yap
    python agent.py              # sürekli çalış (Ctrl+C, STOP dosyası veya Telegram /durdur)
"""
import argparse
import csv
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from analysis import Regime, btc_regime, evaluate, global_check, slippage_pct, tf_metrics
from broker import LiveBroker, PaperBroker, parse_symbol_info, pct
from config import HERE, Config
from exchange import BinanceGlobal, BinanceTR, ExchangeError, market_symbol
from notifier import Notifier
from report import format_summary, load, summarize
from risk import Risk, edge_ok, entry_plan, new_state, round_trip_cost_pct, tr_now

log = logging.getLogger("ajan")
STOP_FILE = HERE / "STOP"
JOURNAL_FIELDS = ["zaman", "pozisyon_id", "islem", "sembol", "miktar", "fiyat", "tutar_try",
                  "kar_zarar_try", "komisyon_try", "sinyal", "sebep"]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Agent:
    def __init__(self, cfg, client=None, global_client=None, notifier=None):
        self.cfg = cfg
        self.client = client or BinanceTR(cfg.api_key, cfg.api_secret, cfg.base_url, cfg.market_url)
        self.global_client = global_client or BinanceGlobal(cfg.global_url)
        self.notifier = notifier or Notifier(cfg.telegram_token, cfg.telegram_chat_id)
        self.broker = LiveBroker(cfg, self.client) if cfg.live else PaperBroker(cfg)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = cfg.data_dir / f"state_{cfg.mode}.json"
        self.journal_path = cfg.data_dir / f"islemler_{cfg.mode}.csv"
        self.report_path = cfg.data_dir / "tarama.csv"
        self.state = self._load_state()
        self.risk = Risk(cfg, self.state)
        self.symbols = {}
        self.prev_depth = {}
        self.depth_cache = {}
        self.failures = 0
        self.last_block = ""
        self.pump_reasons = {}

    # ------------------------------------------------------------- durum
    def _load_state(self):
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return new_state(self.cfg)

    def _save_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    def _journal(self, **row):
        new = not self.journal_path.exists()
        with self.journal_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
            if new:
                w.writeheader()
            w.writerow({"zaman": now_iso(), **row})

    def notify(self, text):
        log.info(text.replace("\n", " | "))
        self.notifier.send(text)

    # ------------------------------------------------------------- tarama
    def load_symbols(self):
        for raw in self.client.symbols():
            if raw.get("quoteAsset") != "TRY" or raw.get("type") not in (1, None):
                continue  # type 1 = ana piyasa (api.binance.me verisi)
            if raw.get("spotTradingEnable", 1) in (0, False):
                continue
            self.symbols[raw["symbol"]] = parse_symbol_info(raw)
        log.info("%d TRY paritesi bulundu", len(self.symbols))

    def universe(self):
        tickers = {t["symbol"]: t for t in self.client.tickers_24h()}
        rows = []
        for sym in self.symbols:
            t = tickers.get(market_symbol(sym))
            if t and float(t.get("quoteVolume", 0)) >= self.cfg.min_quote_volume_try:
                rows.append((sym, t))
        rows.sort(key=lambda r: float(r[1]["quoteVolume"]), reverse=True)
        return rows[: self.cfg.max_symbols]

    def analyze(self, sym, ticker):
        tfs = {tf: self.client.klines(sym, tf, 60) for tf in self.cfg.timeframes}
        trades = self.client.trades(sym, 500)
        depth = self.client.depth(sym, 100)
        sig = evaluate(sym, tfs, trades, depth, ticker, prev_depth=self.prev_depth.get(sym))
        self.prev_depth[sym] = depth
        self.depth_cache[sym] = depth
        # (7) yeni listelenen coin: günlük mum sayısı az
        if "1d" in tfs:
            sig.listing_days = len(tfs["1d"])
        return sig

    def scan(self):
        self.depth_cache = {}
        signals = []
        rows = self.universe()
        log.info("Tarama başladı: hacmi yeterli %d coin inceleniyor...", len(rows))
        started = time.time()
        for n, (sym, t) in enumerate(rows, 1):
            try:
                signals.append(self.analyze(sym, t))
            except ExchangeError as exc:
                log.warning("%s atlandı: %s", sym, exc)
            if n % 10 == 0 and n < len(rows):
                log.info("  %d/%d coin incelendi", n, len(rows))
            time.sleep(0.1)  # istek sınırına takılmamak için
        log.info("Tarama bitti (%.0f saniye)", time.time() - started)
        signals.sort(key=lambda s: s.score, reverse=True)
        self._write_report(signals)
        return signals

    def _write_report(self, signals):
        with self.report_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["sembol", "puan", "durum", "sahte_hacim", "fiyat", "makas_%", "24s_%",
                        "24s_hacim_try", "atr_1s_%", *self.cfg.timeframes, "aciklama"])
            for s in signals:
                w.writerow([s.symbol, s.score, s.label, s.fake_score, s.price, round(s.spread_pct, 3),
                            s.change_24h_pct, round(s.quote_volume_24h), round(s.atr_pct, 2),
                            *[s.tf.get(tf, "") for tf in self.cfg.timeframes], "; ".join(s.reasons)])
        print(f"\n{'SEMBOL':<12}{'PUAN':>7}  {'DURUM':<18}{'SAHTE':>6}{'24s%':>8}")
        for s in signals[:15]:
            print(f"{s.symbol:<12}{s.score:>7.2f}  {s.label:<18}{s.fake_score:>6.2f}{s.change_24h_pct:>8.1f}")
        print(f"Tam rapor: {self.report_path}\n")

    def market_regime(self):
        """(1) Bitcoin filtresi."""
        if not self.cfg.btc_filter:
            return None
        try:
            k = {tf: self.client.klines("BTC_TRY", tf, 60) for tf in ("1h", "4h", "1d")}
        except ExchangeError as exc:
            log.warning("BTC verisi alınamadı, güvenlik için alım yapılmayacak: %s", exc)
            return Regime(False, "BTC verisi alınamadı")
        return btc_regime(k["4h"], k["1d"], k["1h"], self.cfg.btc_max_drop_1h_pct)

    def global_data(self):
        """(9) Global Binance fiyatları. Erişilemezse kontrol atlanır (uyarı yazılır)."""
        if not self.cfg.global_check:
            return None
        try:
            tickers = {t["symbol"]: t for t in self.global_client.tickers_24h()}
        except ExchangeError as exc:
            log.warning("Global Binance verisi alınamadı, global kontrol atlanıyor: %s", exc)
            return None
        usdt = tickers.get("USDTTRY")
        return {"tickers": tickers, "usdt_try": float(usdt["lastPrice"]) if usdt else None}

    def global_ok(self, s, gdata):
        if not gdata:
            return True, ""
        base = self.symbols.get(s.symbol, {}).get("base") or s.symbol.split("_")[0]
        gsym = base + "USDT"
        gt = gdata["tickers"].get(gsym)
        g_rvol = None
        if gt:
            try:
                ms = [tf_metrics(self.global_client.klines(gsym, tf, 30)) for tf in ("15m", "1h")]
                g_rvol = max((m.rvol for m in ms if m), default=None)
            except ExchangeError:
                pass
        ok, why, _ = global_check(s.price, gt, gdata["usdt_try"], s.rvol, g_rvol,
                                  self.cfg.max_premium_pct, self.cfg.local_rvol_ratio)
        return ok, why

    # ------------------------------------------------------------- alım
    def try_buy(self, s, gdata, now):
        c = self.cfg
        info = self.symbols.get(s.symbol, {})
        why = self.risk.symbol_block(s, info, now)
        if not why and s.listing_days < c.min_listing_days:
            why = f"yeni listelenmiş ({s.listing_days} gün)"
        if not why:
            why = self.pump_reasons.get(s.symbol, "")
        if why:
            return why
        depth = self.depth_cache.get(s.symbol) or self.client.depth(s.symbol, 100)
        plan = entry_plan(c, s.atr_pct)
        if not edge_ok(c, plan):
            return (f"kâr hedefi (%{plan['tp_pct']}) komisyon ve kaymaya göre çok küçük "
                    f"(maliyet %{round_trip_cost_pct(c):.2f})")
        size = plan["size_try"]
        if not c.live and self.state["cash_try"] < size:
            return "sanal bakiye yetersiz"
        if info.get("min_notional") and size < info["min_notional"]:
            return "tutar borsanın alt sınırının altında"
        slip = slippage_pct(depth, size)
        if slip > c.max_slippage_pct:  # (5)
            return f"fiyat kayması yüksek (%{slip:.2f})"
        ok, gwhy = self.global_ok(s, gdata)  # (9)
        if not ok:
            return gwhy

        fill = self.broker.buy(s.symbol, size, depth, info)
        entry = fill["price"]
        pos = {
            "id": uuid.uuid4().hex[:8], "qty": fill["qty"], "entry": entry, "cost_try": fill["try"],
            "opened": now, "peak": entry, "label": s.label, "partial_done": False,
            "stop_price": entry * (1 - plan["stop_pct"] / 100), "stop_order_id": None, **plan,
            "baseline": fill.get("baseline", 0.0),  # alımdan önce hesapta zaten olan miktar
        }
        self.state["positions"][s.symbol] = pos
        self.risk.record_buy()
        if not c.live:
            self.state["cash_try"] -= fill["try"]
        self._sync_exchange_stop(s.symbol, pos, force=True)
        reason = f"{s.label}, puan {s.score}, stop %{plan['stop_pct']}, hedef %{plan['tp_pct']}"
        self._journal(pozisyon_id=pos["id"], islem="AL", sembol=s.symbol, miktar=fill["qty"],
                      fiyat=entry, tutar_try=round(fill["try"], 2), kar_zarar_try="",
                      komisyon_try=round(fill["try"] * c.fee_rate, 4), sinyal=s.label, sebep=reason)
        self.notify(f"🟢 ALIM {s.symbol}\n{fill['qty']:.8g} adet @ {entry:.8g}\n"
                    f"Tutar: {fill['try']:.2f} TRY\n{reason}")
        return ""

    # ------------------------------------------------------------- pozisyon yönetimi
    def _sync_exchange_stop(self, sym, pos, force=False):
        """(6) Borsadaki stop emrini güncel stop fiyatına taşır (sadece canlı mod)."""
        if not self.broker.live or not self.cfg.exchange_stop:
            return
        last = pos.get("stop_order_price") or 0
        if not force and pos.get("stop_order_id") and last and pos["stop_price"] < last * 1.003:
            return  # %0,3'ten küçük değişimde emir yenilenmez
        self.broker.cancel_stop(pos.get("stop_order_id"))
        try:
            pos["stop_order_id"] = self.broker.place_stop(sym, pos["qty"], pos["stop_price"],
                                                          self.symbols.get(sym, {}))
            pos["stop_order_price"] = pos["stop_price"]
        except (ExchangeError, RuntimeError) as exc:
            pos["stop_order_id"] = None
            log.error("%s borsaya stop emri yazılamadı, ajan stopu kendisi izleyecek: %s", sym, exc)

    def _sell(self, sym, pos, qty, depth, reason, final):
        self.broker.cancel_stop(pos.get("stop_order_id"))
        pos["stop_order_id"] = None
        fill = self.broker.sell(sym, qty, depth, self.symbols.get(sym, {}))
        share = pos["cost_try"] * fill["qty"] / pos["qty"]
        pnl = fill["try"] - share
        pos["qty"] -= fill["qty"]
        pos["cost_try"] -= share
        final = final or pos["qty"] <= 1e-12
        self.risk.record_close(sym, pnl, time.time(), final=final)
        if not self.cfg.live:
            self.state["cash_try"] += fill["try"]
        if final:
            del self.state["positions"][sym]
        else:
            self._sync_exchange_stop(sym, pos, force=True)
        self._journal(pozisyon_id=pos["id"], islem="SAT" if final else "KISMİ SAT", sembol=sym,
                      miktar=fill["qty"], fiyat=fill["price"], tutar_try=round(fill["try"], 2),
                      kar_zarar_try=round(pnl, 2), komisyon_try=round(fill["try"] * self.cfg.fee_rate, 4),
                      sinyal=pos.get("label", ""), sebep=reason)
        icon = "✅" if pnl > 0 else "🔴"
        self.notify(f"{icon} {'SATIŞ' if final else 'KISMİ SATIŞ'} {sym} @ {fill['price']:.8g}\n"
                    f"Kâr/zarar: {pnl:+.2f} TRY\nSebep: {reason}")

    def exit_reason(self, pos, bid, signal, sell_all):
        c = self.cfg
        if sell_all:
            return "hepsini sat komutu"
        change = pct(bid, pos["entry"])
        if bid <= pos["stop_price"]:
            kind = "zarar durdur" if pos["stop_price"] < pos["entry"] else "iz süren stop"
            return f"{kind} ({change:+.1f}%)"
        if change >= pos["tp_pct"]:
            return f"kâr al ({change:+.1f}%)"
        if (time.time() - pos["opened"]) / 3600 >= c.max_hold_hours:
            return f"süre doldu ({change:+.1f}%)"
        if signal and self.risk.signal_exit(signal.symbol, bid < pos["entry"]):
            return f"sinyal döndü: {signal.label} ({change:+.1f}%)"
        return None

    def _update_stops(self, pos, bid):
        c = self.cfg
        pos["peak"] = max(pos["peak"], bid)
        if pct(pos["peak"], pos["entry"]) >= c.trailing_start_r * pos["stop_pct"]:
            pos["stop_price"] = max(pos["stop_price"], pos["peak"] * (1 - pos["trail_pct"] / 100))

    def reconcile(self, sym, pos):
        """(15) Canlı modda: borsadaki bakiye ajanın kaydıyla uyuşuyor mu?
        Borsa stopu gerçekleştiyse pozisyonu kapatır. Döner: True = pozisyon kapandı/sorunlu."""
        if not self.broker.live:
            return False
        held = self.broker.holdings(self.symbols.get(sym, {}).get("base") or sym.split("_")[0])
        if held is not None:
            held -= pos.get("baseline", 0.0)
        if held is None or held >= pos["qty"] * 0.98:
            return False
        if held <= pos["qty"] * 0.05 and pos.get("stop_order_id"):
            proceeds = pos["qty"] * pos["stop_order_price"] * (1 - self.cfg.fee_rate)
            pnl = proceeds - pos["cost_try"]
            self.risk.record_close(sym, pnl, time.time(), final=True)
            del self.state["positions"][sym]
            self._journal(pozisyon_id=pos["id"], islem="SAT", sembol=sym, miktar=pos["qty"],
                          fiyat=pos["stop_order_price"], tutar_try=round(proceeds, 2),
                          kar_zarar_try=round(pnl, 2), komisyon_try=round(proceeds * self.cfg.fee_rate, 4),
                          sinyal=pos.get("label", ""), sebep="borsadaki stop emri gerçekleşti (tahmini fiyat)")
            self.notify(f"🔴 Borsadaki stop gerçekleşti: {sym}\nTahmini kâr/zarar: {pnl:+.2f} TRY")
            return True
        self.state["halted"] = f"{sym} bakiyesi kayıtla uyuşmuyor (borsa {held:.8g}, kayıt {pos['qty']:.8g})"
        self.notify(f"⚠️ {self.state['halted']}. Yeni alımlar durduruldu, lütfen hesabı kontrol edin. "
                    "Sorun çözülünce Telegram'dan /devam yazın.")
        return True

    def manage_positions(self, signals):
        by_sym = {s.symbol: s for s in signals}
        sell_all = self.state.pop("sell_all", False) or (
            STOP_FILE.exists() and STOP_FILE.read_text(encoding="utf-8").strip().upper() == "SAT")
        for sym, pos in list(self.state["positions"].items()):
            try:
                if self.reconcile(sym, pos):
                    continue
                depth = self.depth_cache.get(sym) or self.client.depth(sym, 100)
                bid = float(depth["bids"][0][0])
            except (ExchangeError, KeyError, IndexError) as exc:
                log.warning("%s fiyat alınamadı: %s", sym, exc)
                continue
            self._update_stops(pos, bid)
            try:
                reason = self.exit_reason(pos, bid, by_sym.get(sym), sell_all)
                if reason:
                    self._sell(sym, pos, pos["qty"], depth, reason, final=True)
                    continue
                # (16) kademeli satış: kâr stop mesafesine ulaşınca bir kısmını sat, stopu girişe çek
                if not pos["partial_done"] and pct(bid, pos["entry"]) >= self.cfg.partial_take_r * pos["stop_pct"]:
                    pos["partial_done"] = True
                    pos["stop_price"] = max(pos["stop_price"], pos["entry"] * (1 + 2 * self.cfg.fee_rate))
                    self._sell(sym, pos, pos["qty"] * self.cfg.partial_fraction, depth,
                               f"kademeli kâr al ({pct(bid, pos['entry']):+.1f}%)", final=False)
                    continue
            except (ExchangeError, RuntimeError) as exc:
                log.error("%s satılamadı: %s", sym, exc)
                self.notify(f"⚠️ {sym} satılamadı: {exc}")
            self._sync_exchange_stop(sym, pos)

    # ------------------------------------------------------------- Telegram / rapor
    def status_text(self):
        st = self.state
        lines = [f"Mod: {'CANLI' if self.cfg.live else 'SANAL'}"
                 + (f" | DURDURULDU: {st['halted']}" if st.get("halted") else ""),
                 f"Bugün: {st['realized_today']:+.2f} TRY | Hafta: {st['realized_week']:+.2f} | "
                 f"Toplam: {st['realized_total']:+.2f}"]
        if not self.cfg.live:
            lines.append(f"Sanal nakit: {st['cash_try']:.2f} TRY")
        for sym, p in st["positions"].items():
            lines.append(f"• {sym}: {p['qty']:.8g} @ {p['entry']:.8g}, stop {p['stop_price']:.8g}")
        if not st["positions"]:
            lines.append("Açık pozisyon yok")
        return "\n".join(lines)

    def handle_commands(self):
        for cmd in self.notifier.commands():
            if cmd == "/durum":
                self.notifier.send(self.status_text())
            elif cmd == "/durdur":
                self.state["halted"] = "Telegram /durdur"
                self.notifier.send("⏸ Yeni alımlar durduruldu. Pozisyonlar izlenmeye devam ediyor. /devam ile açılır.")
            elif cmd == "/devam":
                self.state["halted"] = ""
                self.notifier.send("▶️ Alımlar yeniden açıldı.")
            elif cmd == "/hepsinisat":
                self.state["sell_all"] = True
                self.state["halted"] = "Telegram /hepsinisat"
                self.notifier.send("🛑 Bütün pozisyonlar bir sonraki turda satılacak, alımlar durduruldu.")
            else:
                self.notifier.send("Komutlar: /durum /durdur /devam /hepsinisat")
            self._save_state()

    def daily_report(self, now):
        t = tr_now(now)
        day = t.date().isoformat()
        if t.hour < self.cfg.daily_report_hour or self.state.get("last_report_day") == day:
            return
        self.state["last_report_day"] = day
        rows = load(self.journal_path)
        self.notifier.send(format_summary(f"Günlük özet {day}", summarize(rows, day))
                           + "\n\n" + self.status_text())

    # ------------------------------------------------------------- tur
    def step(self, trade=True):
        now = time.time()
        self.risk.roll_periods(now)
        self.handle_commands()
        regime = self.market_regime() if trade else None
        signals = self.scan()
        self.risk.update_streaks(signals)
        self.risk.update_sell_streaks(signals)
        if not trade:
            return signals
        self.pump_reasons = {s.symbol: self.risk.pump_block(s, now) for s in signals}
        self.manage_positions(signals)

        block = self.risk.global_block(now, STOP_FILE.exists(), regime)
        if block:
            if block != self.last_block:
                log.info("Yeni alım yok: %s", block)
            self.last_block = block
        else:
            self.last_block = ""
            gdata = self.global_data()
            for s in signals:
                if len(self.state["positions"]) >= self.cfg.max_open_positions:
                    break
                try:
                    why = self.try_buy(s, gdata, now)
                except (ExchangeError, RuntimeError) as exc:
                    why = f"alım hatası: {exc}"
                    log.error("%s alınamadı: %s", s.symbol, exc)
                if why and s.score >= self.cfg.buy_score:
                    log.info("%s alınmadı: %s", s.symbol, why)
        self.daily_report(now)
        self._save_state()
        log.info(self.status_text().replace("\n", " | "))
        return signals


def main():
    p = argparse.ArgumentParser(description="Binance TR TRY ajanı")
    p.add_argument("--scan", action="store_true", help="sadece tara, işlem yapma")
    p.add_argument("--once", action="store_true", help="tek tur çalış")
    args = p.parse_args()

    cfg = Config.from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(cfg.data_dir / "ajan.log", encoding="utf-8")])
    agent = Agent(cfg)
    if cfg.live:
        agent.notify(f"⚠️ Ajan CANLI modda başladı. İşlem başı risk {cfg.risk_per_trade_try:.0f} TRY, "
                     f"en fazla {cfg.max_open_positions} pozisyon.")
    else:
        agent.notify("Ajan SANAL modda başladı (gerçek emir gönderilmez).")
    agent.load_symbols()
    agent.notifier.commands()  # eski Telegram mesajlarını atla

    while True:
        try:
            agent.step(trade=not args.scan)
            agent.failures = 0
        except Exception as exc:  # (15) sağlık kontrolü
            agent.failures += 1
            log.exception("Tur başarısız (%d): %s", agent.failures, exc)
            if agent.failures == 3:
                agent.notify(f"⚠️ Ajan 3 turdur çalışamıyor: {exc}")
            if agent.failures >= 10:
                agent.notify("🛑 Ajan 10 tur üst üste hata verdiği için kapandı. Açık pozisyonları kontrol edin.")
                raise
        if args.scan or args.once:
            break
        if STOP_FILE.exists():
            agent.notify("STOP dosyası bulundu, ajan duruyor.")
            break
        end = time.time() + cfg.loop_seconds
        while time.time() < end:  # beklerken Telegram komutlarını dinle
            agent.handle_commands()
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Durduruldu.")
