"""
polymarket.py — Načítá aktuální BTC Up/Down trhy (5min a 15min).

Slug formát:
  btc-updown-5m-{unix_start}    — trh od start do start+300
  btc-updown-15m-{unix_start}   — trh od start do start+900

Strategie:
  Zkusí aktuální slot. Pokud Gamma API nevrátí event, fallback na předchozí slot.
  Při neúspěchu vrací prázdný list — strategie pak rozhoduje bez Polymarket dat.
"""

import asyncio
import json
import logging
import time
from typing import Any, List, Optional

import httpx

from models import PolymarketQuote

log = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


def _parse_outcome_prices(raw: Any) -> List[float]:
    """
    Gamma API vrací outcomePrices někdy jako list, jindy jako JSON string
    (např. '["0.505", "0.495"]'). Zde sjednotíme.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    out: List[float] = []
    for v in raw:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            return []
    return out


def _market_slug(interval_sec: int, offset: int = 0) -> str:
    now = int(time.time())
    start = (now // interval_sec) * interval_sec + offset
    label = "5m" if interval_sec == 300 else "15m"
    return f"btc-updown-{label}-{start}"


async def fetch_market_by_slug(client: httpx.AsyncClient, slug: str) -> Optional[PolymarketQuote]:
    """Načte jeden trh dle slugu. None = neexistuje / chyba.

    Veřejné API — používá Settler pro polling rezoluce pozic.
    Když je trh rezolvovaný, outcomePrices budou {0.0, 1.0} nebo {1.0, 0.0}.
    """
    try:
        r = await client.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=8.0)
        r.raise_for_status()
        events = r.json()
        if not events:
            return None
        event = events[0]
        markets = event.get("markets") or []
        if not markets:
            return None
        prices = _parse_outcome_prices(markets[0].get("outcomePrices"))
        if len(prices) >= 2:
            yes = round(prices[0], 3)
            no = round(prices[1], 3)
        else:
            yes = no = None
        return PolymarketQuote(
            slug=slug,
            question=event.get("title", slug),
            yes=yes,
            no=no,
            active=bool(event.get("active", True)),
        )
    except Exception as e:
        log.debug(f"Polymarket fetch failed ({slug}): {e}")
        return None


async def _try_slugs(client: httpx.AsyncClient, slugs: List[str]) -> Optional[PolymarketQuote]:
    for slug in slugs:
        m = await fetch_market_by_slug(client, slug)
        if m:
            return m
    return None


async def fetch_updown_markets(client: httpx.AsyncClient) -> List[PolymarketQuote]:
    """Vrátí list 5min a 15min BTC Up/Down quotes (max 2 prvky)."""
    slugs_5m = [_market_slug(300), _market_slug(300, -300)]
    slugs_15m = [_market_slug(900), _market_slug(900, -900)]

    quote_5m, quote_15m = await asyncio.gather(
        _try_slugs(client, slugs_5m),
        _try_slugs(client, slugs_15m),
    )
    return [q for q in (quote_5m, quote_15m) if q is not None]
