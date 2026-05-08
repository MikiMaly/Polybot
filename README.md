# Polybot — BTC Polymarket Signal Bot

Asyncní Python bot, který sleduje BTC/USDT na Binance, počítá technické indikátory a pomocí OpenRouter AI generuje obchodní doporučení pro Polymarket trhy.

## Jak to funguje

```
Binance WS (5min svíčky)
        ↓
  buffer 100 svíček
        ↓  každé 2 minuty
  RSI · EMA9/21 · Volume ratio
        ↓
  Polymarket YES/NO ceny
  (btc-updown-5m + btc-updown-15m)
        ↓
  OpenRouter AI (GPT / Llama / ...)
        ↓
  BUY_UP · BUY_DOWN · SKIP
        ↓
  logs/signals.jsonl + Dashboard
```

## Struktura

```
src/
├── main.py          — spouštěcí skript
├── bot.py           — orchestrátor (buffer + periodická analýza)
├── feed.py          — Binance REST + WebSocket stream
├── indicators.py    — RSI, EMA, volume ratio
├── advisor.py       — OpenRouter AI vrstva
├── polymarket.py    — Polymarket Gamma API (slug-based fetch)
├── models.py        — datové třídy (Candle, Indicators, Signal)
└── config.py        — konfigurace z .env
logs/                — signals.jsonl + bot.log (gitignored)
```

## Instalace

```bash
pip install -r requirements.txt
cp .env.example .env
# doplň OPENROUTER_API_KEY do .env
```

## Spuštění

```bash
python src/main.py
```

## Konfigurace

| Proměnná | Popis |
|---|---|
| `OPENROUTER_API_KEY` | API klíč z [openrouter.ai](https://openrouter.ai) (zdarma) |
| `HUB_API_URL` | URL dashboardu pro odesílání signálů (volitelné) |
| `HUB_BOT_SECRET` | Bearer token pro dashboard API (volitelné) |

Model, intervaly a indikátory lze měnit přímo v `src/config.py`.

## Dashboard

Signály lze zobrazit na webovém dashboardu — viz repozitář [MikiMaly/hub](https://github.com/MikiMaly/hub).
