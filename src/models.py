"""
models.py — Datové modely a CandleBuffer.

Pydantic v2 modely:
  - Candle             — OHLCV svíčka z Binance
  - Indicators         — vypočtené technické indikátory
  - PolymarketQuote    — jeden BTC Up/Down trh
  - AIResponse         — co očekáváme od LLM (validovaný JSON)
  - Signal             — finální obchodní doporučení (perzistované)
"""

from collections import deque
from datetime import datetime, timezone
from typing import Iterable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


Action = Literal["BUY_UP", "BUY_DOWN", "SKIP"]
EmaCross = Literal["bullish", "bearish", "neutral"]


class Candle(BaseModel):
    """Jedna OHLCV svíčka. open_time je unix ms (začátek svíčky)."""
    model_config = ConfigDict(frozen=False)

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = False

    @property
    def dt(self) -> str:
        """HH:MM UTC."""
        return datetime.fromtimestamp(self.open_time / 1000, tz=timezone.utc).strftime("%H:%M")

    @property
    def dt_full(self) -> str:
        """YYYY-MM-DD HH:MM UTC."""
        return datetime.fromtimestamp(self.open_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


class Indicators(BaseModel):
    rsi_14: Optional[float] = None
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    ema_cross: Optional[EmaCross] = None
    volume_ratio: Optional[float] = None

    @property
    def ready(self) -> bool:
        return all(v is not None for v in (self.rsi_14, self.ema_fast, self.ema_slow))


class PolymarketQuote(BaseModel):
    slug: str
    question: str
    yes: Optional[float] = None
    no: Optional[float] = None
    active: bool = True


class AIResponse(BaseModel):
    """Validovaný JSON output z LLM."""
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    suggested_market: str


class Signal(BaseModel):
    """Finální signál — to, co loggujeme a posíláme do hubu."""
    timestamp: str
    btc_price: float
    action: Action
    confidence: float
    reasoning: str
    suggested_market: str
    indicators: dict
    strategy: str = "ai_advisor"

    @property
    def is_actionable(self) -> bool:
        return self.action != "SKIP" and self.confidence >= 0.60


# ---------- Paper trading ----------

Outcome = Literal["YES", "NO"]
PositionStatus = Literal["OPEN", "WON", "LOST", "CANCELLED"]
TradeEventType = Literal["ORDER", "FILL", "SETTLED", "REJECTED"]


class TradeOrder(BaseModel):
    """Záměr otevřít pozici. Vytváří TradingPolicy ze Signal + market quote."""
    slug: str
    market_interval: str       # "5m" | "15m"
    question: str
    outcome: Outcome
    size_usd: float
    limit_price: float         # tržní YES/NO cena v okamžiku rozhodnutí
    signal_action: Action      # BUY_UP / BUY_DOWN
    signal_confidence: float


class Position(BaseModel):
    """Otevřená nebo uzavřená pozice."""
    id: str                                  # uuid4
    slug: str
    market_interval: str
    question: str
    outcome: Outcome
    size_usd: float
    entry_price: float
    entry_time: str                          # ISO UTC
    status: PositionStatus = "OPEN"
    resolution_price: Optional[float] = None
    resolution_time: Optional[str] = None
    pnl_usd: Optional[float] = None
    signal_action: Action
    signal_confidence: float

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN"


class TradeEvent(BaseModel):
    """Event v trade logu."""
    event: TradeEventType
    timestamp: str
    position_id: str
    detail: dict


class CandleBuffer:
    """
    Rolling buffer svíček s update-or-append sémantikou.

    Binance WS posílá stejnou svíčku opakovaně (každou sekundu),
    dokud se neuzavře. update() rozhodne, zda nahradit poslední
    svíčku (stejný open_time) nebo přidat novou.
    """

    def __init__(self, maxlen: int):
        self._buf: deque[Candle] = deque(maxlen=maxlen)

    def update(self, candle: Candle) -> None:
        if self._buf and self._buf[-1].open_time == candle.open_time:
            self._buf[-1] = candle
        else:
            self._buf.append(candle)

    def extend(self, candles: Iterable[Candle]) -> None:
        self._buf.extend(candles)

    def snapshot(self) -> List[Candle]:
        return list(self._buf)

    def __len__(self) -> int:
        return len(self._buf)
