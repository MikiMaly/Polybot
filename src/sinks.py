"""
sinks.py — Kam se signál pošle po vygenerování.

Protocol SignalSink: jediná metoda `emit(signal)`. Pipeline fan-outuje
signál do všech sinků; chyby jsou izolované per-sink.

Implementace:
  ConsoleSink — pretty log do stdout/log file
  JsonlSink   — append-only JSONL na disk
  HubSink     — HTTP POST na dashboard (volitelné)
  TradeSink   — JSONL pro paper trading eventy (ORDER/FILL/SETTLED)
"""

import logging
from pathlib import Path
from typing import Protocol

import httpx

from models import Signal, TradeEvent

log = logging.getLogger(__name__)


ACTION_EMOJI = {
    "BUY_UP": "🟢",
    "BUY_DOWN": "🔴",
    "SKIP": "⚪",
}


class SignalSink(Protocol):
    async def emit(self, signal: Signal) -> None:
        ...


class ConsoleSink:
    """Pretty log do stdout (a log filu přes logging)."""

    async def emit(self, signal: Signal) -> None:
        emoji = ACTION_EMOJI.get(signal.action, "❓")
        log.info(
            f"{emoji} SIGNAL: {signal.action} | "
            f"Confidence: {signal.confidence:.0%} | "
            f"Price: ${signal.btc_price:,.0f}"
        )
        log.info(f"   💬 {signal.reasoning}")
        log.info(f"   📍 Market: {signal.suggested_market}")

        if signal.is_actionable:
            log.warning(
                f"⚡ AKČNÍ SIGNÁL ({signal.confidence:.0%}): "
                f"{signal.action} → {signal.suggested_market}"
            )


class JsonlSink:
    """Append-only JSONL persistence."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def emit(self, signal: Signal) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(signal.model_dump_json() + "\n")
        except OSError as e:
            log.error(f"JSONL write failed ({self.path}): {e}")


class TradeSink:
    """Append-only JSONL pro paper trading eventy (ORDER/FILL/SETTLED/REJECTED)."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def emit(self, event: TradeEvent) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        except OSError as e:
            log.error(f"TradeSink write failed ({self.path}): {e}")


class HubSink:
    """POST signálu na dashboard endpoint. Sdílený httpx.AsyncClient."""

    def __init__(self, url: str, secret: str, client: httpx.AsyncClient):
        self.url = url
        self.secret = secret
        self.client = client

    async def emit(self, signal: Signal) -> None:
        try:
            r = await self.client.post(
                self.url,
                json=signal.model_dump(),
                headers={"Authorization": f"Bearer {self.secret}"},
                timeout=10.0,
            )
            r.raise_for_status()
            log.debug("Hub push OK.")
        except Exception as e:
            log.warning(f"Hub push failed: {e}")
