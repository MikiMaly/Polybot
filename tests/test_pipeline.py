"""
End-to-end test pipeline s mocknutou strategií a sinky.

Ověřuje:
  - cooldown logika (první run projde, druhý hned po něm je zablokován)
  - cooldown se aktivuje JEN po úspěšném signálu (strategy raise → cooldown bez efektu)
  - signál teče do všech sinků
  - JsonlSink reálně píše na disk
  - sink chyba neshodí pipeline
  - CandleBuffer update-or-append funguje správně

Spuštění:  python tests/test_pipeline.py
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from models import (  # noqa: E402
    AIResponse, Candle, CandleBuffer, Indicators, PolymarketQuote, Signal,
)
from pipeline import AnalysisPipeline  # noqa: E402
from sinks import JsonlSink  # noqa: E402


FAILURES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        global FAILURES
        FAILURES += 1


# ---------- mocks ----------

class FakeConfig:
    """Minimální Config-kompatibilní mock pro pipeline."""
    ema_fast = 9
    ema_slow = 21
    signal_cooldown_seconds = 90
    candle_history = 20


class CountingSink:
    """Sink, který si pamatuje, kolikrát byl volán a s čím."""
    def __init__(self):
        self.calls: list[Signal] = []

    async def emit(self, signal: Signal) -> None:
        self.calls.append(signal)


class FailingSink:
    """Sink, co vždy raise — testuje izolaci chyb."""
    def __init__(self):
        self.calls = 0

    async def emit(self, signal: Signal) -> None:
        self.calls += 1
        raise RuntimeError("simulated sink failure")


class StubStrategy:
    """Strategie, co vrací předem připravený signál."""
    name = "stub"

    def __init__(self, signal: Signal | None = None, exc: Exception | None = None):
        self._signal = signal
        self._exc = exc
        self.calls = 0

    async def decide(self, candles, indicators, markets) -> Signal:
        self.calls += 1
        if self._exc:
            raise self._exc
        assert self._signal is not None
        return self._signal


def _make_signal(action: str = "BUY_UP", confidence: float = 0.75) -> Signal:
    return Signal(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        btc_price=100_000.0,
        action=action,
        confidence=confidence,
        reasoning="test",
        suggested_market="test market",
        indicators={},
        strategy="stub",
    )


def _make_buffer_with_data() -> CandleBuffer:
    """Buffer s 30 svíčkami — dost pro indikátory."""
    buf = CandleBuffer(maxlen=50)
    closes = [100.0 + i * 0.5 for i in range(30)]
    for i, c in enumerate(closes):
        buf.update(Candle(
            open_time=i * 300_000,
            open=c, high=c * 1.001, low=c * 0.999, close=c,
            volume=100.0, closed=True,
        ))
    return buf


# ---------- tests ----------

async def test_pipeline_runs_and_emits():
    print("\ntest_pipeline_runs_and_emits")
    sink = CountingSink()
    strategy = StubStrategy(signal=_make_signal())
    buf = _make_buffer_with_data()

    pipeline = AnalysisPipeline(
        config=FakeConfig(), buffer=buf, strategy=strategy, sinks=[sink], http=None,
    )
    # Vyhneme se Polymarket fetchi — patch _last_signal_time mimo cooldown
    # (Polymarket běží proti reálnému API, takže pipeline vyžaduje http klienta.)
    pipeline.http = None  # type: ignore[assignment]

    # Polymarket fetch potřebuje http; nahradíme ho monkeypatchem
    from pipeline import fetch_updown_markets  # noqa: F401
    import pipeline as pm
    original = pm.fetch_updown_markets

    async def fake_fetch(http):
        return []
    pm.fetch_updown_markets = fake_fetch  # type: ignore[assignment]

    try:
        await pipeline.run_once()
        check("Strategy volaná 1×", strategy.calls == 1, f"got {strategy.calls}")
        check("Sink dostal 1 signál", len(sink.calls) == 1, f"got {len(sink.calls)}")
        check("Cooldown nastaven po úspěchu", pipeline._last_signal_time > 0)
    finally:
        pm.fetch_updown_markets = original  # type: ignore[assignment]


async def test_cooldown_blocks_second_run():
    print("\ntest_cooldown_blocks_second_run")
    sink = CountingSink()
    strategy = StubStrategy(signal=_make_signal())
    buf = _make_buffer_with_data()
    pipeline = AnalysisPipeline(
        config=FakeConfig(), buffer=buf, strategy=strategy, sinks=[sink], http=None,
    )

    import pipeline as pm
    original = pm.fetch_updown_markets

    async def fake_fetch(http):
        return []
    pm.fetch_updown_markets = fake_fetch  # type: ignore[assignment]

    try:
        await pipeline.run_once()
        await pipeline.run_once()  # tohle mělo být zablokované cooldownem
        check("Strategy volaná jen 1× (druhý run blokován)", strategy.calls == 1,
              f"got {strategy.calls}")
        check("Sink dostal jen 1 signál", len(sink.calls) == 1, f"got {len(sink.calls)}")
    finally:
        pm.fetch_updown_markets = original  # type: ignore[assignment]


async def test_cooldown_not_set_on_strategy_failure():
    print("\ntest_cooldown_not_set_on_strategy_failure")
    sink = CountingSink()
    strategy = StubStrategy(exc=RuntimeError("AI selhala"))
    buf = _make_buffer_with_data()
    pipeline = AnalysisPipeline(
        config=FakeConfig(), buffer=buf, strategy=strategy, sinks=[sink], http=None,
    )

    import pipeline as pm
    original = pm.fetch_updown_markets

    async def fake_fetch(http):
        return []
    pm.fetch_updown_markets = fake_fetch  # type: ignore[assignment]

    try:
        await pipeline.run_once()
        check("Cooldown NEnastaven po selhání strategie", pipeline._last_signal_time == 0.0,
              f"got {pipeline._last_signal_time}")
        check("Sink nedostal nic", len(sink.calls) == 0)
    finally:
        pm.fetch_updown_markets = original  # type: ignore[assignment]


async def test_sink_failure_does_not_kill_pipeline():
    print("\ntest_sink_failure_does_not_kill_pipeline")
    good_sink = CountingSink()
    bad_sink = FailingSink()
    strategy = StubStrategy(signal=_make_signal())
    buf = _make_buffer_with_data()
    pipeline = AnalysisPipeline(
        config=FakeConfig(), buffer=buf, strategy=strategy,
        sinks=[bad_sink, good_sink], http=None,
    )

    import pipeline as pm
    original = pm.fetch_updown_markets

    async def fake_fetch(http):
        return []
    pm.fetch_updown_markets = fake_fetch  # type: ignore[assignment]

    try:
        await pipeline.run_once()
        check("Failing sink volaný", bad_sink.calls == 1)
        check("Good sink dostal signál i přes selhání jiného sinku", len(good_sink.calls) == 1)
    finally:
        pm.fetch_updown_markets = original  # type: ignore[assignment]


async def test_jsonl_sink_writes_file():
    print("\ntest_jsonl_sink_writes_file")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sub" / "signals.jsonl"
        sink = JsonlSink(str(path))
        sig = _make_signal()
        await sink.emit(sig)
        await sink.emit(sig)

        check("Soubor vytvořen", path.exists())
        lines = path.read_text(encoding="utf-8").splitlines()
        check("Dva zápisy", len(lines) == 2, f"got {len(lines)}")
        parsed = json.loads(lines[0])
        check("JSON má 'action'", parsed.get("action") == "BUY_UP")


def test_candle_buffer_update_vs_append():
    print("\ntest_candle_buffer_update_vs_append")
    buf = CandleBuffer(maxlen=5)
    c1 = Candle(open_time=1000, open=1, high=1, low=1, close=100, volume=10, closed=False)
    c1_updated = Candle(open_time=1000, open=1, high=1, low=1, close=101, volume=11, closed=True)
    c2 = Candle(open_time=2000, open=1, high=1, low=1, close=200, volume=20, closed=False)

    buf.update(c1)
    buf.update(c1_updated)
    buf.update(c2)

    snap = buf.snapshot()
    check("Buffer má 2 svíčky (1 updated, 1 nová)", len(snap) == 2, f"got {len(snap)}")
    check("První svíčka má aktualizovaný close", snap[0].close == 101.0)
    check("První svíčka je closed=True po update", snap[0].closed is True)


def test_candle_buffer_maxlen():
    print("\ntest_candle_buffer_maxlen")
    buf = CandleBuffer(maxlen=3)
    for i in range(5):
        buf.update(Candle(open_time=i * 1000, open=1, high=1, low=1, close=i, volume=1))
    snap = buf.snapshot()
    check("Buffer respektuje maxlen=3", len(snap) == 3)
    check("Buffer drží POSLEDNÍ 3", [c.close for c in snap] == [2.0, 3.0, 4.0])


def test_pydantic_ai_response_validates():
    print("\ntest_pydantic_ai_response_validates")
    valid = '{"action":"BUY_UP","confidence":0.75,"reasoning":"x","suggested_market":"y"}'
    obj = AIResponse.model_validate_json(valid)
    check("Validní JSON projde", obj.action == "BUY_UP" and obj.confidence == 0.75)

    invalid_action = '{"action":"MOON","confidence":0.5,"reasoning":"x","suggested_market":"y"}'
    try:
        AIResponse.model_validate_json(invalid_action)
        check("Nevalidní action vyhodí ValidationError", False, "no exception raised")
    except Exception as e:
        check("Nevalidní action vyhodí chybu", "MOON" in str(e) or "literal" in str(e).lower())

    bad_confidence = '{"action":"SKIP","confidence":1.5,"reasoning":"x","suggested_market":"y"}'
    try:
        AIResponse.model_validate_json(bad_confidence)
        check("Confidence > 1.0 vyhodí ValidationError", False)
    except Exception:
        check("Confidence > 1.0 vyhodí ValidationError", True)


# ---------- run ----------

async def main():
    test_candle_buffer_update_vs_append()
    test_candle_buffer_maxlen()
    test_pydantic_ai_response_validates()

    await test_jsonl_sink_writes_file()
    await test_pipeline_runs_and_emits()
    await test_cooldown_blocks_second_run()
    await test_cooldown_not_set_on_strategy_failure()
    await test_sink_failure_does_not_kill_pipeline()


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{'=' * 50}")
    if FAILURES == 0:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"{FAILURES} FAILURES")
        sys.exit(1)
