"""Emir yürütme: sanal (paper) ve gerçek (live) aracı.

İkisi de aynı arayüzü sunar. buy/sell şu sözlüğü döner:
    {"qty": miktar, "try": ödenen ya da alınan TRY (komisyon dahil), "price": ortalama fiyat}
"""
import time
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from analysis import limit_price_for
from exchange import SIDE_BUY


def floor_step(qty, step):
    if not step:
        return qty
    d = Decimal(str(step))
    return float((Decimal(str(qty)) / d).to_integral_value(ROUND_DOWN) * d)


def round_tick(price, tick, up=False):
    if not tick:
        return price
    d = Decimal(str(tick))
    return float((Decimal(str(price)) / d).to_integral_value(ROUND_UP if up else ROUND_DOWN) * d)


def walk_book(levels, quote_amount=None, qty=None, limit=None, side="buy"):
    """Emir defterinde ilerleyerek gerçekleşecek miktarı ve tutarı hesaplar."""
    got_qty, got_try = 0.0, 0.0
    for p, q in ((float(p), float(q)) for p, q, *_ in levels):
        if limit is not None and ((side == "buy" and p > limit) or (side == "sell" and p < limit)):
            break
        if quote_amount is not None:
            take = min(q, (quote_amount - got_try) / p)
        else:
            take = min(q, qty - got_qty)
        if take <= 0:
            break
        got_qty += take
        got_try += take * p
    return got_qty, got_try


class PaperBroker:
    """Gerçek emir defteri üzerinden sanal işlem (kayma ve komisyon dahil)."""

    live = False

    def __init__(self, cfg):
        self.cfg = cfg

    def buy(self, symbol, size_try, depth, info):
        limit = (limit_price_for(depth, size_try, self.cfg.max_slippage_pct)
                 if self.cfg.order_type == "limit" else None)
        spend = size_try / (1 + self.cfg.fee_rate)
        qty, cost = walk_book(depth["asks"], quote_amount=spend, limit=limit, side="buy")
        qty = floor_step(qty, info.get("step"))
        if qty <= 0:
            raise RuntimeError(f"{symbol} için uygun fiyattan satıcı yok")
        cost = cost * (1 + self.cfg.fee_rate)
        return {"qty": qty, "try": cost, "price": cost / qty}

    def sell(self, symbol, qty, depth, info):
        filled, gross = walk_book(depth["bids"], qty=qty, side="sell")
        if filled <= 0:
            raise RuntimeError(f"{symbol} için alıcı yok")
        net = gross * (1 - self.cfg.fee_rate)
        return {"qty": filled, "try": net, "price": gross / filled}

    def place_stop(self, symbol, qty, stop_price, info):
        return None  # sanal modda stopu ajan kendisi izler

    def cancel_stop(self, order_id):
        pass

    def holdings(self, asset):
        return None


class LiveBroker:
    """Gerçek emir gönderir. Miktarları ve tutarları borsa bakiyesindeki değişimden hesaplar,
    böylece borsanın emir cevabının biçiminden bağımsız olarak doğru kayıt tutulur."""

    live = True
    settle_seconds = 3.0

    def __init__(self, cfg, client):
        self.cfg = cfg
        self.client = client

    def _snapshot(self, asset):
        b = self.client.balances()
        base = b.get(asset, {"free": 0.0, "locked": 0.0})
        return base["free"] + base["locked"], b.get("TRY", {"free": 0.0})["free"], base["free"]

    def holdings(self, asset):
        return self._snapshot(asset)[0]

    def buy(self, symbol, size_try, depth, info):
        base0, try0, _ = self._snapshot(info["base"])
        if self.cfg.order_type == "limit":
            limit = limit_price_for(depth, size_try, self.cfg.max_slippage_pct)
            limit = round_tick(limit, info.get("tick"))
            qty = floor_step(size_try / (1 + self.cfg.fee_rate) / limit, info.get("step"))
            order = self.client.limit_order(symbol, SIDE_BUY, qty, limit)
            time.sleep(self.settle_seconds)
            try:  # dolmayan kısmı iptal et (tamamen dolduysa hata verebilir, sorun değil)
                self.client.cancel_order(order.get("orderId"))
            except Exception:
                pass
        else:
            self.client.market_buy(symbol, size_try)
        time.sleep(1.0)
        base1, try1, _ = self._snapshot(info["base"])
        qty, cost = base1 - base0, try0 - try1
        if qty <= 0:
            raise RuntimeError(f"{symbol} alımı gerçekleşmedi (bakiye artmadı)")
        return {"qty": qty, "try": cost, "price": cost / qty if cost > 0 else 0.0, "baseline": base0}

    def sell(self, symbol, qty, depth, info):
        _, try0, free = self._snapshot(info["base"])
        qty = floor_step(min(qty, free), info.get("step"))
        if qty <= 0 or (info.get("min_qty") and qty < info["min_qty"]):
            raise RuntimeError(f"{symbol} satılacak miktar çok küçük ({qty})")
        self.client.market_sell(symbol, qty)
        time.sleep(1.5)
        _, try1, _ = self._snapshot(info["base"])
        got = try1 - try0
        return {"qty": qty, "try": got, "price": got / qty if got > 0 else 0.0}

    def place_stop(self, symbol, qty, stop_price, info):
        """(6) Borsaya stop-limit satış emri yazar. Ajan kapansa bile borsa uygular."""
        if not self.cfg.exchange_stop:
            return None
        qty = floor_step(qty, info.get("step"))
        stop = round_tick(stop_price, info.get("tick"))
        limit = round_tick(stop_price * 0.99, info.get("tick"))  # hızlı düşüşte de dolsun diye %1 aşağısı
        order = self.client.stop_loss_limit(symbol, qty, stop, limit)
        return order.get("orderId")

    def cancel_stop(self, order_id):
        if order_id:
            try:
                self.client.cancel_order(order_id)
                time.sleep(0.5)  # kilitli bakiyenin serbest kalması için
            except Exception:
                pass  # emir zaten gerçekleşmiş ya da iptal edilmiş olabilir


def parse_symbol_info(raw):
    """Binance TR sembol kaydından ihtiyacımız olan alanlar."""
    info = {"base": raw.get("baseAsset"), "quote": raw.get("quoteAsset"),
            "type": raw.get("type"), "step": None, "tick": None, "min_qty": None,
            "min_notional": None}
    for f in raw.get("filters", []):
        t = f.get("filterType")
        if t == "LOT_SIZE":
            info["step"] = float(f.get("stepSize", 0)) or None
            info["min_qty"] = float(f.get("minQty", 0)) or None
        elif t == "PRICE_FILTER":
            info["tick"] = float(f.get("tickSize", 0)) or None
        elif t in ("MIN_NOTIONAL", "NOTIONAL"):
            info["min_notional"] = float(f.get("minNotional", 0)) or None
    if info["step"] is None and raw.get("basePrecision") is not None:
        info["step"] = 10 ** -int(raw["basePrecision"])
    if info["tick"] is None and raw.get("quotePrecision") is not None:
        info["tick"] = 10 ** -int(raw["quotePrecision"])
    return info


def pct(a, b):
    return (a / b - 1) * 100 if b else 0.0
