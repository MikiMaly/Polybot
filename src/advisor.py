"""
advisor.py — OpenRouter AI vrstva pro generování obchodních doporučení.

Logika:
  1. Sestaví prompt s posledními N svíčkami a indikátory.
  2. Zavolá OpenRouter API (OpenAI-kompatibilní) s požadavkem na JSON odpověď.
  3. Parsuje odpověď do Signal objektu.
"""

import asyncio
import json
import logging
from datetime import datetime

from openai import AsyncOpenAI, RateLimitError

from config import Config
from models import Candle, Indicators, Signal

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Jsi expert na krátkodobé predikce ceny BTC pro binární predikční markety (Polymarket).
Dostaneš 5min OHLCV svíčky BTC/USDT a technické indikátory.

Tvůj úkol: Rozhodni, zda doporučuješ vsadit na:
  BUY_UP   = BTC poroste v nejbližším časovém okně
  BUY_DOWN = BTC klesne v nejbližším časovém okně
  SKIP     = signál není dostatečně přesvědčivý, lepší neinvestovat

Důležité: Polymarket je binární trh s expirací — výstup není možný kdykoliv.
Buď konzervativní s confidence. SKIP je legitimní volba.

Odpověz POUZE validním JSON objektem (žádný text před ani za, žádné markdown backticks):
{
  "action": "BUY_UP" | "BUY_DOWN" | "SKIP",
  "confidence": číslo 0.0-1.0,
  "reasoning": "max 2 věty česky — konkrétní zdůvodnění",
  "suggested_market": "příklad Polymarket marketu, např. 'BTC above $103k by May 9'"
}"""


class OpenRouterAdvisor:
    """Wrapper pro OpenRouter API volání (OpenAI-kompatibilní)."""

    def __init__(self, config: Config):
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
        )

    def _build_prompt(self, candles: list[Candle], ind: Indicators, poly_markets: list[dict]) -> str:
        recent = candles[-self.config.candle_history:]
        candle_lines = "\n".join(
            f"  {c.dt} | O:{c.open:.0f} H:{c.high:.0f} L:{c.low:.0f} C:{c.close:.0f} V:{c.volume:.0f}"
            for c in recent
        )

        prompt = (
            f"5min BTC/USDT svíčky (posledních {len(recent)}, UTC):\n"
            f"{candle_lines}\n\n"
            f"Technické indikátory:\n"
            f"  RSI(14):      {ind.rsi_14}\n"
            f"  EMA(9):       {ind.ema_fast}\n"
            f"  EMA(21):      {ind.ema_slow}\n"
            f"  EMA cross:    {ind.ema_cross}\n"
            f"  Volume ratio: {ind.volume_ratio}x\n\n"
            f"Aktuální cena: {recent[-1].close:.0f} USD\n"
            f"Čas analýzy: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )

        if poly_markets:
            lines = []
            for m in poly_markets:
                if m["yes"] is not None:
                    lines.append(f"  YES={m['yes']:.2f} / NO={m['no']:.2f}  → \"{m['question']}\"")
            if lines:
                prompt += (
                    "\n\nAktuální Polymarket BTC trhy (seřazeno dle likvidity):\n"
                    + "\n".join(lines)
                    + "\nVýznam: cena YES = tržní pravděpodobnost, že podmínka nastane."
                    + " Zahrň tyto tržní pravděpodobnosti do svého rozhodování."
                )

        return prompt

    async def get_signal(self, candles: list[Candle], ind: Indicators, poly_markets: list[dict]) -> Signal:
        """
        Zavolá OpenRouter a vrátí Signal. Při rate limitu zkusí 3× s prodlevou.
        Raises: Exception, json.JSONDecodeError
        """
        prompt = self._build_prompt(candles, ind, poly_markets)

        for attempt in range(3):
            try:
                log.debug(f"Volám OpenRouter API (pokus {attempt + 1})...")
                response = await self.client.chat.completions.create(
                    model=self.config.openrouter_model,
                    max_tokens=self.config.openrouter_max_tokens,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                break
            except RateLimitError:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    log.warning(f"Rate limit, čekám {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    raise

        content = response.choices[0].message.content
        if not content:
            raise ValueError("Model vrátil prázdnou odpověď")
        raw = content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.error(f"OpenRouter vrátil nevalidní JSON: {raw!r}")
            raise

        return Signal(
            timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            btc_price=candles[-1].close,
            action=data["action"],
            confidence=float(data["confidence"]),
            reasoning=data["reasoning"],
            suggested_market=data["suggested_market"],
            indicators=ind.to_dict(),
        )
