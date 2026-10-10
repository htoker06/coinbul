"""Çoklu zaman dilimi, hacim, alım/satım baskısı ve sahte hacim analizi.

Buradaki fonksiyonlar ağa çıkmaz; sadece borsadan gelen ham listeleri işler.
"""
import math
from collections import Counter
from dataclasses import dataclass, field
from statistics import mean, pstdev

TF_WEIGHTS = {"1m": 0.5, "5m": 0.75, "15m": 1.0, "30m": 1.0, "1h": 1.5, "4h": 1.5, "1d": 1.0}


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def ema(values, period):
    k = 2 / (period + 1)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


@dataclass
class TFMetrics:
    change_pct: float      # son kapanmış mumun değişimi
    trend_up: bool         # kapanış EMA20 üstünde mi
    rvol: float            # son mum hacmi / önceki 20 mum ortalaması
    taker_buy_ratio: float  # son 3 mumda hacmin alıcı tarafından gelen oranı
    range_pct: float       # son mumun (yüksek-düşük)/açılış


def tf_metrics(klines):
    """klines: [openTime, open, high, low, close, volume, closeTime, quoteVol, trades, takerBase, ...]
    Son eleman henüz kapanmamış mumdur ve atılır."""
    closed = klines[:-1] if len(klines) > 1 else klines
    if len(closed) < 3:
        return None
    o = [float(k[1]) for k in closed]
    h = [float(k[2]) for k in closed]
    lo = [float(k[3]) for k in closed]
    c = [float(k[4]) for k in closed]
    v = [float(k[5]) for k in closed]
    tb = [float(k[9]) for k in closed]

    prev = v[-21:-1]
    avg_prev = mean(prev) if prev else 0
    rvol = v[-1] / avg_prev if avg_prev > 0 else 1.0
    vol3 = sum(v[-3:])
    taker = sum(tb[-3:]) / vol3 if vol3 > 0 else 0.5
    return TFMetrics(
        change_pct=(c[-1] / o[-1] - 1) * 100 if o[-1] else 0.0,
        trend_up=c[-1] > ema(c[-20:], 20),
        rvol=rvol,
        taker_buy_ratio=taker,
        range_pct=(h[-1] - lo[-1]) / o[-1] * 100 if o[-1] else 0.0,
    )


@dataclass
class BookMetrics:
    best_bid: float
    best_ask: float
    spread_pct: float
    bid_depth_try: float  # en iyi fiyatın %1 yakınındaki alış emirleri (TRY)
    ask_depth_try: float
    imbalance: float      # -1 (satıcı ağırlıklı) .. +1 (alıcı ağırlıklı)


def book_metrics(depth):
    bids = [(float(p), float(q)) for p, q, *_ in depth.get("bids", [])]
    asks = [(float(p), float(q)) for p, q, *_ in depth.get("asks", [])]
    if not bids or not asks:
        return None
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    bid_d = sum(p * q for p, q in bids if p >= bb * 0.99)
    ask_d = sum(p * q for p, q in asks if p <= ba * 1.01)
    tot = bid_d + ask_d
    return BookMetrics(bb, ba, (ba - bb) / mid * 100, bid_d, ask_d,
                       (bid_d - ask_d) / tot if tot else 0.0)


@dataclass
class TradeMetrics:
    """Saniyelik akış: son 60 saniyedeki işlemler."""
    trades_per_sec: float
    buy_ratio_60s: float
    quote_volume_60s: float


def trade_metrics(trades, now_ms=None):
    if not trades:
        return TradeMetrics(0.0, 0.5, 0.0)
    now_ms = now_ms or trades[-1]["time"]
    recent = [t for t in trades if t["time"] >= now_ms - 60_000]
    qv = [float(t["quoteQty"]) for t in recent]
    buy = sum(float(t["quoteQty"]) for t in recent if not t["isBuyerMaker"])
    total = sum(qv)
    return TradeMetrics(len(recent) / 60, buy / total if total else 0.5, total)


def fake_volume_score(trades, tf15, book, quote_volume_24h):
    """0 (temiz) .. 1 (büyük ihtimalle sahte/yıkama işlem). Sebepleri de döner."""
    score, reasons = 0.0, []

    if tf15 and tf15.rvol >= 3 and tf15.range_pct < 0.3:
        score += 0.35
        reasons.append(f"15dk hacim {tf15.rvol:.1f}x ama fiyat oynamıyor")

    if len(trades) >= 50:
        qtys = [float(t["qty"]) for t in trades]
        top_qty, top_n = Counter(round(q, 8) for q in qtys).most_common(1)[0]
        if top_n / len(qtys) >= 0.3:
            score += 0.25
            reasons.append(f"işlemlerin %{top_n / len(qtys) * 100:.0f}'i aynı miktar ({top_qty})")

        pairs = list(zip(trades, trades[1:]))
        mirror = sum(1 for a, b in pairs
                     if a["qty"] == b["qty"] and a["isBuyerMaker"] != b["isBuyerMaker"])
        if mirror / len(pairs) >= 0.2:
            score += 0.2
            reasons.append("aynı miktarda ardışık al-sat (kendi kendine işlem)")

        gaps = [b["time"] - a["time"] for a, b in pairs]
        g_mean = mean(gaps)
        if g_mean > 0 and pstdev(gaps) / g_mean < 0.3:
            score += 0.15
            reasons.append("işlemler saat gibi düzenli aralıklarla")

        top = sorted((float(t["quoteQty"]) for t in trades), reverse=True)
        k = max(1, len(top) // 20)
        if sum(top[:k]) / sum(top) >= 0.7:
            score += 0.1
            reasons.append("hacmin çoğu birkaç büyük işlemde")

    if book and quote_volume_24h > 0:
        depth = book.bid_depth_try + book.ask_depth_try
        if depth > 0 and (quote_volume_24h / 1440) / depth > 5:
            score += 0.2
            reasons.append("hacme göre emir defteri çok sığ")

    return min(score, 1.0), reasons


def atr_pct(klines, period=14):
    """Ortalama gerçek aralık (ATR), fiyatın yüzdesi olarak. Coinin ne kadar oynak olduğunu gösterir."""
    closed = klines[:-1] if len(klines) > 1 else klines
    if len(closed) < 2:
        return None
    trs = []
    for prev, k in zip(closed, closed[1:]):
        h, lo, pc = float(k[2]), float(k[3]), float(prev[4])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    last = float(closed[-1][4])
    return mean(trs[-period:]) / last * 100 if last else None


def recent_change_pct(klines, candles):
    """Son `candles` mumdaki toplam değişim (içinde bulunulan mum dahil)."""
    if len(klines) < candles + 1:
        return 0.0
    start = float(klines[-candles - 1][4])
    return (float(klines[-1][4]) / start - 1) * 100 if start else 0.0


def detect_spoof(prev_depth, depth, wall_mult=5.0, band_pct=2.0):
    """Önceki turda görünen büyük emir duvarı, fiyat oraya gelmeden kaybolduysa True."""
    if not prev_depth or not depth:
        return False
    for side in ("bids", "asks"):
        prev = [(float(p), float(q)) for p, q, *_ in prev_depth.get(side, [])]
        now = {float(p): float(q) for p, q, *_ in depth.get(side, [])}
        if len(prev) < 5 or not now:
            continue
        best = prev[0][0]
        sizes = sorted(p * q for p, q in prev)
        median = sizes[len(sizes) // 2]
        cur_best = max(now) if side == "bids" else min(now)
        for p, q in prev:
            if abs(p / best - 1) * 100 > band_pct or p * q < wall_mult * median:
                continue
            traded_through = cur_best < p if side == "bids" else cur_best > p
            if not traded_through and now.get(p, 0) * p < 0.2 * p * q:
                return True
    return False


def slippage_pct(depth, quote_amount):
    """`quote_amount` TRY'lik piyasa alımının en iyi satış fiyatına göre ne kadar pahalıya geleceği."""
    asks = [(float(p), float(q)) for p, q, *_ in (depth or {}).get("asks", [])]
    if not asks:
        return 99.0
    left, cost, qty = quote_amount, 0.0, 0.0
    for p, q in asks:
        take = min(left, p * q)
        cost += take
        qty += take / p
        left -= take
        if left <= 1e-9:
            break
    if left > 1e-9:
        return 99.0  # defterde yeterli emir yok
    return (cost / qty / asks[0][0] - 1) * 100


def limit_price_for(depth, quote_amount, max_slippage_pct):
    """Limit alım fiyatı: miktarı karşılayan en kötü fiyat, kayma sınırıyla kısıtlı."""
    asks = [(float(p), float(q)) for p, q, *_ in (depth or {}).get("asks", [])]
    if not asks:
        return None
    cap = asks[0][0] * (1 + max_slippage_pct / 100)
    left = quote_amount
    for p, q in asks:
        left -= p * q
        if left <= 0:
            return min(p, cap)
    return cap


@dataclass
class Regime:
    ok: bool
    reason: str


def btc_regime(k4h, k1d, k1h, max_drop_1h_pct):
    """(1) Bitcoin düşüş eğilimindeyse yeni alım yapılmaz."""
    m4, md = tf_metrics(k4h), tf_metrics(k1d)
    drop = recent_change_pct(k1h, 1)
    if m4 and md and not m4.trend_up and not md.trend_up:
        return Regime(False, "BTC 4s ve günlükte ortalamanın altında")
    if drop <= -max_drop_1h_pct:
        return Regime(False, f"BTC son 1 saatte %{drop:.1f}")
    return Regime(True, "BTC uygun")


def global_check(try_price, global_ticker, usdt_try, tr_rvol, global_rvol,
                 max_premium_pct, local_rvol_ratio):
    """(9) TRY fiyatı/hacmi global USDT paritesiyle tutarlı mı? (ok, açıklama, prim%)"""
    if not global_ticker or not usdt_try:
        return True, "globalde karşılığı yok", None
    g = float(global_ticker.get("lastPrice", 0)) * usdt_try
    if g <= 0:
        return True, "global fiyat yok", None
    premium = (try_price / g - 1) * 100
    if abs(premium) > max_premium_pct:
        return False, f"TRY fiyatı globalden %{premium:+.1f} farklı", premium
    if tr_rvol >= 3 and global_rvol is not None and tr_rvol > local_rvol_ratio * max(global_rvol, 1.0):
        return False, f"hacim sadece TR'de artıyor ({tr_rvol:.1f}x, global {global_rvol:.1f}x)", premium
    return True, f"global prim %{premium:+.2f}", premium


def resample(klines, minutes):
    """Kısa mumlardan uzun mum üretir (geçmiş veri testi için)."""
    ms = minutes * 60_000
    out = {}
    for k in klines:
        key = int(k[0]) // ms * ms
        o, h, lo, c, v, tb = (float(k[i]) for i in (1, 2, 3, 4, 5, 9))
        if key not in out:
            out[key] = [key, o, h, lo, c, v, key + ms - 1, float(k[7]), int(k[8]), tb, 0.0, 0]
        else:
            r = out[key]
            r[2], r[3], r[4] = max(r[2], h), min(r[3], lo), c
            r[5] += v
            r[7] += float(k[7])
            r[8] += int(k[8])
            r[9] += tb
    return [out[key] for key in sorted(out)]


@dataclass
class Signal:
    symbol: str
    score: float
    label: str
    fake_score: float
    price: float
    spread_pct: float
    change_24h_pct: float
    quote_volume_24h: float
    reasons: list = field(default_factory=list)
    tf: dict = field(default_factory=dict)
    atr_pct: float = 0.0       # 1 saatlik ATR yüzdesi
    rvol: float = 1.0
    change_5m_pct: float = 0.0  # son iki 5dk mumdaki değişim
    change_1m_pct: float = 0.0  # içinde bulunulan 1dk mum
    spoof: bool = False
    listing_days: int = 999    # günlük mum sayısı (yeni listelenen coinler için)
    swing: object = None       # swing stratejisinde SwingSetup


def evaluate(symbol, tfs, trades, depth, ticker, prev_depth=None):
    """tfs: {"15m": klines, ...}. Puan -1 (güçlü satış) .. +1 (güçlü alım).
    trades/depth boş olabilir (geçmiş veri testinde)."""
    m = {tf: tf_metrics(k) for tf, k in tfs.items()}
    m = {tf: x for tf, x in m.items() if x}
    book = book_metrics(depth) if depth else None
    flow = trade_metrics(trades)
    qv24 = float(ticker.get("quoteVolume", 0))
    fake, fake_reasons = fake_volume_score(trades, m.get("15m"), book, qv24)
    spoof = detect_spoof(prev_depth, depth)
    if spoof:
        fake = min(1.0, fake + 0.1)
        fake_reasons.append("emir defterinde kaybolan sahte emir duvarı")

    w_total = sum(TF_WEIGHTS.get(tf, 1) for tf in m) or 1
    momentum = sum(TF_WEIGHTS.get(tf, 1) * (1 if x.trend_up else -1) for tf, x in m.items()) / w_total

    short = [m[tf] for tf in ("15m", "1h") if tf in m]
    rvol = max((x.rvol for x in short), default=1.0)
    vol_score = clamp(math.log2(rvol) / 2) if rvol > 0 else -1
    taker = mean(x.taker_buy_ratio for x in short) if short else 0.5
    live_ratio = flow.buy_ratio_60s if trades else taker
    flow_score = clamp((0.7 * taker + 0.3 * live_ratio - 0.5) * 4)
    imbalance = book.imbalance if (book and not spoof) else 0.0

    score = (0.35 * momentum + 0.35 * flow_score
             + 0.2 * max(vol_score, 0) * (1 if flow_score >= 0 else -1) + 0.1 * imbalance)
    score *= 1 - fake

    chg15 = m["15m"].change_pct if "15m" in m else 0.0
    if fake >= 0.5:
        label = "ŞÜPHELİ HACİM"
    elif rvol >= 2 and flow_score > 0.2 and chg15 > 0:
        label = "ALIM BAŞLADI"
    elif rvol >= 2 and flow_score < -0.2:
        label = "SATIŞ BASKISI"
    elif score > 0.3:
        label = "YÜKSELİŞ EĞİLİMİ"
    elif score < -0.3:
        label = "DÜŞÜŞ EĞİLİMİ"
    else:
        label = "NÖTR"

    reasons = [f"trend {sum(x.trend_up for x in m.values())}/{len(m)} dilimde yukarı",
               f"hacim {rvol:.1f}x", f"alıcı oranı %{taker * 100:.0f}",
               f"son 60sn alıcı %{live_ratio * 100:.0f}"] + fake_reasons
    return Signal(
        symbol=symbol, score=round(score, 3), label=label, fake_score=round(fake, 2),
        price=book.best_ask if book else float(ticker.get("lastPrice", 0)),
        spread_pct=book.spread_pct if book else 99.0,
        change_24h_pct=float(ticker.get("priceChangePercent", 0)),
        quote_volume_24h=qv24, reasons=reasons,
        tf={tf: round(x.change_pct, 2) for tf, x in m.items()},
        atr_pct=(atr_pct(tfs["1h"]) or 0.0) if "1h" in tfs else 0.0,
        rvol=rvol,
        change_5m_pct=recent_change_pct(tfs["5m"], 2) if "5m" in tfs else 0.0,
        change_1m_pct=recent_change_pct(tfs["1m"], 1) if "1m" in tfs else 0.0,
        spoof=spoof,
    )
