"""
Testy pro paper trading vrstvu — Portfolio + TradingPolicy + PaperExecutor.

Spuštění:  python tests/test_trading.py
"""

import asyncio
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Windows: stdout je defaultně cp1250 — kvůli šipkám reconfigurujem na UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from models import (  # noqa: E402
    PolymarketQuote, Position, Signal, TradeEvent, TradeOrder,
)
from trading import (  # noqa: E402
    PaperExecutor, Portfolio, TradingPolicy, _is_resolved, _market_interval_from_slug,
)


FAILURES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        global FAILURES
        FAILURES += 1


# ---------- helpers ----------

class FakeConfig:
    paper_initial_balance = 100.0
    paper_position_size_usd = 1.0
    paper_max_open_positions = 10
    paper_min_confidence_to_trade = 0.62
    paper_target_markets = ["5m", "15m"]


class RecordingSink:
    def __init__(self):
        self.events: list[TradeEvent] = []

    async def emit(self, event: TradeEvent) -> None:
        self.events.append(event)


def _make_signal(action: str = "BUY_UP", confidence: float = 0.75) -> Signal:
    return Signal(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        btc_price=80_000.0,
        action=action,  # type: ignore[arg-type]
        confidence=confidence,
        reasoning="test",
        suggested_market="test",
        indicators={},
        strategy="stub",
    )


def _quote(slug: str, yes: float, no: float, question: str = "Q?") -> PolymarketQuote:
    return PolymarketQuote(slug=slug, question=question, yes=yes, no=no, active=True)


# ---------- pure helpers ----------

def test_market_interval_from_slug():
    print("\ntest_market_interval_from_slug")
    check("5m extrakce", _market_interval_from_slug("btc-updown-5m-12345") == "5m")
    check("15m extrakce", _market_interval_from_slug("btc-updown-15m-67890") == "15m")
    check("nepoznané vrátí '?'", _market_interval_from_slug("garbage") == "?")


def test_is_resolved():
    print("\ntest_is_resolved")
    check("YES 1.0 / NO 0.0 → resolved", _is_resolved(_quote("s", 1.0, 0.0)))
    check("YES 0.0 / NO 1.0 → resolved", _is_resolved(_quote("s", 0.0, 1.0)))
    check("YES 0.99 / NO 0.01 → resolved (tolerance)", _is_resolved(_quote("s", 0.99, 0.01)))
    check("YES 0.55 / NO 0.45 → not resolved", _is_resolved(_quote("s", 0.55, 0.45)) is False)
    check("None → not resolved", _is_resolved(_quote("s", None, None)) is False)  # type: ignore[arg-type]


# ---------- Portfolio ----------

def test_portfolio_open_position():
    print("\ntest_portfolio_open_position")
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "portfolio.json")
        p = Portfolio(initial_balance=100.0, persist_path=path)
        order = TradeOrder(
            slug="btc-updown-5m-1", market_interval="5m", question="Q",
            outcome="YES", size_usd=1.0, limit_price=0.55,
            signal_action="BUY_UP", signal_confidence=0.7,
        )
        pos = p.open(order)
        check("Pozice vytvořena", pos is not None and pos.is_open)
        check("Balance odečteno", p.balance == 99.0, f"got {p.balance}")
        check("1 otevřená pozice", len(p.open_positions()) == 1)
        check("Soubor zapsán", Path(path).exists())


def test_portfolio_insufficient_balance():
    print("\ntest_portfolio_insufficient_balance")
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "portfolio.json")
        p = Portfolio(initial_balance=0.50, persist_path=path)
        order = TradeOrder(
            slug="s", market_interval="5m", question="Q",
            outcome="YES", size_usd=1.0, limit_price=0.5,
            signal_action="BUY_UP", signal_confidence=0.7,
        )
        pos = p.open(order)
        check("Pozice odmítnuta", pos is None)
        check("Balance nezměněno", p.balance == 0.50)


def test_portfolio_settle_win():
    print("\ntest_portfolio_settle_win")
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "p.json")
        p = Portfolio(initial_balance=100.0, persist_path=path)
        order = TradeOrder(
            slug="s", market_interval="5m", question="Q",
            outcome="YES", size_usd=1.0, limit_price=0.50,
            signal_action="BUY_UP", signal_confidence=0.7,
        )
        pos = p.open(order)
        assert pos is not None
        # Vyhráli jsme — resolution_price = 1.0
        # Payout = 1.0 * (1.0 / 0.50) = 2.0 → pnl = 2.0 - 1.0 = +1.0
        settled = p.settle(pos.id, 1.0)
        check("Stav WON", settled is not None and settled.status == "WON")
        check("P/L = +1.00", settled is not None and abs(settled.pnl_usd - 1.0) < 0.01,
              f"got {settled.pnl_usd if settled else None}")
        check("Balance vzrostlo", abs(p.balance - 101.0) < 0.01, f"got {p.balance}")
        check("realized_pnl +1.00", abs(p.realized_pnl - 1.0) < 0.01)


def test_portfolio_settle_loss():
    print("\ntest_portfolio_settle_loss")
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "p.json")
        p = Portfolio(initial_balance=100.0, persist_path=path)
        order = TradeOrder(
            slug="s", market_interval="5m", question="Q",
            outcome="YES", size_usd=1.0, limit_price=0.40,
            signal_action="BUY_UP", signal_confidence=0.7,
        )
        pos = p.open(order)
        assert pos is not None
        settled = p.settle(pos.id, 0.0)
        check("Stav LOST", settled is not None and settled.status == "LOST")
        check("P/L = -1.00", settled is not None and abs(settled.pnl_usd + 1.0) < 0.01)
        check("Balance vrátilo na 99", abs(p.balance - 99.0) < 0.01)


def test_portfolio_persistence():
    print("\ntest_portfolio_persistence")
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "p.json")
        p1 = Portfolio(initial_balance=100.0, persist_path=path)
        order = TradeOrder(
            slug="abc", market_interval="5m", question="Q",
            outcome="YES", size_usd=1.0, limit_price=0.55,
            signal_action="BUY_UP", signal_confidence=0.7,
        )
        p1.open(order)

        # Druhý Portfolio načte stejný soubor
        p2 = Portfolio(initial_balance=999.0, persist_path=path)
        check("Balance načteno", abs(p2.balance - 99.0) < 0.01, f"got {p2.balance}")
        check("Pozice načtena", len(p2.open_positions()) == 1)
        check("Slug zachován", p2.open_positions()[0].slug == "abc")


# ---------- TradingPolicy ----------

def test_policy_skips_skip_action():
    print("\ntest_policy_skips_skip_action")
    with tempfile.TemporaryDirectory() as d:
        p = Portfolio(initial_balance=100.0, persist_path=str(Path(d) / "p.json"))
        policy = TradingPolicy(FakeConfig(), p)
        orders = policy.evaluate(
            _make_signal(action="SKIP", confidence=0.9),
            [_quote("btc-updown-5m-1", 0.5, 0.5)],
        )
        check("SKIP signal → no orders", orders == [])


def test_policy_skips_low_confidence():
    print("\ntest_policy_skips_low_confidence")
    with tempfile.TemporaryDirectory() as d:
        p = Portfolio(initial_balance=100.0, persist_path=str(Path(d) / "p.json"))
        policy = TradingPolicy(FakeConfig(), p)
        orders = policy.evaluate(
            _make_signal(action="BUY_UP", confidence=0.50),
            [_quote("btc-updown-5m-1", 0.5, 0.5)],
        )
        check("Confidence 0.50 < 0.62 → no orders", orders == [])


def test_policy_buy_up_yes_5m_and_15m():
    print("\ntest_policy_buy_up_yes_5m_and_15m")
    with tempfile.TemporaryDirectory() as d:
        p = Portfolio(initial_balance=100.0, persist_path=str(Path(d) / "p.json"))
        policy = TradingPolicy(FakeConfig(), p)
        orders = policy.evaluate(
            _make_signal(action="BUY_UP", confidence=0.75),
            [
                _quote("btc-updown-5m-1", 0.55, 0.45),
                _quote("btc-updown-15m-1", 0.60, 0.40),
            ],
        )
        check("Vzniknou 2 orders (5m + 15m)", len(orders) == 2, f"got {len(orders)}")
        check("Obě YES", all(o.outcome == "YES" for o in orders))
        check("5m za 0.55", any(o.market_interval == "5m" and o.limit_price == 0.55 for o in orders))
        check("15m za 0.60", any(o.market_interval == "15m" and o.limit_price == 0.60 for o in orders))


def test_policy_buy_down_uses_no_price():
    print("\ntest_policy_buy_down_uses_no_price")
    with tempfile.TemporaryDirectory() as d:
        p = Portfolio(initial_balance=100.0, persist_path=str(Path(d) / "p.json"))
        policy = TradingPolicy(FakeConfig(), p)
        orders = policy.evaluate(
            _make_signal(action="BUY_DOWN", confidence=0.7),
            [_quote("btc-updown-5m-1", 0.7, 0.3)],
        )
        check("Outcome NO", orders[0].outcome == "NO")
        check("Limit price = NO price (0.30)", orders[0].limit_price == 0.30)


def test_policy_skips_duplicate_slug():
    print("\ntest_policy_skips_duplicate_slug")
    with tempfile.TemporaryDirectory() as d:
        p = Portfolio(initial_balance=100.0, persist_path=str(Path(d) / "p.json"))
        # Otevřeme předtím pozici na 5m slugu
        p.open(TradeOrder(
            slug="btc-updown-5m-1", market_interval="5m", question="Q",
            outcome="YES", size_usd=1.0, limit_price=0.5,
            signal_action="BUY_UP", signal_confidence=0.7,
        ))
        policy = TradingPolicy(FakeConfig(), p)
        orders = policy.evaluate(
            _make_signal(action="BUY_UP", confidence=0.75),
            [_quote("btc-updown-5m-1", 0.5, 0.5), _quote("btc-updown-15m-1", 0.5, 0.5)],
        )
        check("5m pominut (dup), 15m vzniká", len(orders) == 1 and orders[0].market_interval == "15m")


def test_policy_skips_extreme_prices():
    print("\ntest_policy_skips_extreme_prices")
    with tempfile.TemporaryDirectory() as d:
        p = Portfolio(initial_balance=100.0, persist_path=str(Path(d) / "p.json"))
        policy = TradingPolicy(FakeConfig(), p)
        orders = policy.evaluate(
            _make_signal(action="BUY_UP", confidence=0.8),
            [_quote("btc-updown-5m-1", 1.0, 0.0)],  # už rozhodnuto
        )
        check("Extrémní cena → skip", orders == [])


def test_policy_max_positions():
    print("\ntest_policy_max_positions")
    class TightConfig(FakeConfig):
        paper_max_open_positions = 1
    with tempfile.TemporaryDirectory() as d:
        p = Portfolio(initial_balance=100.0, persist_path=str(Path(d) / "p.json"))
        p.open(TradeOrder(
            slug="x", market_interval="5m", question="Q",
            outcome="YES", size_usd=1.0, limit_price=0.5,
            signal_action="BUY_UP", signal_confidence=0.7,
        ))
        policy = TradingPolicy(TightConfig(), p)
        orders = policy.evaluate(
            _make_signal(action="BUY_UP", confidence=0.8),
            [_quote("btc-updown-5m-2", 0.5, 0.5)],
        )
        check("Při max=1 a otevřené 1 → no orders", orders == [])


# ---------- PaperExecutor ----------

async def test_executor_fills_and_emits():
    print("\ntest_executor_fills_and_emits")
    with tempfile.TemporaryDirectory() as d:
        p = Portfolio(initial_balance=100.0, persist_path=str(Path(d) / "p.json"))
        sink = RecordingSink()
        ex = PaperExecutor(portfolio=p, trade_sink=sink)
        order = TradeOrder(
            slug="s", market_interval="5m", question="Q",
            outcome="YES", size_usd=1.0, limit_price=0.55,
            signal_action="BUY_UP", signal_confidence=0.7,
        )
        pos = await ex.execute(order)
        check("Pozice vrácena", pos is not None and pos.is_open)
        check("1 event v sinku", len(sink.events) == 1)
        check("Event je FILL", sink.events[0].event == "FILL")
        check("Detail má entry_price", sink.events[0].detail.get("entry_price") == 0.55)


async def test_executor_emits_rejected_on_no_balance():
    print("\ntest_executor_emits_rejected_on_no_balance")
    with tempfile.TemporaryDirectory() as d:
        p = Portfolio(initial_balance=0.0, persist_path=str(Path(d) / "p.json"))
        sink = RecordingSink()
        ex = PaperExecutor(portfolio=p, trade_sink=sink)
        order = TradeOrder(
            slug="s", market_interval="5m", question="Q",
            outcome="YES", size_usd=1.0, limit_price=0.55,
            signal_action="BUY_UP", signal_confidence=0.7,
        )
        pos = await ex.execute(order)
        check("Pozice None", pos is None)
        check("Event je REJECTED", sink.events[0].event == "REJECTED")


# ---------- run ----------

async def main_async():
    await test_executor_fills_and_emits()
    await test_executor_emits_rejected_on_no_balance()


if __name__ == "__main__":
    test_market_interval_from_slug()
    test_is_resolved()

    test_portfolio_open_position()
    test_portfolio_insufficient_balance()
    test_portfolio_settle_win()
    test_portfolio_settle_loss()
    test_portfolio_persistence()

    test_policy_skips_skip_action()
    test_policy_skips_low_confidence()
    test_policy_buy_up_yes_5m_and_15m()
    test_policy_buy_down_uses_no_price()
    test_policy_skips_duplicate_slug()
    test_policy_skips_extreme_prices()
    test_policy_max_positions()

    asyncio.run(main_async())

    print(f"\n{'=' * 50}")
    if FAILURES == 0:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"{FAILURES} FAILURES")
        sys.exit(1)
