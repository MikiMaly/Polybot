#!/usr/bin/env python3
"""
BTC Polymarket Signal Bot — Entry point
Spusť: python src/main.py
"""

import asyncio
import logging
import sys
from dotenv import load_dotenv
from bot import SignalBot
from config import Config

load_dotenv()

# Windows: vynutit UTF-8 pro konzoli i log soubor
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
    ],
)

if __name__ == "__main__":
    config = Config.from_env()
    bot = SignalBot(config)
    asyncio.run(bot.run())
