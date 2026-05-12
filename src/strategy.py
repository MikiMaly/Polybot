"""
strategy.py — Rozhodovací vrstva.

Strategy protokol: čistá funkce (candles + indicators + markets) → Signal.

Implementace:
  AIAdvisor   — volá OpenRouter (OpenAI-kompatibilní API), JSON output
                validovaný přes AIResponse model.

Přidání další strategie (např. RuleBased) = implementovat Strategy protokol.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import List, Protocol

from openai import AsyncOpenAI
from pydantic import ValidationError

from config import Config
from models import AIResponse, Candle, Indicators, PolymarketQuote, Signal

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


class Strategy(Protocol):
    name: str

    async def decide(
        self,
        candles: List[Candle],
        indicators: Indicators,
        markets: List[PolymarketQuote],
    ) -> Signal:
        ...


class AIAdvisor:
    """OpenRouter-based strategie. JSON response validovaná přes Pydantic."""

    name = "ai_advisor"

    def __init__(self, config: Config):
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.openrouter_api_key,
            base_url=config.openrouter_base_url,
        )

    async def decide(
        self,
        candles: List[Candle],
        indicators: Indicators,
        markets: List[PolymarketQuote],
    ) -> Signal:
        prompt = self._build_prompt(candles, indicators, markets)
        content = await self._call_with_retry(prompt)
        ai = self._parse(content)

        return Signal(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            btc_price=candles[-1].close,
            action=ai.action,
            confidence=ai.confidence,
            reasoning=ai.reasoning,
            suggested_market=ai.suggested_market,
            indicators=indicators.model_dump(),
            strategy=self.name,
        )

    # ---------- internal ----------

    def _build_prompt(
        self,
        candles: List[Candle],
        ind: Indicators,
        markets: List[PolymarketQuote],
    ) -> str:
        recent = candles[-self.config.candle_history:]
        candle_lines = "\n".join(
            f"  {c.dt} | O:{c.open:.0f} H:{c.high:.0f} L:{c.low:.0f} "
            f"C:{c.close:.0f} V:{c.volume:.0f}"
            for c in recent
        )

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
            f"Čas analýzy:   {now_utc}"
        )

        usable = [m for m in markets if m.yes is not None]
        if usable:
            lines = "\n".join(
                f"  YES={m.yes:.2f} / NO={m.no:.2f}  → \"{m.question}\""
                for m in usable
            )
            prompt += (
                "\n\nAktuální Polymarket BTC trhy:\n"
                f"{lines}\n"
                "Význam: cena YES = tržní pravděpodobnost, že podmínka nastane. "
                "Zahrň tyto tržní pravděpodobnosti do rozhodování."
            )
        return prompt

    async def _call_with_retry(self, prompt: str) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                log.debug(f"OpenRouter call (attempt {attempt + 1}/3)")
                response = await self.client.chat.completions.create(
                    model=self.config.openrouter_model,
                    max_tokens=self.config.openrouter_max_tokens,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                content = response.choices[0].message.content
                if not content:
                    raise ValueError("Model vrátil prázdnou odpověď")
                return content.strip()
            except Exception as e:
                last_error = e
                msg = str(e).lower()
                is_rate_limit = "429" in msg or "rate" in msg
                if attempt < 2 and is_rate_limit:
                    wait = 15 * (attempt + 1)
                    log.warning(f"Rate limit, čekám {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                raise
        # nedosažitelné, ale type checker je rád
        assert last_error is not None
        raise last_error

    def _parse(self, raw: str) -> AIResponse:
        cleaned = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return AIResponse.model_validate_json(cleaned)
        except ValidationError as e:
            log.error(f"OpenRouter response invalid: {cleaned!r}")
            raise ValueError(f"Invalid AI response: {e}") from e
        except json.JSONDecodeError as e:
            log.error(f"OpenRouter response not JSON: {cleaned!r}")
            raise
