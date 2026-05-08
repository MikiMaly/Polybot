"""
models.py — Datové třídy pro svíčky, indikátory a signály.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Candle:
    """Jedna OHLCV svíčka z Binance."""
    open_time: int        # Unix timestamp v ms (začátek svíčky)
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = False  # True = svíčka je uzavřená (finální data)

    @property
    def dt(self) -> str:
        """Čas svíčky jako HH:MM UTC."""
        return datetime.utcfromtimestamp(self.open_time / 1000).strftime("%H:%M")

    @property
    def dt_full(self) -> str:
        return datetime.utcfromtimestamp(self.open_time / 1000).strftime("%Y-%m-%d %H:%M")


@dataclass
class Indicators:
    """Vypočtené technické indikátory pro aktuální sadu svíček."""
    rsi_14: Optional[float] = None
    ema_fast: Optional[float] = None       # EMA(9)
    ema_slow: Optional[float] = None       # EMA(21)
    ema_cross: Optional[str] = None        # "bullish" | "bearish" | "neutral"
    volume_ratio: Optional[float] = None   # Aktuální objem / 20-svíčkový průměr

    @property
    def ready(self) -> bool:
        """True pokud máme dost dat pro všechny indikátory."""
        return all(v is not None for v in [self.rsi_14, self.ema_fast, self.ema_slow])

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Signal:
    """Výstup z Claude — obchodní doporučení."""
    timestamp: str
    btc_price: float
    action: str             # "BUY_UP" | "BUY_DOWN" | "SKIP"
    confidence: float       # 0.0 – 1.0
    reasoning: str          # Zdůvodnění od Clauda (max 2 věty)
    suggested_market: str   # Např. "BTC above $103k by end of day"
    indicators: dict        # Snapshot indikátorů v době signálu

    @property
    def is_actionable(self) -> bool:
        return self.action != "SKIP" and self.confidence >= 0.60

    def to_dict(self) -> dict:
        return asdict(self)
