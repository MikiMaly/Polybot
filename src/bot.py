"""
bot.py — Hlavní orchestrátor bota.

Logika životního cyklu:
  1. Načte historická data (feed.fetch_historical)
  2. Inicializuje buffer svíček (deque s maxlen=100)
  3. Spustí WebSocket stream + periodický analyzér souběžně
  4. Na každé uzavřené svíčce aktualizuje buffer
  5. Každých ~2 minuty (analysis_interval):
     a. Zkontroluje cooldown
     b. Vypočítá indikátory
     c. Načte Polymarket YES/NO ceny
     d. Zavolá OpenRouter (advisor.get_signal)
     e. Loguje signál + uloží JSONL + odešle na hub
"""

import asyncio
import json
import logging
import time
from collections import deque

import httpx

from config import Config
from feed import fetch_historical, stream_candles
from indicators import compute_indicators
from advisor import OpenRouterAdvisor
from polymarket import fetch_updown_markets
from models import Signal

log = logging.getLogger(__name__)

ACTION_EMOJI = {
    "BUY_UP":   "🟢",
    "BUY_DOWN": "🔴",
    "SKIP":     "⚪",
}


class SignalBot:
    def __init__(self, config: Config):
        self.config = config
        self.advisor = OpenRouterAdvisor(config)
        self.buffer: deque = deque(maxlen=100)
        self._last_signal_time: float = 0
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=10)
        self._analysis_task: asyncio.Task | None = None

    async def run(self):
        """Hlavní smyčka bota — stream svíček + periodický analyzér."""
        log.info("=" * 60)
        log.info("BTC Polymarket Signal Bot — START")
        log.info(f"Model: {self.config.openrouter_model}")
        log.info(f"Min confidence: {self.config.min_confidence:.0%}")
        log.info(f"Analýza každých: {self.config.analysis_interval}s")
        log.info("=" * 60)

        historical = await fetch_historical(self.config)
        self.buffer.extend(historical)

        self._analysis_task = asyncio.create_task(self._periodic_loop())

        try:
            async for _ in stream_candles(self.config, self.buffer):
                pass  # buffer aktualizuje stream_candles sám
        finally:
            await self._http.aclose()

    async def _periodic_loop(self):
        """Spouští analýzu každých analysis_interval sekund."""
        await asyncio.sleep(self.config.analysis_interval)  # počkej na naplnění bufferu
        while True:
            await self._run_analysis()
            await asyncio.sleep(self.config.analysis_interval)

    async def _run_analysis(self):
        """Provede jednu analýzu — indikátory + Polymarket + AI signál."""
        now = time.time()
        elapsed = now - self._last_signal_time
        if elapsed < self.config.signal_cooldown:
            log.debug(f"Cooldown: {self.config.signal_cooldown - elapsed:.0f}s zbývá")
            return

        candles = list(self.buffer)
        if not candles:
            return

        ind = compute_indicators(candles, self.config.ema_fast, self.config.ema_slow)
        if not ind.ready:
            log.info(f"⏳ Nedostatek dat pro indikátory ({len(candles)} svíček). Čekám...")
            return

        latest = candles[-1]
        log.info(
            f"📊 {latest.dt} UTC | Close: ${latest.close:,.0f} | "
            f"RSI: {ind.rsi_14} | EMA9/21: {ind.ema_fast:.0f}/{ind.ema_slow:.0f} | "
            f"Cross: {ind.ema_cross} | Vol: {ind.volume_ratio}x"
        )

        poly_markets = await fetch_updown_markets()
        if poly_markets:
            log.info(f"📈 Polymarket: {len(poly_markets)} BTC trhů načteno")

        try:
            signal = await self.advisor.get_signal(candles, ind, poly_markets)
            self._last_signal_time = now
            self._log_signal(signal)
            self._save_signal(signal)
            await self._push_signal(signal)

        except Exception as e:
            log.error(f"Neočekávaná chyba: {e}", exc_info=True)

    def _log_signal(self, signal: Signal):
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

    def _save_signal(self, signal: Signal):
        try:
            with open(self.config.signals_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(signal.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            log.error(f"Nepodařilo se zapsat signál: {e}")

    async def _push_signal(self, signal: Signal):
        if not self.config.hub_api_url or not self.config.hub_bot_secret:
            return
        try:
            r = await self._http.post(
                self.config.hub_api_url,
                json=signal.to_dict(),
                headers={"Authorization": f"Bearer {self.config.hub_bot_secret}"},
            )
            r.raise_for_status()
            log.debug("Signál odeslán na hub.")
        except Exception as e:
            log.warning(f"Nepodařilo se odeslat signál na hub: {e}")
