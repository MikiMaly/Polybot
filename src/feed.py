"""
feed.py — Příjem dat z Binance (REST + WebSocket).

Logika:
  1. Při startu načte historická data přes REST API (rychlé, spolehlivé).
  2. Pak se napojí na WebSocket stream pro live aktualizace.
  3. Každá zpráva z WS je buď aktualizace otevřené svíčky nebo nová uzavřená svíčka.
  4. Uzavřená svíčka (kline.x == true) je finální — spouští analýzu.

Proč kombinovat REST + WS?
  WS stream začíná až od okamžiku připojení — bez historických dat
  by indikátory nebyly přesné prvních ~20 svíček (cca 100 minut).
"""

import json
import logging
from collections import deque
from typing import AsyncIterator, List

import httpx
import websockets

from config import Config
from models import Candle

log = logging.getLogger(__name__)


async def fetch_historical(config: Config) -> List[Candle]:
    """
    Načte N historických 5min svíček z Binance REST API.
    Endpoint: GET /api/v3/klines
    """
    log.info(f"Načítám {config.initial_candles} historických svíček z Binance REST...")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            config.binance_rest_url,
            params={
                "symbol": config.symbol,
                "interval": config.interval,
                "limit": config.initial_candles,
            },
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
            closed=True,  # Historická data jsou vždy uzavřená
        )
        for row in rows
    ]

    log.info(f"Načteno {len(candles)} historických svíček. "
             f"Od {candles[0].dt_full} do {candles[-1].dt_full} UTC")
    return candles


async def stream_candles(
    config: Config,
    buffer: deque,
) -> AsyncIterator[Candle]:
    """
    AsyncIterator — yieldje uzavřené svíčky z Binance WebSocket.

    Binance kline stream posílá zprávy každou sekundu (aktualizace aktuální svíčky)
    a pak finální zprávu s x=true když se svíčka uzavře.

    Automaticky reconnectuje při výpadku spojení.
    """
    log.info(f"Připojuji se k Binance WebSocket: {config.binance_ws_url}")

    async for ws in websockets.connect(config.binance_ws_url, ping_interval=20):
        try:
            async for raw_msg in ws:
                msg = json.loads(raw_msg)
                k = msg.get("k", {})

                candle = Candle(
                    open_time=k["t"],
                    open=float(k["o"]),
                    high=float(k["h"]),
                    low=float(k["l"]),
                    close=float(k["c"]),
                    volume=float(k["v"]),
                    closed=k["x"],  # True = svíčka právě uzavřena
                )

                # Aktualizuj buffer: nahraď poslední svíčku nebo přidej novou
                if buffer and buffer[-1].open_time == candle.open_time:
                    buffer[-1] = candle
                else:
                    buffer.append(candle)

                # Yield pouze uzavřené svíčky — ty mají finální data
                if candle.closed:
                    log.debug(f"Uzavřená svíčka: {candle.dt} | C:{candle.close:.0f}")
                    yield candle

        except websockets.ConnectionClosed:
            log.warning("WebSocket odpojen. Reconnectuji za 3s...")
            import asyncio
            await asyncio.sleep(3)
