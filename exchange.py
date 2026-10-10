"""Binance TR API istemcisi.

Binance TR'de iki adres kullanılır:
  * www.binance.tr/open/v1/...   -> sembol listesi, hesap, emirler (sembol "BTC_TRY")
  * api.binance.me/api/v3/...    -> ana piyasa verisi: mum, işlem, derinlik (sembol "BTCTRY")
Uç noktalar https://www.binance.tr/apidocs/ belgelerine göre yazıldı. Binance adres
değiştirirse .env içinden BINANCE_TR_BASE_URL / BINANCE_TR_MARKET_URL ile güncelleyin.
"""
import hashlib
import hmac
import time
import urllib.parse

import requests

SIDE_BUY, SIDE_SELL = 0, 1
TYPE_LIMIT, TYPE_MARKET = 1, 2


class ExchangeError(RuntimeError):
    pass


def sign(secret, query):
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def market_symbol(symbol):
    """'BTC_TRY' -> 'BTCTRY'"""
    return symbol.replace("_", "")


class BinanceTR:
    def __init__(self, api_key="", api_secret="", base_url="https://www.binance.tr",
                 market_url="https://api.binance.me", timeout=10):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.market_url = market_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    # ---------------------------------------------------------------- yardımcı
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

    def klines(self, symbol, interval, limit=60):
        return self._request("GET", f"{self.market_url}/api/v3/klines",
                             params={"symbol": market_symbol(symbol), "interval": interval, "limit": limit})

    def trades(self, symbol, limit=500):
        return self._request("GET", f"{self.market_url}/api/v3/trades",
                             params={"symbol": market_symbol(symbol), "limit": limit})

    def depth(self, symbol, limit=100):
        return self._request("GET", f"{self.market_url}/api/v3/depth",
                             params={"symbol": market_symbol(symbol), "limit": limit})

    # ---------------------------------------------------------------- hesap / emir
    def balances(self):
        data = self._signed("GET", "/open/v1/account/spot")["data"]
        return {a["asset"]: float(a["free"]) for a in data.get("accountAssets", [])}

    def market_buy(self, symbol, quote_amount):
        return self._signed("POST", "/open/v1/orders", {
            "symbol": symbol, "side": SIDE_BUY, "type": TYPE_MARKET,
            "quoteOrderQty": f"{quote_amount:.2f}",
        })["data"]

    def market_sell(self, symbol, quantity):
        return self._signed("POST", "/open/v1/orders", {
            "symbol": symbol, "side": SIDE_SELL, "type": TYPE_MARKET,
            "quantity": quantity,
        })["data"]
