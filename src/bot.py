"""
bot.py — Orchestrátor lifecycle.

Zodpovědnosti:
  - REST backfill historie do bufferu
  - tři souběžné tasky: feed stream + periodická analýza + (volitelné) settler
  - graceful shutdown přes asyncio.Event
  - propagace chyb (pokud jeden task umře, ostatní jsou cancelovány)

Vše ostatní (indikátory, AI volání, persistence, trading) řeší AnalysisPipeline
a Settler.
"""

import asyncio
import logging
from typing import Optional, Set

import httpx

from config import Config
from feed import fetch_historical, stream_candles
from models import CandleBuffer
from pipeline import AnalysisPipeline
from trading import Settler

log = logging.getLogger(__name__)


class SignalBot:
    def __init__(
        self,
        config: Config,
        pipeline: AnalysisPipeline,
        buffer: CandleBuffer,
        http: httpx.AsyncClient,
        settler: Optional[Settler] = None,
    ):
        self.config = config
        self.pipeline = pipeline
        self.buffer = buffer
        self.http = http
        self.settler = settler
        self._stop = asyncio.Event()
        self._tasks: Set[asyncio.Task] = set()

    def request_stop(self) -> None:
        """Graceful shutdown — feed i analýza dokončí aktuální iteraci."""
        log.info("Stop požadován, ukončuji...")
        self._stop.set()

    async def run(self) -> None:
        log.info("=" * 60)
        log.info("BTC Polymarket Signal Bot — START")
        log.info(f"  Strategy:  {self.pipeline.strategy.name}")
        log.info(f"  Model:     {self.config.openrouter_model}")
        log.info(f"  Min conf:  {self.config.min_confidence:.0%}")
        log.info(f"  Interval:  {self.config.analysis_interval_seconds}s")
        log.info(f"  Symbol:    {self.config.symbol} {self.config.interval}")
        log.info("=" * 60)

        historical = await fetch_historical(self.config, self.http)
        self.buffer.extend(historical)

        feed_task = asyncio.create_task(self._feed_loop(), name="feed")
        analysis_task = asyncio.create_task(self._analysis_loop(), name="analysis")
        self._tasks = {feed_task, analysis_task}

        if self.settler is not None:
            settler_task = asyncio.create_task(
                self.settler.run(self._stop), name="settler"
            )
            self._tasks.add(settler_task)

        try:
            done, pending = await asyncio.wait(
                self._tasks, return_when=asyncio.FIRST_EXCEPTION
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            for t in done:
                exc = t.exception()
                if exc:
                    log.error(f"Task {t.get_name()} skončil s chybou: {exc}", exc_info=exc)
                    raise exc
        finally:
            self._tasks.clear()

    async def _feed_loop(self) -> None:
        """Konzumuje WS stream a aktualizuje buffer."""
        try:
            async for candle in stream_candles(self.config):
                if self._stop.is_set():
                    return
                self.buffer.update(candle)
                if candle.closed:
                    log.debug(f"Uzavřená svíčka: {candle.dt} | C:{candle.close:.0f}")
        except asyncio.CancelledError:
            log.debug("Feed loop cancelled.")
            raise

    async def _analysis_loop(self) -> None:
        """Spouští pipeline.run_once() v intervalu."""
        # Nech buffer naplnit alespoň jedním cyklem než spustíme první analýzu.
        try:
            await asyncio.wait_for(
                self._stop.wait(),
                timeout=self.config.analysis_interval_seconds,
            )
            return  # stop signal během čekání
        except asyncio.TimeoutError:
            pass

        while not self._stop.is_set():
            try:
                await self.pipeline.run_once()
            except Exception as e:
                log.error(f"Pipeline run_once selhal: {e}", exc_info=True)

            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.config.analysis_interval_seconds,
                )
                return  # stop signal mezi cykly
            except asyncio.TimeoutError:
                continue
