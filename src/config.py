"""
config.py — Centrální konfigurace bota.
Všechny parametry jsou načítány z .env nebo přímo z tohoto souboru.
"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-oss-120b:free"
    openrouter_max_tokens: int = 1024

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
    signal_cooldown: int = 90    # minimální odstup mezi signály
    analysis_interval: int = 120  # jak často spouštět analýzu (sekundy)

    # Výstup
    signals_log: str = "logs/signals.jsonl"

    # Hub API (volitelné)
    hub_api_url: str = ""
    hub_bot_secret: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls()
        cfg.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not cfg.openrouter_api_key:
            raise EnvironmentError(
                "Chybí OPENROUTER_API_KEY. Nastav ho v .env nebo jako env proměnnou."
            )
        cfg.openrouter_model = os.environ.get("OPENROUTER_MODEL", cfg.openrouter_model)
        cfg.openrouter_max_tokens = int(os.environ.get("OPENROUTER_MAX_TOKENS", cfg.openrouter_max_tokens))

        cfg.symbol = os.environ.get("SYMBOL", cfg.symbol)
        cfg.interval = os.environ.get("INTERVAL", cfg.interval)
        cfg.initial_candles = int(os.environ.get("INITIAL_CANDLES", cfg.initial_candles))

        cfg.rsi_period = int(os.environ.get("RSI_PERIOD", cfg.rsi_period))
        cfg.ema_fast = int(os.environ.get("EMA_FAST", cfg.ema_fast))
        cfg.ema_slow = int(os.environ.get("EMA_SLOW", cfg.ema_slow))
        cfg.candle_history = int(os.environ.get("CANDLE_HISTORY", cfg.candle_history))

        cfg.min_confidence = float(os.environ.get("MIN_CONFIDENCE", cfg.min_confidence))
        cfg.signal_cooldown = int(os.environ.get("SIGNAL_COOLDOWN", cfg.signal_cooldown))
        cfg.analysis_interval = int(os.environ.get("ANALYSIS_INTERVAL", cfg.analysis_interval))

        cfg.signals_log = os.environ.get("SIGNALS_LOG", cfg.signals_log)
        cfg.hub_api_url = os.environ.get("HUB_API_URL", "")
        cfg.hub_bot_secret = os.environ.get("HUB_BOT_SECRET", "")
        return cfg
