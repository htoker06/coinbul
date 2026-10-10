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


def evaluate(symbol, tfs, trades, depth, ticker):
    """tfs: {"15m": klines, ...}. Puan -1 (güçlü satış) .. +1 (güçlü alım)."""
    m = {tf: tf_metrics(k) for tf, k in tfs.items()}
    m = {tf: x for tf, x in m.items() if x}
    book = book_metrics(depth)
    flow = trade_metrics(trades)
    qv24 = float(ticker.get("quoteVolume", 0))
    fake, fake_reasons = fake_volume_score(trades, m.get("15m"), book, qv24)

    w_total = sum(TF_WEIGHTS.get(tf, 1) for tf in m) or 1
    momentum = sum(TF_WEIGHTS.get(tf, 1) * (1 if x.trend_up else -1) for tf, x in m.items()) / w_total

    short = [m[tf] for tf in ("15m", "1h") if tf in m]
    rvol = max((x.rvol for x in short), default=1.0)
    vol_score = clamp(math.log2(rvol) / 2) if rvol > 0 else -1
    taker = mean(x.taker_buy_ratio for x in short) if short else 0.5
    flow_score = clamp((0.7 * taker + 0.3 * flow.buy_ratio_60s - 0.5) * 4)
    imbalance = book.imbalance if book else 0.0

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
               f"son 60sn alıcı %{flow.buy_ratio_60s * 100:.0f}"] + fake_reasons
    return Signal(
        symbol=symbol, score=round(score, 3), label=label, fake_score=round(fake, 2),
        price=book.best_ask if book else float(ticker.get("lastPrice", 0)),
        spread_pct=book.spread_pct if book else 99.0,
        change_24h_pct=float(ticker.get("priceChangePercent", 0)),
        quote_volume_24h=qv24, reasons=reasons,
        tf={tf: round(x.change_pct, 2) for tf, x in m.items()},
    )
