"""Yavaş (swing) strateji: büyük trend yukarıyken saatlik geri çekilmeden dönüşte al.

Alım koşulları (hepsi, kapanmış mumlarla):
  1. Günlük kapanış 20 günlük ortalamanın üstünde (uzun vadeli trend yukarı).
  2. 4 saatlik kapanış 20'lik ortalamanın üstünde ve bu ortalama yükseliyor.
  3. Fiyat 4 saatlik ortalamadan çok uzaklaşmamış (zirveden alım yapılmaz).
  4. Son saatlerde fiyat saatlik ortalamaya kadar geri çekilmiş...
  5. ...ve son saatlik mum yeşil kapanıp ortalamanın üstüne dönmüş (dönüş teyidi).
  6. Saatlik RSI aşırı alımda değil, dönüş mumunda hacim zayıf değil ve alıcılar baskın.
Stop: geri çekilmenin dibinin biraz altı. Hedef: stop mesafesinin SWING_RR katı.
Satış: stop, hedef, iz süren stop, süre dolması veya 4 saatlik trendin bozulması.
"""
from dataclasses import dataclass
from statistics import mean

from analysis import atr_pct, ema


def _closed(klines):
    return klines[:-1] if len(klines) > 1 else klines


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for a, b in zip(closes[-period - 1:-1], closes[-period:]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


@dataclass
class SwingSetup:
    ok: bool
    reason: str
    stop_pct: float = 0.0
    trail_pct: float = 0.0
    entry_ref: float = 0.0       # dönüş mumunun kapanışı
    trend_broken: bool = False   # elde tutulan pozisyon için: 4s trend bozuldu mu


def swing_setup(cfg, k1h, k4h, k1d):
    h, f, d = _closed(k1h), _closed(k4h), _closed(k1d)
    if len(h) < 30 or len(f) < 25 or len(d) < 21:
        return SwingSetup(False, "yeterli geçmiş veri yok")

    c4 = [float(k[4]) for k in f]
    e4 = ema(c4[-60:], 20)
    e4_prev = ema(c4[-60:-5], 20)
    broken = c4[-1] < e4 * (1 - cfg.swing_trend_break_pct / 100)

    def no(reason):
        return SwingSetup(False, reason, trend_broken=broken)

    cd = [float(k[4]) for k in d]
    if cd[-1] <= ema(cd[-60:], 20):
        return no("günlük trend aşağı")
    if c4[-1] <= e4 or e4 <= e4_prev:
        return no("4 saatlik trend zayıf")
    ext = (c4[-1] / e4 - 1) * 100
    if ext > cfg.swing_max_ext_pct:
        return no(f"4 saatlik ortalamadan %{ext:.1f} uzakta")

    ch = [float(k[4]) for k in h]
    e1 = ema(ch[-60:], 20)
    last = h[-1]
    o, c = float(last[1]), float(last[4])
    n = cfg.swing_pullback_bars
    recent_lows = [float(k[3]) for k in h[-n - 1:-1]]
    if min(recent_lows) > e1 * 1.003:
        return no("saatlik geri çekilme yok")
    if not (c > o and c > e1):
        return no("dönüş teyidi yok")
    r = rsi(ch)
    if not (cfg.swing_rsi_min <= r <= cfg.swing_rsi_max):
        return no(f"saatlik RSI {r:.0f}")
    vols = [float(k[5]) for k in h]
    avg = mean(vols[-21:-1])
    if avg > 0 and vols[-1] < cfg.swing_min_rvol * avg:
        return no("dönüş mumunda hacim zayıf")
    if float(last[5]) > 0 and float(last[9]) / float(last[5]) < 0.5:
        return no("dönüş mumunda satıcılar baskın")

    swing_low = min(float(k[3]) for k in h[-n - 2:])
    stop_pct = (1 - swing_low * 0.997 / c) * 100
    if stop_pct > cfg.swing_max_stop_pct:
        return no(f"stop çok uzak (%{stop_pct:.1f})")
    stop_pct = max(stop_pct, cfg.swing_min_stop_pct)
    trail = max((atr_pct(k4h) or 0) * cfg.swing_trail_atr_mult, 1.0)
    return SwingSetup(True, "trend + geri çekilme + dönüş", round(stop_pct, 3), round(trail, 3), c, broken)
