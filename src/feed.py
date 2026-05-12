"""
feed.py — Binance feed (REST backfill + WS stream).

Změny oproti v1:
  - stream_candles je čistý async generátor — yieldje KAŽDOU svíčku
    (i otevřenou). Buffer se aktualizuje v consumeru, ne uvnitř.
  - fetch_historical používá sdílený httpx.AsyncClient.
"""

import asyncio
import json
import logging
from typing import AsyncIterator, List

import httpx
import websockets

from config import Config
from models import Candle

log = logging.getLogger(__name__)


async def fetch_historical(config: Config, client: httpx.AsyncClient) -> List[Candle]:
    """Načte N historických svíček z Binance REST API."""
    log.info(
        f"Načítám {config.initial_candles} historických {config.interval} svíček "
        f"({config.symbol}) z Binance REST..."
    )
    resp = await client.get(
        config.binance_rest_url,
        params={
            "symbol": config.symbol,
            "interval": config.interval,
            "limit": config.initial_candles,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    rows = resp.json()

    candles = [
        Candle(
            open_time=row[0],
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            closed=True,
        )
        for row in rows
    ]

    if candles:
        log.info(
            f"Načteno {len(candles)} svíček. "
            f"Od {candles[0].dt_full} do {candles[-1].dt_full} UTC"
        )
    return candles


async def stream_candles(config: Config) -> AsyncIterator[Candle]:
    """
    Async generátor, který yieldje každou Candle z WS streamu.
    Automatický reconnect při výpadku.

    Consumer si svíčku přidá do bufferu sám — generátor nemá vedlejší efekty.
    """
    log.info(f"Připojuji se k Binance WebSocket: {config.binance_ws_url}")

    async for ws in websockets.connect(config.binance_ws_url, ping_interval=20):
        try:
            async for raw_msg in ws:
                msg = json.loads(raw_msg)
                k = msg.get("k", {})
                if not k:
                    continue
                yield Candle(
                    open_time=k["t"],
                    open=float(k["o"]),
                    high=float(k["h"]),
                    low=float(k["l"]),
                    close=float(k["c"]),
                    volume=float(k["v"]),
                    closed=bool(k["x"]),
                )
        except websockets.ConnectionClosed:
            log.warning("WebSocket odpojen. Reconnectuji za 3s...")
            await asyncio.sleep(3)
