"""
indicators.py — Technické indikátory (čisté funkce, snadno testovatelné).

  RSI(14)        — Wilder's smoothing (SMA seed → rolling EMA gains/losses)
  EMA(9), EMA(21)— SMA seed pro prvních N period, pak standardní EMA
  EMA Cross      — bullish/bearish/neutral (porovnání posledních dvou bodů)
  Volume ratio   — current volume / průměr trailing 20 svíček (BEZ aktuální)
"""

from typing import List

from models import Candle, Indicators


def compute_ema(values: List[float], period: int) -> List[float]:
    """
    EMA se SMA seedem. Vrací sérii zarovnanou na konec vstupu.

    Pro vstup délky N a period P vrátí seznam délky N - P + 1
    (první EMA bod = SMA prvních P hodnot, pak postupně dál).
    """
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    result = [seed]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def compute_rsi(closes: List[float], period: int = 14) -> float:
    """
    Wilder's RSI: SMA seed prvních N period, pak rolling EMA-like smoothing.

    avg_gain_t = (avg_gain_{t-1} * (N-1) + gain_t) / N
    avg_loss_t = (avg_loss_{t-1} * (N-1) + loss_t) / N
    RSI = 100 - 100 / (1 + avg_gain / avg_loss)
    """
    if len(closes) < period + 1:
        return 50.0

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_indicators(
    candles: List[Candle],
    ema_fast_period: int = 9,
    ema_slow_period: int = 21,
) -> Indicators:
    """
    Sestaví Indicators z listu svíček.

    Potřebuje >= ema_slow_period + 2 svíček pro EMA cross detekci.
    """
    if len(candles) < ema_slow_period + 2:
        return Indicators()

    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]

    rsi_val = compute_rsi(closes)

    fast_series = compute_ema(closes, ema_fast_period)
    slow_series = compute_ema(closes, ema_slow_period)
    if len(fast_series) < 2 or len(slow_series) < 2:
        return Indicators()

    fast_now, fast_prev = fast_series[-1], fast_series[-2]
    slow_now, slow_prev = slow_series[-1], slow_series[-2]

    if fast_prev <= slow_prev and fast_now > slow_now:
        cross = "bullish"
    elif fast_prev >= slow_prev and fast_now < slow_now:
        cross = "bearish"
    else:
        cross = "neutral"

    # Volume ratio: aktuální vs průměr předchozích 20 (BEZ aktuální).
    window = volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1]
    avg_vol = sum(window) / len(window) if window else 0.0
    vol_ratio = round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

    return Indicators(
        rsi_14=rsi_val,
        ema_fast=round(fast_now, 2),
        ema_slow=round(slow_now, 2),
        ema_cross=cross,
        volume_ratio=vol_ratio,
    )
