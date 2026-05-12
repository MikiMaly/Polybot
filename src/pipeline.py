"""
pipeline.py — Jeden běh analýzy.

run_once():
  1. Check cooldown
  2. Snapshot bufferu → compute_indicators
  3. Fetch Polymarket (paralelně přes Gamma API)
  4. Strategy.decide(...)
  5. Fan-out signálu do sinků (chyby izolované)
  6. (volitelné) Paper trading: policy.evaluate → executor.execute

Cooldown se aktualizuje JEN po úspěšném signálu — když strategie selže,
cooldown zůstane, takže další iterace zkusí znovu.
"""

import logging
import time
from typing import List, Optional

import httpx

from config import Config
from indicators import compute_indicators
from models import CandleBuffer
from polymarket import fetch_updown_markets
from sinks import SignalSink
from strategy import Strategy
from trading import PaperExecutor, TradingPolicy

log = logging.getLogger(__name__)


class AnalysisPipeline:
    def __init__(
        self,
        config: Config,
        buffer: CandleBuffer,
        strategy: Strategy,
        sinks: List[SignalSink],
        http: httpx.AsyncClient,
        policy: Optional[TradingPolicy] = None,
        executor: Optional[PaperExecutor] = None,
    ):
        self.config = config
        self.buffer = buffer
        self.strategy = strategy
        self.sinks = sinks
        self.http = http
        self.policy = policy
        self.executor = executor
        self._last_signal_time: float = 0.0  # monotonic seconds

    async def run_once(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_signal_time
        if self._last_signal_time and elapsed < self.config.signal_cooldown_seconds:
            log.debug(f"Cooldown: {self.config.signal_cooldown_seconds - elapsed:.0f}s zbývá")
            return

        candles = self.buffer.snapshot()
        if not candles:
            log.debug("Buffer je prázdný, přeskakuji analýzu.")
            return

        indicators = compute_indicators(
            candles,
            ema_fast_period=self.config.ema_fast,
            ema_slow_period=self.config.ema_slow,
        )
        if not indicators.ready:
            log.info(f"⏳ Nedostatek dat pro indikátory ({len(candles)} svíček). Čekám...")
            return

        latest = candles[-1]
        log.info(
            f"📊 {latest.dt} UTC | Close: ${latest.close:,.0f} | "
            f"RSI: {indicators.rsi_14} | "
            f"EMA9/21: {indicators.ema_fast:.0f}/{indicators.ema_slow:.0f} | "
            f"Cross: {indicators.ema_cross} | Vol: {indicators.volume_ratio}x"
        )

        markets = await fetch_updown_markets(self.http)
        if markets:
            log.info(f"📈 Polymarket: {len(markets)} BTC trhů načteno")

        try:
            signal = await self.strategy.decide(candles, indicators, markets)
        except Exception as e:
            log.error(f"Strategie '{self.strategy.name}' selhala: {e}", exc_info=True)
            return

        # Cooldown nastavíme až po úspěšném signálu.
        self._last_signal_time = time.monotonic()

        # Fan-out do všech sinků — každý chráněný try/except.
        for sink in self.sinks:
            try:
                await sink.emit(signal)
            except Exception as e:
                log.error(f"Sink {type(sink).__name__} selhal: {e}", exc_info=True)

        # Paper trading: jen pokud máme policy + executor.
        if self.policy is not None and self.executor is not None:
            orders = self.policy.evaluate(signal, markets)
            for order in orders:
                try:
                    await self.executor.execute(order)
                except Exception as e:
                    log.error(f"Executor selhal pro {order.slug}: {e}", exc_info=True)
