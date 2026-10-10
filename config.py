"""Ajan ayarları. Değerler .env dosyasından veya ortam değişkenlerinden okunur."""
import os
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


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


def _f(name, default):
    return float(os.environ.get(name, default))


def _i(name, default):
    return int(os.environ.get(name, default))


@dataclass
class Config:
    # Bağlantı
    api_key: str = ""
    api_secret: str = ""
    base_url: str = "https://www.binance.tr"
    market_url: str = "https://api.binance.me"

    # Mod: "paper" (sanal para, varsayılan) veya "live" (gerçek para)
    mode: str = "paper"
    confirm_live: str = ""
    paper_start_try: float = 10_000.0
    fee_rate: float = 0.001

    # Tarama
    timeframes: tuple = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
    min_quote_volume_try: float = 2_000_000.0  # 24s hacmi bunun altındaki coinler taranmaz
    max_symbols: int = 60
    loop_seconds: int = 60

    # Alım kuralları
    buy_score: float = 0.45
    max_fake_score: float = 0.4
    max_spread_pct: float = 0.4
    max_24h_change_pct: float = 25.0  # bundan fazla yükselmişse "pump" kovalama

    # Risk
    trade_size_try: float = 500.0
    max_open_positions: int = 3
    max_daily_loss_try: float = 750.0
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 6.0
    trailing_start_pct: float = 3.0
    trailing_pct: float = 1.5
    max_hold_hours: float = 48.0

    data_dir: Path = field(default=HERE / "data")

    @property
    def live(self):
        return self.mode == "live"

    @classmethod
    def from_env(cls):
        load_env()
        e = os.environ
        cfg = cls(
            api_key=e.get("BINANCE_TR_API_KEY", ""),
            api_secret=e.get("BINANCE_TR_API_SECRET", ""),
            base_url=e.get("BINANCE_TR_BASE_URL", cls.base_url),
            market_url=e.get("BINANCE_TR_MARKET_URL", cls.market_url),
            mode=e.get("MODE", "paper").lower(),
            confirm_live=e.get("CONFIRM_LIVE", ""),
            paper_start_try=_f("PAPER_START_TRY", cls.paper_start_try),
            fee_rate=_f("FEE_RATE", cls.fee_rate),
            min_quote_volume_try=_f("MIN_QUOTE_VOLUME_TRY", cls.min_quote_volume_try),
            max_symbols=_i("MAX_SYMBOLS", cls.max_symbols),
            loop_seconds=_i("LOOP_SECONDS", cls.loop_seconds),
            buy_score=_f("BUY_SCORE", cls.buy_score),
            max_fake_score=_f("MAX_FAKE_SCORE", cls.max_fake_score),
            max_spread_pct=_f("MAX_SPREAD_PCT", cls.max_spread_pct),
            max_24h_change_pct=_f("MAX_24H_CHANGE_PCT", cls.max_24h_change_pct),
            trade_size_try=_f("TRADE_SIZE_TRY", cls.trade_size_try),
            max_open_positions=_i("MAX_OPEN_POSITIONS", cls.max_open_positions),
            max_daily_loss_try=_f("MAX_DAILY_LOSS_TRY", cls.max_daily_loss_try),
            stop_loss_pct=_f("STOP_LOSS_PCT", cls.stop_loss_pct),
            take_profit_pct=_f("TAKE_PROFIT_PCT", cls.take_profit_pct),
            trailing_start_pct=_f("TRAILING_START_PCT", cls.trailing_start_pct),
            trailing_pct=_f("TRAILING_PCT", cls.trailing_pct),
            max_hold_hours=_f("MAX_HOLD_HOURS", cls.max_hold_hours),
        )
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
        if self.trade_size_try <= 0 or self.max_open_positions <= 0:
            raise ValueError("TRADE_SIZE_TRY ve MAX_OPEN_POSITIONS pozitif olmalı")
