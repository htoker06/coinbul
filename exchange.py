"""Binance TR ve global Binance API istemcisi.

Binance TR'de iki adres kullanılır:
  * www.binance.tr/open/v1/...   -> sembol listesi, hesap, emirler (sembol "BTC_TRY")
  * api.binance.me/api/v3/...    -> ana piyasa verisi: mum, işlem, derinlik (sembol "BTCTRY")
Global karşılaştırma için api.binance.com kullanılır (sadece herkese açık veri).
Uç noktalar https://www.binance.tr/apidocs/ belgelerine göre yazıldı. Binance adres
değiştirirse .env içinden BINANCE_TR_BASE_URL / BINANCE_TR_MARKET_URL ile güncelleyin.
"""
import hashlib
import hmac
import time
import urllib.parse

import requests

SIDE_BUY, SIDE_SELL = 0, 1
TYPE_LIMIT, TYPE_MARKET, TYPE_STOP_LOSS, TYPE_STOP_LOSS_LIMIT = 1, 2, 3, 4


class ExchangeError(RuntimeError):
    pass


def sign(secret, query):
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def market_symbol(symbol):
    """'BTC_TRY' -> 'BTCTRY'"""
    return symbol.replace("_", "")


def fmt(x):
    """Bilimsel gösterim olmadan sayı: 0.00001 -> '0.00001'"""
    return f"{x:.10f}".rstrip("0").rstrip(".")


class HttpClient:
    def __init__(self, timeout=10):
        self.timeout = timeout
        self.session = requests.Session()

    def _request(self, method, url, **kwargs):
        for attempt in range(3):
            try:
                r = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise ExchangeError(f"Bağlantı hatası: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (418, 429):  # istek sınırı aşıldı
                time.sleep(int(r.headers.get("Retry-After", 5 * (attempt + 1))))
                continue
            if r.status_code >= 400:
                raise ExchangeError(f"HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            # open/v1 uçları {"code": 0, "msg": ..., "data": ...} döner
            if isinstance(data, dict) and "code" in data and data["code"] != 0:
                raise ExchangeError(f"Borsa hatası {data.get('code')}: {data.get('msg')}")
            return data
        raise ExchangeError("İstek sınırı aşıldı, tekrar denemeler başarısız")


class BinanceGlobal(HttpClient):
    """Sadece herkese açık piyasa verisi. Anahtar kullanılmaz."""

    def __init__(self, url="https://api.binance.com", timeout=10):
        super().__init__(timeout)
        self.url = url.rstrip("/")

    def tickers_24h(self):
        return self._request("GET", f"{self.url}/api/v3/ticker/24hr")

    def klines(self, symbol, interval, limit=60):
        return self._request("GET", f"{self.url}/api/v3/klines",
                             params={"symbol": symbol, "interval": interval, "limit": limit})


class BinanceTR(HttpClient):
    def __init__(self, api_key="", api_secret="", base_url="https://www.binance.tr",
                 market_url="https://api.binance.me", timeout=10):
        super().__init__(timeout)
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.market_url = market_url.rstrip("/")

    def _signed(self, method, path, params=None):
        if not (self.api_key and self.api_secret):
            raise ExchangeError("API anahtarı tanımlı değil")
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params.setdefault("recvWindow", 5000)
        query = urllib.parse.urlencode(params)
        query += "&signature=" + sign(self.api_secret, query)
        headers = {"X-MBX-APIKEY": self.api_key}
        url = f"{self.base_url}{path}"
        if method == "GET":
            return self._request("GET", f"{url}?{query}", headers=headers)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self._request(method, url, data=query, headers=headers)

    # ---------------------------------------------------------------- herkese açık
    def symbols(self):
        return self._request("GET", f"{self.base_url}/open/v1/common/symbols")["data"]["list"]

    def tickers_24h(self):
        return self._request("GET", f"{self.market_url}/api/v3/ticker/24hr")

    def klines(self, symbol, interval, limit=60, start_ms=None):
        params = {"symbol": market_symbol(symbol), "interval": interval, "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        return self._request("GET", f"{self.market_url}/api/v3/klines", params=params)

    def trades(self, symbol, limit=500):
        return self._request("GET", f"{self.market_url}/api/v3/trades",
                             params={"symbol": market_symbol(symbol), "limit": limit})

    def depth(self, symbol, limit=100):
        return self._request("GET", f"{self.market_url}/api/v3/depth",
                             params={"symbol": market_symbol(symbol), "limit": limit})

    # ---------------------------------------------------------------- hesap / emir
    def balances(self):
        """{varlık: {"free": .., "locked": ..}}"""
        data = self._signed("GET", "/open/v1/account/spot")["data"]
        return {a["asset"]: {"free": float(a.get("free", 0)), "locked": float(a.get("locked", 0))}
                for a in data.get("accountAssets", [])}

    def market_buy(self, symbol, quote_amount):
        return self._signed("POST", "/open/v1/orders", {
            "symbol": symbol, "side": SIDE_BUY, "type": TYPE_MARKET,
            "quoteOrderQty": f"{quote_amount:.2f}"})["data"]

    def market_sell(self, symbol, quantity):
        return self._signed("POST", "/open/v1/orders", {
            "symbol": symbol, "side": SIDE_SELL, "type": TYPE_MARKET,
            "quantity": fmt(quantity)})["data"]

    def limit_order(self, symbol, side, quantity, price):
        return self._signed("POST", "/open/v1/orders", {
            "symbol": symbol, "side": side, "type": TYPE_LIMIT,
            "quantity": fmt(quantity), "price": fmt(price)})["data"]

    def stop_loss_limit(self, symbol, quantity, stop_price, limit_price):
        return self._signed("POST", "/open/v1/orders", {
            "symbol": symbol, "side": SIDE_SELL, "type": TYPE_STOP_LOSS_LIMIT,
            "quantity": fmt(quantity), "stopPrice": fmt(stop_price), "price": fmt(limit_price)})["data"]

    def cancel_order(self, order_id):
        return self._signed("POST", "/open/v1/orders/cancel", {"orderId": order_id})["data"]

    def order_detail(self, order_id):
        return self._signed("GET", "/open/v1/orders/detail", {"orderId": order_id})["data"]
