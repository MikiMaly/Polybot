"""
config.py — Konfigurace bota přes Pydantic Settings.

Zdroje (v pořadí precedence):
  1. proměnné prostředí
  2. .env soubor (pokud existuje)
  3. defaulty zde
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # OpenRouter
    openrouter_api_key: str = Field(..., description="API klíč z openrouter.ai")
    openrouter_model: str = "openai/gpt-oss-120b:free"
    openrouter_max_tokens: int = 1024
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Binance
    binance_ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@kline_5m"
    binance_rest_url: str = "https://api.binance.com/api/v3/klines"
    symbol: str = "BTCUSDT"
    interval: str = "5m"
    initial_candles: int = 50

    # Indikátory
    rsi_period: int = 14
    ema_fast: int = 9
    ema_slow: int = 21
    candle_history: int = 20

    # Signály
    min_confidence: float = 0.60
    signal_cooldown_seconds: int = 90
    analysis_interval_seconds: int = 120

    # Výstup
    signals_log: str = "logs/signals.jsonl"

    # Hub API (volitelné)
    hub_api_url: str = ""
    hub_bot_secret: str = ""

    # Paper trading
    paper_trading_enabled: bool = True
    paper_initial_balance: float = 100.0       # USD startovní kapitál
    paper_position_size_usd: float = 1.0       # velikost jednoho trade
    paper_max_open_positions: int = 10         # safety net
    paper_min_confidence_to_trade: float = 0.60  # = min_confidence (actionable)
    paper_target_markets: list[str] = ["5m", "15m"]  # kterým marketům dáme trade
    paper_portfolio_path: str = "logs/portfolio.json"
    paper_trades_log: str = "logs/trades.jsonl"
    paper_settler_interval_seconds: int = 60   # jak často kontrolovat rezoluci

    @property
    def buffer_size(self) -> int:
        """Buffer musí být dost velký na backfill i okno pro EMA cross."""
        return max(self.initial_candles, self.candle_history, self.ema_slow + 30)

    @property
    def hub_enabled(self) -> bool:
        return bool(self.hub_api_url and self.hub_bot_secret)
