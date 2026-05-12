"""
trading.py — Paper trading vrstva.

Komponenty:
  Portfolio       — stav balance + otevřených pozic, persistence do JSON
  TradingPolicy   — Signal → list[TradeOrder] (filtruje a sizuje)
  PaperExecutor   — simuluje immediate fill za tržní cenu
  Settler         — async loop, pollne Gamma a uzavírá rezolvované pozice

Mapování signálu na trade:
  BUY_UP   → buy YES na btc-updown-{interval}
  BUY_DOWN → buy NO  na btc-updown-{interval}
  SKIP     → nic

P/L kalkulace (binární trh, payout 1.00):
  Vyhraná: pnl = size_usd * (1.0 - entry_price) / entry_price
  Prohraná: pnl = -size_usd
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Protocol

import httpx

from config import Config
from models import (
    Outcome, Position, PolymarketQuote, Signal, TradeEvent, TradeOrder,
)
from polymarket import fetch_market_by_slug

log = logging.getLogger(__name__)


class TradeEventSink(Protocol):
    """Cíl pro paper trading eventy (ORDER/FILL/SETTLED/REJECTED)."""
    async def emit(self, event: TradeEvent) -> None:
        ...


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _is_resolved(quote: PolymarketQuote) -> bool:
    """Trh je rezolvovaný, pokud outcomePrices jsou {0.0, 1.0} nebo {1.0, 0.0}."""
    if quote.yes is None or quote.no is None:
        return False
    # tolerance — někdy Polymarket vrátí 0.99 / 0.01 těsně před finalizací
    return (quote.yes >= 0.99 and quote.no <= 0.01) or (quote.yes <= 0.01 and quote.no >= 0.99)


def _market_interval_from_slug(slug: str) -> str:
    """btc-updown-5m-1778607000 → '5m'."""
    parts = slug.split("-")
    return parts[2] if len(parts) >= 3 else "?"


# ---------- Portfolio ----------

class Portfolio:
    """
    In-memory state + persistence do JSON.
    Single-threaded přístup (volá se z asyncio event loopu).
    """

    def __init__(self, initial_balance: float, persist_path: str):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.positions: dict[str, Position] = {}
        self.realized_pnl: float = 0.0
        self._path = Path(persist_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            log.info(f"Portfolio: nový start, balance ${self.initial_balance:.2f}")
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self.balance = raw["balance"]
            self.realized_pnl = raw.get("realized_pnl", 0.0)
            self.positions = {
                pid: Position.model_validate(p) for pid, p in raw.get("positions", {}).items()
            }
            n_open = sum(1 for p in self.positions.values() if p.is_open)
            log.info(
                f"Portfolio načteno: balance ${self.balance:.2f}, "
                f"{n_open} otevřených pozic, realized P/L ${self.realized_pnl:+.2f}"
            )
        except Exception as e:
            log.error(f"Portfolio načtení selhalo ({self._path}): {e}. Začínám čistě.")
            self.balance = self.initial_balance
            self.positions = {}
            self.realized_pnl = 0.0

    def save(self) -> None:
        data = {
            "initial_balance": self.initial_balance,
            "balance": self.balance,
            "realized_pnl": self.realized_pnl,
            "positions": {pid: p.model_dump() for pid, p in self.positions.items()},
            "saved_at": _now_utc_iso(),
        }
        try:
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            log.error(f"Portfolio save selhalo: {e}")

    def open_positions(self) -> List[Position]:
        return [p for p in self.positions.values() if p.is_open]

    def has_open_position_on_slug(self, slug: str) -> bool:
        return any(p.slug == slug and p.is_open for p in self.positions.values())

    def open(self, order: TradeOrder) -> Optional[Position]:
        """Otevře pozici. Vrátí None pokud nedostatek balance."""
        if self.balance < order.size_usd:
            log.warning(
                f"Portfolio: nedostatek balance pro {order.slug} "
                f"(${self.balance:.2f} < ${order.size_usd:.2f})"
            )
            return None
        pos = Position(
            id=str(uuid.uuid4()),
            slug=order.slug,
            market_interval=order.market_interval,
            question=order.question,
            outcome=order.outcome,
            size_usd=order.size_usd,
            entry_price=order.limit_price,
            entry_time=_now_utc_iso(),
            signal_action=order.signal_action,
            signal_confidence=order.signal_confidence,
        )
        self.balance -= order.size_usd
        self.positions[pos.id] = pos
        self.save()
        return pos

    def settle(self, position_id: str, resolution_price: float) -> Optional[Position]:
        """
        Uzavře pozici. resolution_price je tržní cena VYBRANÉHO outcome (YES nebo NO)
        v okamžiku rezoluce — měla by být 1.0 (vyhráli jsme) nebo 0.0 (prohráli).
        """
        pos = self.positions.get(position_id)
        if pos is None or not pos.is_open:
            return None

        # Binární payout: získáme size_usd * (resolution_price / entry_price) zpět.
        # Když resolution_price = 1.0 a entry_price = 0.55: dostaneme 1/0.55 = 1.82x.
        # Když resolution_price = 0.0: dostaneme 0.
        payout = pos.size_usd * (resolution_price / pos.entry_price) if pos.entry_price > 0 else 0.0
        pnl = payout - pos.size_usd

        pos.status = "WON" if resolution_price >= 0.5 else "LOST"
        pos.resolution_price = resolution_price
        pos.resolution_time = _now_utc_iso()
        pos.pnl_usd = round(pnl, 4)

        self.balance += payout
        self.realized_pnl += pnl
        self.save()
        return pos

    def summary(self) -> dict:
        n_open = sum(1 for p in self.positions.values() if p.is_open)
        n_won = sum(1 for p in self.positions.values() if p.status == "WON")
        n_lost = sum(1 for p in self.positions.values() if p.status == "LOST")
        n_closed = n_won + n_lost
        win_rate = (n_won / n_closed) if n_closed > 0 else 0.0
        return {
            "balance": round(self.balance, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "open_positions": n_open,
            "closed_positions": n_closed,
            "won": n_won,
            "lost": n_lost,
            "win_rate": round(win_rate, 3),
        }


# ---------- TradingPolicy ----------

class TradingPolicy:
    """
    Rozhoduje, jestli Signal → jeden nebo víc TradeOrder.

    Filtry (v pořadí):
      1. action == SKIP → no orders
      2. confidence < paper_min_confidence_to_trade → no orders
      3. open_positions >= max_open_positions → no orders
      4. pro každý cílový market interval (5m, 15m):
         - quote z markets musí existovat a mít validní YES/NO
         - nesmíme už mít otevřenou pozici na tomto slugu
         - cena nesmí být extrémní (slug ne-resolved)
    """

    def __init__(self, config: Config, portfolio: Portfolio):
        self.config = config
        self.portfolio = portfolio

    def evaluate(self, signal: Signal, markets: List[PolymarketQuote]) -> List[TradeOrder]:
        if signal.action == "SKIP":
            return []
        if signal.confidence < self.config.paper_min_confidence_to_trade:
            log.info(
                f"📝 Policy: skip — confidence {signal.confidence:.0%} < "
                f"{self.config.paper_min_confidence_to_trade:.0%}"
            )
            return []

        n_open = len(self.portfolio.open_positions())
        if n_open >= self.config.paper_max_open_positions:
            log.warning(
                f"📝 Policy: skip — {n_open} otevřených pozic >= limitu "
                f"{self.config.paper_max_open_positions}"
            )
            return []

        outcome: Outcome = "YES" if signal.action == "BUY_UP" else "NO"
        orders: List[TradeOrder] = []
        # Sběr důvodů, proč jsme něco neobchodovali — pro INFO log výše.
        skips: list[str] = []

        for q in markets:
            interval = _market_interval_from_slug(q.slug)
            if interval not in self.config.paper_target_markets:
                skips.append(f"{interval} mimo target_markets")
                continue
            if q.yes is None or q.no is None:
                skips.append(f"{q.slug} bez cen")
                continue
            if self.portfolio.has_open_position_on_slug(q.slug):
                skips.append(f"{q.slug} už má otevřenou pozici")
                continue
            price = q.yes if outcome == "YES" else q.no
            if price <= 0.0 or price >= 1.0:
                skips.append(f"{interval} {outcome}={price:.2f} extrémní")
                continue

            orders.append(TradeOrder(
                slug=q.slug,
                market_interval=interval,
                question=q.question,
                outcome=outcome,
                size_usd=self.config.paper_position_size_usd,
                limit_price=price,
                signal_action=signal.action,
                signal_confidence=signal.confidence,
            ))

        if not orders and skips:
            log.info(f"📝 Policy: 0 orders ({signal.action} {signal.confidence:.0%}) — " + "; ".join(skips))
        elif orders:
            log.info(
                f"📝 Policy: {len(orders)} order(s) pro {signal.action} {signal.confidence:.0%}"
            )
        return orders


# ---------- PaperExecutor ----------

class PaperExecutor:
    """Simuluje immediate fill za tržní cenu. Žádný slippage, žádné fees."""

    def __init__(self, portfolio: Portfolio, trade_sink: TradeEventSink):
        self.portfolio = portfolio
        self.trade_sink = trade_sink

    async def execute(self, order: TradeOrder) -> Optional[Position]:
        pos = self.portfolio.open(order)
        if pos is None:
            await self._emit("REJECTED", "rejected-" + order.slug, {
                "reason": "insufficient_balance_or_other",
                "order": order.model_dump(),
            })
            return None

        await self._emit("FILL", pos.id, {
            "slug": pos.slug,
            "interval": pos.market_interval,
            "outcome": pos.outcome,
            "size_usd": pos.size_usd,
            "entry_price": pos.entry_price,
            "signal_action": pos.signal_action,
            "signal_confidence": pos.signal_confidence,
            "question": pos.question,
        })
        log.info(
            f"💰 OPEN {pos.market_interval} {pos.outcome} @ {pos.entry_price:.3f} "
            f"size=${pos.size_usd:.2f} — {pos.question}"
        )
        return pos

    async def _emit(self, event_type: str, position_id: str, detail: dict) -> None:
        ev = TradeEvent(
            event=event_type,  # type: ignore[arg-type]
            timestamp=_now_utc_iso(),
            position_id=position_id,
            detail=detail,
        )
        try:
            await self.trade_sink.emit(ev)
        except Exception as e:
            log.error(f"TradeSink emit selhal: {e}")


# ---------- Settler ----------

class Settler:
    """
    Background task: každých `settler_interval_seconds` se podívá na otevřené
    pozice a uzavře ty rezolvované.

    Rezoluce detekce: pollne Gamma API pro slug, pokud outcomePrices ~ {0,1}
    nebo {1,0}, pozice se vyhodnotí.
    """

    def __init__(
        self,
        config: Config,
        portfolio: Portfolio,
        http: httpx.AsyncClient,
        trade_sink: TradeEventSink,
    ):
        self.config = config
        self.portfolio = portfolio
        self.http = http
        self.trade_sink = trade_sink

    async def run(self, stop_event: asyncio.Event) -> None:
        log.info(f"Settler start (interval {self.config.paper_settler_interval_seconds}s).")
        # nech systém naběhnout
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=self.config.paper_settler_interval_seconds,
            )
            return
        except asyncio.TimeoutError:
            pass

        while not stop_event.is_set():
            try:
                await self._settle_round()
            except Exception as e:
                log.error(f"Settler round selhal: {e}", exc_info=True)

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.config.paper_settler_interval_seconds,
                )
                return
            except asyncio.TimeoutError:
                continue

    async def _settle_round(self) -> None:
        opens = self.portfolio.open_positions()
        if not opens:
            return
        log.debug(f"Settler: kontroluji {len(opens)} otevřených pozic.")

        # Skupinujeme dotazy podle slugu (vícero pozic může sdílet slug,
        # i když policy to teď zakazuje).
        slugs = list({p.slug for p in opens})
        results = await asyncio.gather(
            *(fetch_market_by_slug(self.http, s) for s in slugs),
            return_exceptions=True,
        )
        quotes_by_slug: dict[str, PolymarketQuote] = {}
        for slug, res in zip(slugs, results):
            if isinstance(res, PolymarketQuote):
                quotes_by_slug[slug] = res

        for pos in opens:
            q = quotes_by_slug.get(pos.slug)
            if q is None:
                continue
            if not _is_resolved(q):
                continue
            # YES vyhrálo, pokud q.yes ~= 1.0
            yes_won = q.yes is not None and q.yes >= 0.99
            resolution_price = (
                1.0 if (pos.outcome == "YES" and yes_won) or (pos.outcome == "NO" and not yes_won)
                else 0.0
            )
            settled = self.portfolio.settle(pos.id, resolution_price)
            if settled is None:
                continue

            await self._emit("SETTLED", settled.id, {
                "slug": settled.slug,
                "interval": settled.market_interval,
                "outcome": settled.outcome,
                "size_usd": settled.size_usd,
                "entry_price": settled.entry_price,
                "resolution_price": settled.resolution_price,
                "pnl_usd": settled.pnl_usd,
                "status": settled.status,
                "signal_action": settled.signal_action,
                "signal_confidence": settled.signal_confidence,
            })
            emoji = "✅" if settled.status == "WON" else "❌"
            log.info(
                f"{emoji} SETTLE {settled.market_interval} {settled.outcome} "
                f"@ entry {settled.entry_price:.3f} → {settled.status} "
                f"P/L ${settled.pnl_usd:+.2f}"
            )

        summary = self.portfolio.summary()
        log.info(
            f"💼 Portfolio: balance ${summary['balance']} | "
            f"realized P/L ${summary['realized_pnl']:+.2f} | "
            f"win rate {summary['win_rate']:.1%} ({summary['won']}/{summary['closed_positions']}) | "
            f"open {summary['open_positions']}"
        )

    async def _emit(self, event_type: str, position_id: str, detail: dict) -> None:
        ev = TradeEvent(
            event=event_type,  # type: ignore[arg-type]
            timestamp=_now_utc_iso(),
            position_id=position_id,
            detail=detail,
        )
        try:
            await self.trade_sink.emit(ev)
        except Exception as e:
            log.error(f"TradeSink emit selhal: {e}")
