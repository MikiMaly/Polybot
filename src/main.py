#!/usr/bin/env python3
"""
BTC Polymarket Signal Bot — entry point.

Spuštění:  python src/main.py

Wiring:
  Config (Pydantic Settings)
  → sdílený httpx.AsyncClient
    → CandleBuffer
    → AIAdvisor (Strategy)
    → Sinks: Console + JSONL + (Hub volitelně)
    → (Paper trading: Portfolio + TradingPolicy + PaperExecutor + Settler)
      → AnalysisPipeline
        → SignalBot
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

from bot import SignalBot
from config import Config
from models import CandleBuffer
from pipeline import AnalysisPipeline
from sinks import ConsoleSink, HubSink, JsonlSink, SignalSink, TradeSink
from strategy import AIAdvisor
from trading import PaperExecutor, Portfolio, Settler, TradingPolicy

load_dotenv()

# Windows: vynutit UTF-8 (kvůli emoji v lozích).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# logs/ adresář musí existovat před FileHandlerem.
Path("logs").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
    ],
)

log = logging.getLogger(__name__)


def _build_sinks(config: Config, http: httpx.AsyncClient) -> list[SignalSink]:
    sinks: list[SignalSink] = [
        ConsoleSink(),
        JsonlSink(config.signals_log),
    ]
    if config.hub_enabled:
        log.info(f"Hub sink aktivní: {config.hub_api_url}")
        sinks.append(HubSink(config.hub_api_url, config.hub_bot_secret, http))
    else:
        log.info("Hub sink vypnut (HUB_API_URL / HUB_BOT_SECRET nejsou nastaveny).")
    return sinks


async def main() -> None:
    config = Config()  # type: ignore[call-arg]  # pydantic-settings načte z env

    async with httpx.AsyncClient() as http:
        buffer = CandleBuffer(maxlen=config.buffer_size)
        strategy = AIAdvisor(config)
        sinks = _build_sinks(config, http)

        # Paper trading vrstva (volitelná — zapnutá přes config.paper_trading_enabled).
        policy = None
        executor = None
        settler = None
        if config.paper_trading_enabled:
            portfolio = Portfolio(
                initial_balance=config.paper_initial_balance,
                persist_path=config.paper_portfolio_path,
            )
            trade_sink = TradeSink(config.paper_trades_log)
            policy = TradingPolicy(config=config, portfolio=portfolio)
            executor = PaperExecutor(portfolio=portfolio, trade_sink=trade_sink)
            settler = Settler(
                config=config, portfolio=portfolio, http=http, trade_sink=trade_sink,
            )
            log.info(
                f"📝 Paper trading: balance ${config.paper_initial_balance:.2f}, "
                f"size ${config.paper_position_size_usd:.2f}/trade, "
                f"markets={config.paper_target_markets}, "
                f"min_conf={config.paper_min_confidence_to_trade:.0%}"
            )
        else:
            log.info("Paper trading vypnuto (paper_trading_enabled=False).")

        pipeline = AnalysisPipeline(
            config=config,
            buffer=buffer,
            strategy=strategy,
            sinks=sinks,
            http=http,
            policy=policy,
            executor=executor,
        )
        bot = SignalBot(
            config=config, pipeline=pipeline, buffer=buffer, http=http, settler=settler,
        )

        # Graceful shutdown — SIGINT/SIGTERM dají bot.request_stop().
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, bot.request_stop)
            except NotImplementedError:
                # Windows: add_signal_handler není pro SIGTERM dostupný.
                # Pro SIGINT se používá default handler (KeyboardInterrupt níže).
                pass

        try:
            await bot.run()
        except KeyboardInterrupt:
            log.info("Ctrl+C — ukončuji.")
            bot.request_stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
