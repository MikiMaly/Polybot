"""
Sanity testy pro indicators.py — čisté funkce, žádné mocky potřeba.

Spuštění:  python tests/test_indicators.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from indicators import compute_ema, compute_rsi, compute_indicators  # noqa: E402
from models import Candle  # noqa: E402


# ---------- helpers ----------

def _candles(closes: list[float], volumes: list[float] | None = None) -> list[Candle]:
    if volumes is None:
        volumes = [100.0] * len(closes)
    return [
        Candle(
            open_time=i * 300_000,  # 5min intervaly v ms
            open=c, high=c * 1.001, low=c * 0.999, close=c,
            volume=v, closed=True,
        )
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def approx(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        global FAILURES
        FAILURES += 1


FAILURES = 0


# ---------- EMA ----------

def test_ema_seed_is_sma() -> None:
    print("\ntest_ema_seed_is_sma")
    values = [1, 2, 3, 4, 5]
    ema = compute_ema(values, period=3)
    # první EMA bod = SMA(1,2,3) = 2.0
    check("EMA[0] == SMA prvních 3 hodnot", approx(ema[0], 2.0), f"got {ema[0]}")
    # délka = N - P + 1 = 5 - 3 + 1 = 3
    check("EMA délka = N - P + 1", len(ema) == 3, f"got {len(ema)}")


def test_ema_too_few_values() -> None:
    print("\ntest_ema_too_few_values")
    check("EMA prázdná pro krátký vstup", compute_ema([1.0, 2.0], period=5) == [])


def test_ema_constant() -> None:
    print("\ntest_ema_constant")
    ema = compute_ema([10.0] * 30, period=9)
    check("EMA konstantní řady = konstanta", all(approx(v, 10.0) for v in ema))


# ---------- RSI ----------

def test_rsi_default_when_too_short() -> None:
    print("\ntest_rsi_default_when_too_short")
    check("RSI vrací 50 pro krátký vstup", compute_rsi([100.0, 101.0], period=14) == 50.0)


def test_rsi_all_gains() -> None:
    print("\ntest_rsi_all_gains")
    # Stále rostoucí cena → RSI = 100
    closes = [100.0 + i for i in range(30)]
    rsi = compute_rsi(closes, period=14)
    check("RSI = 100 při všech ziscích", rsi == 100.0, f"got {rsi}")


def test_rsi_all_losses() -> None:
    print("\ntest_rsi_all_losses")
    closes = [100.0 - i for i in range(30)]
    rsi = compute_rsi(closes, period=14)
    # Wilder: avg_gain=0 → RSI = 0
    check("RSI = 0 při všech ztrátách", rsi == 0.0, f"got {rsi}")


def test_rsi_in_range() -> None:
    print("\ntest_rsi_in_range")
    # smíšený trend
    closes = [100, 102, 101, 103, 102, 104, 103, 105, 104, 106, 105, 107, 106, 108, 107]
    rsi = compute_rsi(closes, period=14)
    check("RSI v rozsahu 0..100", 0.0 <= rsi <= 100.0, f"got {rsi}")


# ---------- compute_indicators ----------

def test_indicators_not_ready_when_short() -> None:
    print("\ntest_indicators_not_ready_when_short")
    ind = compute_indicators(_candles([100.0] * 5))
    check("ready == False pro málo dat", ind.ready is False)


def test_indicators_full_pipeline() -> None:
    print("\ntest_indicators_full_pipeline")
    # 30 svíček — stoupající trend
    closes = [100.0 + i * 0.5 for i in range(30)]
    ind = compute_indicators(_candles(closes))
    check("ready == True", ind.ready is True)
    check("RSI vyšší (uptrend)", ind.rsi_14 > 70, f"RSI={ind.rsi_14}")
    check("EMA9 > EMA21 (uptrend)", ind.ema_fast > ind.ema_slow)
    check("ema_cross je hodnota", ind.ema_cross in ("bullish", "bearish", "neutral"))


def test_volume_ratio_excludes_current() -> None:
    print("\ntest_volume_ratio_excludes_current")
    # 24 svíček, posledních 20 s objemem 100, aktuální má 200 → ratio = 2.0
    closes = [100.0 + i * 0.1 for i in range(24)]
    volumes = [100.0] * 23 + [200.0]
    ind = compute_indicators(_candles(closes, volumes))
    check("Volume ratio = 2.0 (current/avg)", approx(ind.volume_ratio, 2.0), f"got {ind.volume_ratio}")


def test_ema_cross_detection() -> None:
    print("\ntest_ema_cross_detection")
    # downtrend → uptrend = bullish cross někde poblíž obratu
    closes = [100.0 - i * 0.5 for i in range(15)] + [92.5 + i * 0.8 for i in range(15)]
    ind = compute_indicators(_candles(closes))
    check("ready", ind.ready is True)
    # po reverzním uptrendu by EMA9 měla už být nad EMA21
    check("EMA9 > EMA21 po reverzním uptrendu", ind.ema_fast > ind.ema_slow,
          f"fast={ind.ema_fast} slow={ind.ema_slow}")


# ---------- run ----------

if __name__ == "__main__":
    tests = [
        test_ema_seed_is_sma,
        test_ema_too_few_values,
        test_ema_constant,
        test_rsi_default_when_too_short,
        test_rsi_all_gains,
        test_rsi_all_losses,
        test_rsi_in_range,
        test_indicators_not_ready_when_short,
        test_indicators_full_pipeline,
        test_volume_ratio_excludes_current,
        test_ema_cross_detection,
    ]
    for t in tests:
        t()
    print(f"\n{'=' * 50}")
    if FAILURES == 0:
        print(f"ALL {len(tests)} TESTS PASSED")
        sys.exit(0)
    else:
        print(f"{FAILURES} FAILURES")
        sys.exit(1)
