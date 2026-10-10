"""Emir yürütme: sanal (paper) ve gerçek (live) aracı.

İkisi de aynı arayüzü sunar: buy(...) ve sell(...) -> (miktar, ortalama fiyat, komisyon TRY)
"""
import time
from decimal import Decimal


def floor_step(qty, step):
    if not step:
        return qty
    d = Decimal(str(step))
    return float((Decimal(str(qty)) // d) * d)


class PaperBroker:
    """Gerçek fiyatlarla sanal işlem. Kaydırma (slippage) için en iyi alış/satış fiyatı kullanılır."""

    def __init__(self, fee_rate):
        self.fee_rate = fee_rate

    def buy(self, symbol, quote_amount, ask_price, info):
        fee = quote_amount * self.fee_rate
        qty = floor_step((quote_amount - fee) / ask_price, info.get("step"))
        return qty, ask_price, fee

    def sell(self, symbol, qty, bid_price, info):
        gross = qty * bid_price
        return qty, bid_price, gross * self.fee_rate


class LiveBroker:
    """Gerçek emir gönderir. Miktarı borsa bakiyesindeki değişimden hesaplar."""

    def __init__(self, client):
        self.client = client

    def _base_balance(self, asset):
        return self.client.balances().get(asset, 0.0)

    def buy(self, symbol, quote_amount, ask_price, info):
        asset = info["base"]
        before = self._base_balance(asset)
        self.client.market_buy(symbol, quote_amount)
        time.sleep(1.5)
        qty = self._base_balance(asset) - before
        if qty <= 0:
            raise RuntimeError(f"{symbol} alımı sonrası bakiye artmadı; borsadan kontrol edin")
        return qty, quote_amount / qty, 0.0  # komisyon fiyata yansımış durumda

    def sell(self, symbol, qty, bid_price, info):
        free = self._base_balance(info["base"])
        qty = floor_step(min(qty, free), info.get("step"))
        if qty <= 0 or (info.get("min_qty") and qty < info["min_qty"]):
            raise RuntimeError(f"{symbol} satılacak miktar çok küçük ({qty})")
        self.client.market_sell(symbol, f"{qty:f}".rstrip("0").rstrip("."))
        return qty, bid_price, 0.0  # gerçek fiyat borsa geçmişinde; burada tahmini


def parse_symbol_info(raw):
    """Binance TR sembol kaydından ihtiyacımız olan alanlar."""
    info = {"base": raw.get("baseAsset"), "quote": raw.get("quoteAsset"),
            "type": raw.get("type"), "step": None, "min_qty": None, "min_notional": None}
    for f in raw.get("filters", []):
        t = f.get("filterType")
        if t == "LOT_SIZE":
            info["step"] = float(f.get("stepSize", 0)) or None
            info["min_qty"] = float(f.get("minQty", 0)) or None
        elif t in ("MIN_NOTIONAL", "NOTIONAL"):
            info["min_notional"] = float(f.get("minNotional", 0)) or None
    if info["step"] is None and raw.get("basePrecision") is not None:
        info["step"] = 10 ** -int(raw["basePrecision"])
    return info


def pct(a, b):
    return (a / b - 1) * 100 if b else 0.0

