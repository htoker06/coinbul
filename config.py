"""Ajan ayarları. Değerler .env dosyasından veya ortam değişkenlerinden okunur."""
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

HERE = Path(__file__).resolve().parent

STABLECOINS = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EURI", "EUR", "PAXG"}


def load_env(path=HERE / ".env"):
    """Basit .env okuyucu (ek kütüphane gerektirmez)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _hours(text):
    """'2-8' -> (2, 8). Boş -> None"""
    text = (text or "").strip()
    if not text:
        return None
    a, b = text.split("-")
    return int(a), int(b)


@dataclass
class Config:
    # Bağlantı
    api_key: str = ""
    api_secret: str = ""
    base_url: str = "https://www.binance.tr"
    market_url: str = "https://api.binance.me"
    global_url: str = "https://api.binance.com"

    # Mod: "paper" (sanal para, varsayılan) veya "live" (gerçek para)
    mode: str = "paper"
    confirm_live: str = ""
    paper_start_try: float = 10_000.0
    fee_rate: float = 0.001

    # Tarama
    timeframes: tuple = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
    min_quote_volume_try: float = 2_000_000.0
    max_symbols: int = 60
    loop_seconds: int = 60

    # Alım kuralları
    buy_score: float = 0.45
    max_fake_score: float = 0.4
    max_spread_pct: float = 0.4
    max_24h_change_pct: float = 25.0
    confirm_rounds: int = 2                 # (11) sinyal kaç tur üst üste sürmeli
    blacklist: str = ""                     # (7) virgülle: "PEPE,SHIB"
    min_listing_days: int = 14              # (7) yeni listelenen coinleri alma
    no_buy_hours: str = "2-8"               # (17) Türkiye saatiyle alım yapılmayan saatler, boş = kapalı

    # (1) Bitcoin filtresi
    btc_filter: bool = True
    btc_max_drop_1h_pct: float = 2.0

    # (8) Pump koruması
    pump_5m_pct: float = 5.0
    pump_pullback_pct: float = 1.5
    pump_watch_minutes: int = 120

    # (9) Global Binance karşılaştırması
    global_check: bool = True
    max_premium_pct: float = 1.5
    local_rvol_ratio: float = 2.5           # TR hacim artışı globalin bu katından fazlaysa şüpheli

    # (5) Kayma koruması ve emir tipi
    max_slippage_pct: float = 0.3
    order_type: str = "limit"               # limit veya market

    # Risk ve tutar (2)
    risk_per_trade_try: float = 30.0        # stop olursa kaybedilecek yaklaşık tutar
    min_trade_try: float = 200.0
    max_trade_try: float = 1_000.0
    atr_mult: float = 2.0
    min_stop_pct: float = 1.5
    max_stop_pct: float = 8.0
    reward_risk: float = 2.0                # kâr al = stop mesafesi x bu oran
    max_open_positions: int = 3
    max_daily_loss_try: float = 750.0
    max_weekly_loss_try: float = 2_000.0    # (4)
    trailing_start_r: float = 1.0           # kâr stop mesafesine ulaşınca iz süren stop devreye girer
    trailing_atr_mult: float = 1.5
    max_hold_hours: float = 48.0

    # (3) Mola kuralları
    max_consecutive_losses: int = 3
    loss_pause_minutes: int = 120
    symbol_cooldown_hours: float = 6.0

    # (16) Kademeli satış
    partial_take_r: float = 1.0             # kâr stop mesafesine ulaşınca...
    partial_fraction: float = 0.5           # ...pozisyonun bu kadarını sat, stopu giriş fiyatına çek

    # (6) Borsada stop emri (sadece canlı mod)
    exchange_stop: bool = True

    # (14) Telegram
    telegram_token: str = ""
    telegram_chat_id: str = ""
    daily_report_hour: int = 23             # Türkiye saati

    data_dir: Path = field(default=HERE / "data")

    @property
    def live(self):
        return self.mode == "live"

    @property
    def blacklist_set(self):
        return {s.strip().upper() for s in self.blacklist.split(",") if s.strip()}

    @property
    def no_buy_window(self):
        return _hours(self.no_buy_hours)

    @classmethod
    def from_env(cls):
        load_env()
        kwargs = {}
        for f in fields(cls):
            if f.name in ("timeframes", "data_dir"):
                continue
            raw = os.environ.get(f.name.upper())
            if raw is None:
                continue
            if f.type in (bool, "bool"):
                kwargs[f.name] = raw.strip().lower() in ("1", "true", "evet", "yes", "on")
            elif f.type in (int, "int"):
                kwargs[f.name] = int(raw)
            elif f.type in (float, "float"):
                kwargs[f.name] = float(raw)
            else:
                kwargs[f.name] = raw
        # Eski adlarla geriye uyumluluk
        if "BINANCE_TR_API_KEY" in os.environ:
            kwargs["api_key"] = os.environ["BINANCE_TR_API_KEY"]
        if "BINANCE_TR_API_SECRET" in os.environ:
            kwargs["api_secret"] = os.environ["BINANCE_TR_API_SECRET"]
        for env, name in (("BINANCE_TR_BASE_URL", "base_url"), ("BINANCE_TR_MARKET_URL", "market_url"),
                          ("BINANCE_GLOBAL_URL", "global_url")):
            if env in os.environ:
                kwargs[name] = os.environ[env]
        cfg = cls(**kwargs)
        cfg.mode = cfg.mode.lower()
        cfg.validate()
        return cfg

    def validate(self):
        if self.mode not in ("paper", "live"):
            raise ValueError("MODE 'paper' veya 'live' olmalı")
        if self.live:
            if not (self.api_key and self.api_secret):
                raise ValueError("Canlı mod için BINANCE_TR_API_KEY ve BINANCE_TR_API_SECRET gerekli")
            if self.confirm_live != "EVET":
                raise ValueError("Canlı mod için .env içinde CONFIRM_LIVE=EVET yazmalısınız")
        if self.order_type not in ("limit", "market"):
            raise ValueError("ORDER_TYPE 'limit' veya 'market' olmalı")
        if not (0 < self.min_trade_try <= self.max_trade_try) or self.max_open_positions <= 0:
            raise ValueError("MIN_TRADE_TRY, MAX_TRADE_TRY ve MAX_OPEN_POSITIONS pozitif olmalı")
        if not (0 < self.partial_fraction < 1):
            raise ValueError("PARTIAL_FRACTION 0 ile 1 arasında olmalı")
        _hours(self.no_buy_hours)
