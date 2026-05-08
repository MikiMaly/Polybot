"""
indicators.py — Výpočet technických indikátorů.

Logika:
  RSI(14)  — Relative Strength Index: měří rychlost a velikost cenových pohybů.
              > 70 = překoupeno (možný pokles), < 30 = přeprodáno (možný růst).
  EMA(9)   — Rychlá exponenciální klouzavá průměr: sleduje krátkodobý trend.
  EMA(21)  — Pomalá EMA: sleduje střednědobý trend.
  EMA Cross — Překřížení EMA9 a EMA21:
               bullish = EMA9 překříží EMA21 zdola (signál růstu)
               bearish = EMA9 překříží EMA21 shora (signál poklesu)
  Volume Ratio — Aktuální objem / průměrný objem (20 svíček).
                 > 1.5 = zvýšená aktivita trhu (potvrzuje signál)
"""

from typing import List
from models import Candle, Indicators


def compute_ema(values: List[float], period: int) -> List[float]:
    """
    Exponenciální klouzavý průměr.

    Vzorec: EMA_t = price_t * k + EMA_(t-1) * (1 - k)
    kde k = 2 / (period + 1)

    Inicializace: první hodnota EMA = první cena (SMA by bylo přesnější,
    ale pro live data s dostatečnou historií je rozdíl zanedbatelný).
    """
    if not values:
        return []
    k = 2 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def compute_rsi(closes: List[float], period: int = 14) -> float:
    """
    Relative Strength Index (Wilder's RSI).

    1. Spočítá denní změny cen (delta).
    2. Oddělí zisky (gains) a ztráty (losses).
    3. Průměrný zisk / průměrná ztráta = RS.
    4. RSI = 100 - (100 / (1 + RS))

    Vrací 50.0 pokud nemáme dostatek dat.
    """
    if len(closes) < period + 1:
        return 50.0

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = deltas[-period:]

    gains = [max(d, 0) for d in recent]
    losses = [abs(min(d, 0)) for d in recent]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_indicators(candles: List[Candle], ema_fast: int = 9, ema_slow: int = 21) -> Indicators:
    """
    Vypočte všechny indikátory z listu svíček.

    Potřebuje alespoň ema_slow + 2 svíček pro detekci crossu
    (porovnává aktuální a předchozí stav EMA).
    """
    if len(candles) < ema_slow + 2:
        return Indicators()

    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]

    # RSI
    rsi_val = compute_rsi(closes)

    # EMA — počítáme z celé historie pro přesnost
    ema_fast_series = compute_ema(closes, ema_fast)
    ema_slow_series = compute_ema(closes, ema_slow)

    ema_fast_now = ema_fast_series[-1]
    ema_slow_now = ema_slow_series[-1]
    ema_fast_prev = ema_fast_series[-2]
    ema_slow_prev = ema_slow_series[-2]

    # EMA cross detekce
    if ema_fast_prev < ema_slow_prev and ema_fast_now > ema_slow_now:
        cross = "bullish"   # EMA9 překřížila EMA21 zdola → bull signal
    elif ema_fast_prev > ema_slow_prev and ema_fast_now < ema_slow_now:
        cross = "bearish"   # EMA9 překřížila EMA21 shora → bear signal
    else:
        cross = "neutral"

    # Volume ratio
    avg_vol = sum(volumes[-20:]) / min(len(volumes), 20)
    vol_ratio = round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

    return Indicators(
        rsi_14=rsi_val,
        ema_fast=round(ema_fast_now, 2),
        ema_slow=round(ema_slow_now, 2),
        ema_cross=cross,
        volume_ratio=vol_ratio,
    )
