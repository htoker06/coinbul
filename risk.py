"""Risk kuralları: pozisyon büyüklüğü, stoplar, molalar ve alım yasakları."""
from datetime import datetime, timedelta, timezone

from config import STABLECOINS

TR_TZ = timezone(timedelta(hours=3))  # Türkiye 2016'dan beri sabit UTC+3


def tr_now(ts):
    return datetime.fromtimestamp(ts, TR_TZ)


def in_window(hour, window):
    if not window:
        return False
    a, b = window
    return a <= hour < b if a <= b else (hour >= a or hour < b)


def entry_plan(cfg, atr_pct):
    """(2) Coinin oynaklığına göre stop, kâr al, iz süren stop mesafesi ve alım tutarı."""
    stop = min(max(cfg.atr_mult * (atr_pct or 0), cfg.min_stop_pct), cfg.max_stop_pct)
    size = cfg.risk_per_trade_try / (stop / 100)
    size = min(max(size, cfg.min_trade_try), cfg.max_trade_try)
    return {
        "stop_pct": round(stop, 3),
        "tp_pct": round(stop * cfg.reward_risk, 3),
        "trail_pct": round(max(cfg.trailing_atr_mult * (atr_pct or 0), 0.5), 3),
        "size_try": round(size, 2),
    }


def swing_plan(cfg, setup):
    """Swing: stop dipten gelir, tutar yine işlem başı riske göre hesaplanır."""
    stop = setup.stop_pct
    size = min(max(cfg.risk_per_trade_try / (stop / 100), cfg.min_trade_try), cfg.max_trade_try)
    return {"stop_pct": stop, "tp_pct": round(stop * cfg.reward_risk, 3),
            "trail_pct": setup.trail_pct, "size_try": round(size, 2)}


def round_trip_cost_pct(cfg):
    """Bir alım-satım turunun tahmini maliyeti (komisyon x2 + kayma), yüzde."""
    return 2 * cfg.fee_rate * 100 + cfg.est_slippage_pct


def edge_ok(cfg, plan):
    """(Komisyon kontrolü) Kâr hedefi maliyetin en az MIN_EDGE_MULT katı mı?"""
    return plan["tp_pct"] >= cfg.min_edge_mult * round_trip_cost_pct(cfg)


def new_state(cfg):
    return {"cash_try": cfg.paper_start_try, "positions": {},
            "day": "", "realized_today": 0.0, "week": "", "realized_week": 0.0, "realized_total": 0.0,
            "consecutive_losses": 0, "pause_until": 0, "cooldowns": {}, "streaks": {},
            "pump_watch": {}, "halted": "", "last_report_day": "",
            "buys_today": 0, "sell_streaks": {}}


class Risk:
    def __init__(self, cfg, state):
        self.cfg = cfg
        self.state = state
        for k, v in new_state(cfg).items():  # eski durum dosyalarına eksik alanları ekle
            state.setdefault(k, v)

    def roll_periods(self, now):
        t = tr_now(now)
        day = t.date().isoformat()
        week = "%d-W%02d" % t.isocalendar()[:2]
        if self.state["day"] != day:
            self.state["day"], self.state["realized_today"], self.state["buys_today"] = day, 0.0, 0
        if self.state["week"] != week:
            self.state["week"], self.state["realized_week"] = week, 0.0

    def record_close(self, symbol, pnl, now, final=True):
        """Satış sonrası kâr/zarar ve (3) mola sayaçları."""
        st = self.state
        st["realized_today"] += pnl
        st["realized_week"] += pnl
        st["realized_total"] += pnl
        if not final:
            return
        wait = self.cfg.reentry_cooldown_hours
        if pnl < 0:
            wait = max(wait, self.cfg.symbol_cooldown_hours)
        if wait > 0:
            st["cooldowns"][symbol] = max(st["cooldowns"].get(symbol, 0), now + wait * 3600)
        if pnl < 0:
            st["consecutive_losses"] += 1
            if st["consecutive_losses"] >= self.cfg.max_consecutive_losses:
                st["pause_until"] = now + self.cfg.loss_pause_minutes * 60
                st["consecutive_losses"] = 0
        else:
            st["consecutive_losses"] = 0

    def global_block(self, now, stop_file_exists=False, regime=None):
        """Hiçbir coinin alınmaması gereken durumlar. Engel yoksa boş metin döner."""
        c, st = self.cfg, self.state
        if st.get("halted"):
            return f"ajan durduruldu: {st['halted']}"
        if stop_file_exists:
            return "STOP dosyası var"
        if st["realized_today"] <= -c.max_daily_loss_try:
            return "günlük zarar sınırına ulaşıldı"
        if st["realized_week"] <= -c.max_weekly_loss_try:
            return "haftalık zarar sınırına ulaşıldı"
        if now < st["pause_until"]:
            return f"üst üste zarar molası ({tr_now(st['pause_until']):%H:%M}'e kadar)"
        if in_window(tr_now(now).hour, c.no_buy_window):
            return f"alım yapılmayan saatler ({c.no_buy_hours})"
        if regime is not None and not regime.ok:
            return regime.reason
        if len(st["positions"]) >= c.max_open_positions:
            return "en fazla pozisyon sayısında"
        if st["buys_today"] >= c.max_daily_buys:
            return f"günlük alım sınırı ({c.max_daily_buys})"
        return ""

    def record_buy(self):
        self.state["buys_today"] += 1

    def update_streaks(self, signals):
        """(11) Her coinin alım sinyalinin kaç tur üst üste sürdüğünü sayar."""
        streaks = self.state["streaks"]
        good = {s.symbol for s in signals if s.score >= self.cfg.buy_score}
        for sym in list(streaks):
            if sym not in good:
                del streaks[sym]
        for sym in good:
            streaks[sym] = streaks.get(sym, 0) + 1

    def update_sell_streaks(self, signals):
        """Her coinin satış sinyalinin kaç tur üst üste sürdüğünü sayar."""
        streaks = self.state["sell_streaks"]
        bad = {s.symbol for s in signals if s.label in ("SATIŞ BASKISI", "ŞÜPHELİ HACİM")}
        for sym in list(streaks):
            if sym not in bad:
                del streaks[sym]
        for sym in bad:
            streaks[sym] = streaks.get(sym, 0) + 1

    def signal_exit(self, symbol, in_loss):
        """'Sinyal döndü' satışı yapılmalı mı?"""
        if self.cfg.signal_exit_only_loss and not in_loss:
            return False
        return self.state["sell_streaks"].get(symbol, 0) >= self.cfg.signal_exit_rounds

    def symbol_block(self, s, info, now, swing=None):
        """Bu coinin alınmaması için sebep. Engel yoksa boş metin.
        swing: swing stratejisinde coinin SwingSetup'ı (puan/teyit yerine bu kullanılır)."""
        c, st = self.cfg, self.state
        base = (info or {}).get("base") or s.symbol.split("_")[0]
        if s.symbol in st["positions"]:
            return "zaten elde"
        if base in STABLECOINS:
            return "sabit değerli coin"
        if base in c.blacklist_set:
            return "kara listede"
        if st["cooldowns"].get(s.symbol, 0) > now:
            return "yakın zamanda zarar ettirdi, bekleme süresinde"
        if c.strategy == "swing":
            if not swing or not swing.ok:
                return f"swing kurulumu yok: {swing.reason if swing else 'veri yok'}"
            if s.price > swing.entry_ref * 1.01:
                return "fiyat dönüş mumundan çok uzaklaştı"
            if s.label == "ŞÜPHELİ HACİM" or s.fake_score > c.max_fake_score:
                return "sahte hacim"
        elif s.score < c.buy_score:
            return "puan düşük"
        elif s.label in ("ŞÜPHELİ HACİM", "SATIŞ BASKISI") or s.fake_score > c.max_fake_score:
            return "sahte hacim / satış baskısı"
        if s.spread_pct > c.max_spread_pct:
            return "makas geniş"
        if s.change_24h_pct > c.max_24h_change_pct:
            return "24 saatte çok yükselmiş"
        if c.strategy != "swing" and st["streaks"].get(s.symbol, 0) < c.confirm_rounds:
            return f"sinyal teyidi bekleniyor ({st['streaks'].get(s.symbol, 0)}/{c.confirm_rounds})"
        return ""

    def pump_block(self, s, now):
        """(8) Ani yükselişin peşinden koşma: geri çekilme ve yeniden yükseliş bekle."""
        c, watch = self.cfg, self.state["pump_watch"]
        w = watch.get(s.symbol)
        if w and now - w["ts"] > c.pump_watch_minutes * 60:
            del watch[s.symbol]
            w = None
        if s.change_5m_pct >= c.pump_5m_pct:
            if not w:
                watch[s.symbol] = {"peak": s.price, "ts": now}
            else:
                w["peak"] = max(w["peak"], s.price)
            return f"ani yükseliş (5dk %{s.change_5m_pct:.1f}), geri çekilme bekleniyor"
        if w:
            w["peak"] = max(w["peak"], s.price)
            pulled = (s.price / w["peak"] - 1) * 100 <= -c.pump_pullback_pct
            if not pulled:
                return "ani yükseliş sonrası geri çekilme bekleniyor"
            if s.change_1m_pct <= 0:
                return "geri çekildi, yeniden yükseliş bekleniyor"
            del watch[s.symbol]
        return ""
