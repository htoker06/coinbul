"""Binance TR TRY piyasası alım-satım ajanı.

Kullanım:
    python agent.py --scan       # sadece tara, rapor yaz, işlem yapma
    python agent.py --once       # bir tur tara + (sanal/gerçek) işlem yap
    python agent.py              # sürekli çalış (Ctrl+C veya STOP dosyası ile durur)
"""
import argparse
import csv
import json
import logging
import time
from datetime import datetime, timezone

from analysis import evaluate
from broker import LiveBroker, PaperBroker, parse_symbol_info, pct
from config import HERE, Config
from exchange import BinanceTR, ExchangeError, market_symbol

log = logging.getLogger("ajan")
STOP_FILE = HERE / "STOP"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Agent:
    def __init__(self, cfg, client=None):
        self.cfg = cfg
        self.client = client or BinanceTR(cfg.api_key, cfg.api_secret, cfg.base_url, cfg.market_url)
        self.broker = LiveBroker(self.client) if cfg.live else PaperBroker(cfg.fee_rate)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = cfg.data_dir / f"state_{cfg.mode}.json"
        self.journal_path = cfg.data_dir / f"islemler_{cfg.mode}.csv"
        self.report_path = cfg.data_dir / "tarama.csv"
        self.state = self._load_state()
        self.symbols = {}

    # ------------------------------------------------------------- durum
    def _load_state(self):
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"cash_try": self.cfg.paper_start_try, "positions": {},
                "day": "", "realized_today": 0.0, "realized_total": 0.0}

    def _save_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    def _journal(self, row):
        new = not self.journal_path.exists()
        with self.journal_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["zaman", "islem", "sembol", "miktar", "fiyat",
                                              "tutar_try", "kar_zarar_try", "sebep"])
            if new:
                w.writeheader()
            w.writerow(row)

    def _roll_day(self):
        today = datetime.now(timezone.utc).date().isoformat()
        if self.state["day"] != today:
            self.state["day"], self.state["realized_today"] = today, 0.0

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
        return rows[: self.cfg.max_symbols], tickers

    def analyze(self, sym, ticker):
        tfs = {tf: self.client.klines(sym, tf, 60) for tf in self.cfg.timeframes}
        trades = self.client.trades(sym, 500)
        depth = self.client.depth(sym, 100)
        return evaluate(sym, tfs, trades, depth, ticker)

    def scan(self):
        rows, tickers = self.universe()
        signals = []
        for sym, t in rows:
            try:
                signals.append(self.analyze(sym, t))
            except ExchangeError as exc:
                log.warning("%s atlandı: %s", sym, exc)
            time.sleep(0.1)  # istek sınırına takılmamak için
        signals.sort(key=lambda s: s.score, reverse=True)
        self._write_report(signals)
        return signals, tickers

    def _write_report(self, signals):
        with self.report_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["sembol", "puan", "durum", "sahte_hacim", "fiyat", "makas_%", "24s_%",
                        "24s_hacim_try", *self.cfg.timeframes, "aciklama"])
            for s in signals:
                w.writerow([s.symbol, s.score, s.label, s.fake_score, s.price, round(s.spread_pct, 3),
                            s.change_24h_pct, round(s.quote_volume_24h),
                            *[s.tf.get(tf, "") for tf in self.cfg.timeframes], "; ".join(s.reasons)])
        print(f"\n{'SEMBOL':<12}{'PUAN':>7}  {'DURUM':<18}{'SAHTE':>6}{'24s%':>8}")
        for s in signals[:15]:
            print(f"{s.symbol:<12}{s.score:>7.2f}  {s.label:<18}{s.fake_score:>6.2f}{s.change_24h_pct:>8.1f}")
        print(f"Tam rapor: {self.report_path}\n")

    # ------------------------------------------------------------- işlem
    def can_buy(self, s):
        c, st = self.cfg, self.state
        if STOP_FILE.exists():
            return False, "STOP dosyası var"
        if st["realized_today"] <= -c.max_daily_loss_try:
            return False, "günlük zarar sınırına ulaşıldı"
        if len(st["positions"]) >= c.max_open_positions:
            return False, "en fazla pozisyon sayısında"
        if s.symbol in st["positions"]:
            return False, "zaten elde"
        if s.score < c.buy_score:
            return False, "puan düşük"
        if s.label in ("ŞÜPHELİ HACİM", "SATIŞ BASKISI") or s.fake_score > c.max_fake_score:
            return False, "sahte hacim / satış baskısı"
        if s.spread_pct > c.max_spread_pct:
            return False, "makas geniş"
        if s.change_24h_pct > c.max_24h_change_pct:
            return False, "24 saatte çok yükselmiş (pump riski)"
        if not c.live and st["cash_try"] < c.trade_size_try:
            return False, "sanal bakiye yetersiz"
        info = self.symbols.get(s.symbol, {})
        if info.get("min_notional") and c.trade_size_try < info["min_notional"]:
            return False, "işlem tutarı borsanın alt sınırının altında"
        return True, ""

    def buy(self, s):
        info = self.symbols[s.symbol]
        qty, price, fee = self.broker.buy(s.symbol, self.cfg.trade_size_try, s.price, info)
        self.state["positions"][s.symbol] = {
            "qty": qty, "entry": price, "peak": price, "cost_try": self.cfg.trade_size_try,
            "opened": time.time(),
        }
        if not self.cfg.live:
            self.state["cash_try"] -= self.cfg.trade_size_try
        reason = f"{s.label}, puan {s.score}"
        log.info("ALIM %s %.8g @ %.6g (%s)", s.symbol, qty, price, reason)
        self._journal({"zaman": now_iso(), "islem": "AL", "sembol": s.symbol, "miktar": qty,
                       "fiyat": price, "tutar_try": self.cfg.trade_size_try, "kar_zarar_try": "",
                       "sebep": reason})

    def exit_reason(self, pos, bid, signal):
        c = self.cfg
        change = pct(bid, pos["entry"])
        if change <= -c.stop_loss_pct:
            return f"zarar durdur ({change:.1f}%)"
        if change >= c.take_profit_pct:
            return f"kâr al ({change:.1f}%)"
        if pct(pos["peak"], pos["entry"]) >= c.trailing_start_pct and pct(bid, pos["peak"]) <= -c.trailing_pct:
            return f"iz süren stop ({change:.1f}%)"
        if (time.time() - pos["opened"]) / 3600 >= c.max_hold_hours:
            return "süre doldu"
        if signal and signal.label in ("SATIŞ BASKISI", "ŞÜPHELİ HACİM"):
            return f"sinyal döndü: {signal.label}"
        if STOP_FILE.exists() and (STOP_FILE.read_text(encoding="utf-8").strip().upper() == "SAT"):
            return "STOP dosyası: hepsini sat"
        return None

    def manage_positions(self, signals):
        by_sym = {s.symbol: s for s in signals}
        for sym, pos in list(self.state["positions"].items()):
            try:
                depth = self.client.depth(sym, 5)
                bid = float(depth["bids"][0][0])
            except (ExchangeError, KeyError, IndexError) as exc:
                log.warning("%s fiyat alınamadı: %s", sym, exc)
                continue
            pos["peak"] = max(pos["peak"], bid)
            reason = self.exit_reason(pos, bid, by_sym.get(sym))
            if not reason:
                continue
            try:
                qty, price, fee = self.broker.sell(sym, pos["qty"], bid, self.symbols.get(sym, {}))
            except (ExchangeError, RuntimeError) as exc:
                log.error("%s satılamadı: %s", sym, exc)
                continue
            proceeds = qty * price - fee
            pnl = proceeds - pos["cost_try"]
            self.state["realized_today"] += pnl
            self.state["realized_total"] += pnl
            if not self.cfg.live:
                self.state["cash_try"] += proceeds
            del self.state["positions"][sym]
            log.info("SATIŞ %s @ %.6g kâr/zarar %.2f TRY (%s)", sym, price, pnl, reason)
            self._journal({"zaman": now_iso(), "islem": "SAT", "sembol": sym, "miktar": qty,
                           "fiyat": price, "tutar_try": round(proceeds, 2),
                           "kar_zarar_try": round(pnl, 2), "sebep": reason})

    def step(self, trade=True):
        self._roll_day()
        signals, _ = self.scan()
        if trade:
            self.manage_positions(signals)
            for s in signals:
                ok, why = self.can_buy(s)
                if ok:
                    try:
                        self.buy(s)
                    except (ExchangeError, RuntimeError) as exc:
                        log.error("%s alınamadı: %s", s.symbol, exc)
                elif s.score >= self.cfg.buy_score:
                    log.info("%s alınmadı: %s", s.symbol, why)
            self._save_state()
            st = self.state
            log.info("Açık pozisyon: %d | bugün: %.2f TRY | toplam: %.2f TRY%s",
                     len(st["positions"]), st["realized_today"], st["realized_total"],
                     "" if self.cfg.live else f" | sanal nakit: {st['cash_try']:.2f} TRY")
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
    if cfg.live:
        log.warning("CANLI MOD: gerçek parayla işlem yapılacak. İşlem başı %.0f TRY, en fazla %d pozisyon.",
                    cfg.trade_size_try, cfg.max_open_positions)
    else:
        log.info("SANAL MOD: gerçek emir gönderilmez.")

    agent = Agent(cfg)
    agent.load_symbols()
    while True:
        try:
            agent.step(trade=not args.scan)
        except ExchangeError as exc:
            log.error("Tur başarısız: %s", exc)
        if args.scan or args.once:
            break
        if STOP_FILE.exists():
            log.info("STOP dosyası bulundu, ajan duruyor.")
            break
        time.sleep(cfg.loop_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Durduruldu.")
