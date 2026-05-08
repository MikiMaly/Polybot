"""
polymarket.py — Načítá aktuální BTC Up/Down trhy (5min a 15min) z Polymarket.

Slug format: btc-updown-5m-{unix_start_timestamp}
             btc-updown-15m-{unix_start_timestamp}

Příklad: btc-updown-5m-1778255700  = trh od 15:55 do 16:00 UTC
         btc-updown-15m-1778255100 = trh od 15:45 do 16:00 UTC
"""

import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


def _market_slug(interval_sec: int, offset: int = 0) -> str:
    """Vrátí slug pro aktuálně aktivní BTC Up/Down trh daného intervalu."""
    now = int(time.time())
    start = (now // interval_sec) * interval_sec + offset
    label = "5m" if interval_sec == 300 else "15m"
    return f"btc-updown-{label}-{start}"


async def _fetch_market(slug: str) -> dict | None:
    """Načte jeden trh podle slugu. Vrátí None pokud neexistuje nebo selže."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{GAMMA_API}/events", params={"slug": slug})
            r.raise_for_status()
            events = r.json()
            if not events:
                return None
            event = events[0]
            markets = event.get("markets", [])
            if not markets:
                return None
            market = markets[0]
            prices = market.get("outcomePrices", [])
            try:
                yes = round(float(prices[0]), 3)
                no  = round(float(prices[1]), 3)
            except (ValueError, IndexError, TypeError):
                yes = no = None
            return {
                "slug":     slug,
                "question": event.get("title", slug),
                "yes":      yes,
                "no":       no,
                "active":   event.get("active", True),
            }
    except Exception as e:
        log.debug(f"Polymarket fetch selhal ({slug}): {e}")
        return None


async def fetch_updown_markets() -> list[dict]:
    """
    Načte aktuální 5min a 15min BTC Up/Down trhy.
    Pokud aktuální neexistuje, zkusí předchozí periodu.
    """
    slugs_5m  = [_market_slug(300), _market_slug(300, -300)]    # aktuální nebo předchozí
    slugs_15m = [_market_slug(900), _market_slug(900, -900)]

    results_5m, results_15m = await asyncio.gather(
        _try_slugs(slugs_5m),
        _try_slugs(slugs_15m),
    )

    markets = [m for m in [results_5m, results_15m] if m is not None]
    return markets


async def _try_slugs(slugs: list[str]) -> dict | None:
    """Zkusí slugy jeden po druhém, vrátí první úspěšný."""
    for slug in slugs:
        m = await _fetch_market(slug)
        if m:
            return m
    return None
