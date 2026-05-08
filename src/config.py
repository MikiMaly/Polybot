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
        cfg.hub_api_url = os.environ.get("HUB_API_URL", "")
        cfg.hub_bot_secret = os.environ.get("HUB_BOT_SECRET", "")
        return cfg
