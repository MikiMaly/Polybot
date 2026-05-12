# Swing-trading refactor — plán

> Stávající paper-trading drží pozici až do rezoluce trhu (5/15 min).
> Cíl: během životnosti jednoho trhu udělat víc obchodů — koupit levně, prodat dráž ještě před uzavřením.

## Nová logika

- **Entry** (zachovaná): AI signál (`BUY_UP`/`BUY_DOWN` ≥ `min_confidence`) → policy filtr → `PaperExecutor` otevře pozici za tržní YES/NO cenu.
- **Exit** (nová vrstva): `ExitPolicy` se vyhodnotí každý settler tick (60s) **i** po každém signálu z pipeline:
  - **Take profit**: `current_price ≥ entry_price + paper_take_profit_delta` → SELL za current_price
  - **Stop loss**: `current_price ≤ entry_price − paper_stop_loss_delta` → SELL za current_price
  - **Pre-resolution**: market resolvuje za < `paper_exit_before_resolution_seconds` → SELL za current_price (nebýt v binární rezoluci)
  - **Rezoluce sama**: pokud trh už má `outcomePrices ∈ {0,1}`, close za final price (jako settle dnes)
- **Anti-dupe entry**: místo "1 pozice na slug" teď "**1 OPEN pozice na (slug, outcome)**" — po close-u na YES můžu znovu otevřít YES, anebo otevřít NO i když YES běží.

## P/L

Binární trh, payout 1.00 per share:
- shares = `size_usd / entry_price`
- exit hodnota = `shares * exit_price = size_usd * (exit_price / entry_price)`
- `pnl_usd = exit_hodnota − size_usd = size_usd * (exit_price/entry_price − 1)`

Stejný vzorec jak pro pre-resolution exit (exit_price ∈ (0,1)), tak pro rezoluci (exit_price ∈ {0,1}).

## Změny v kódu

| Soubor | Změna |
|---|---|
| `config.py` | + `paper_take_profit_delta=0.05`, `paper_stop_loss_delta=0.10`, `paper_exit_before_resolution_seconds=60` |
| `models.py` | `Position.status: Literal["OPEN", "CLOSED"]` (sjednoceno). Nová pole: `close_price`, `close_reason: Literal["take_profit"\|"stop_loss"\|"pre_resolution"\|"settled_win"\|"settled_loss"]`, `close_time`. `TradeEventType` přidá `"CLOSE"`. |
| `trading.py` | `Portfolio.settle()` → `Portfolio.close(pos_id, exit_price, reason)`. Anti-dupe: `has_open_position(slug, outcome)`. Nová třída `ExitPolicy`. `Settler` přejmenován na `PositionManager` (interně volá ExitPolicy + close). |
| `polymarket.py` | + `slot_end_time(slug) -> int` (parse `btc-updown-{5m\|15m}-{ts}` → ts + 300 nebo +900) |
| `pipeline.py` | Před entry policy zavolat ExitPolicy (chytíme TP/SL hned po novém signálu, ne až 60s pozdějc) |
| `sinks.py` | Beze změny (TradeSink už generický). `ConsoleSink` přidá log pro `CLOSE` event. |
| `bot.py` | `settler` field se přejmenuje na `manager` (kosmetika). |
| `tests/` | Update `test_trading.py` na nové API. Přidat `test_exit_policy.py`. |

## TODO (pořadí implementace)

- [x] **0.** Repo cleanup (stray `src/logs/`, .gitignore) + commit dosavadní v2 refactor + paper trading
- [ ] **1.** Config: TP/SL/pre-resolution knoby
- [ ] **2.** Models: Position rozšířený (`close_price`, `close_reason`, status změna)
- [ ] **3.** Portfolio: `close()` místo `settle()` + nový anti-dupe per (slug, outcome)
- [ ] **4.** ExitPolicy třída (čisté pravidlo, žádný I/O)
- [ ] **5.** PositionManager (přejmenovaný Settler) volá ExitPolicy + close
- [ ] **6.** Pipeline hook pro exit policy mezi signal emit a entry policy
- [ ] **7.** TradingPolicy: anti-dupe per (slug, outcome)
- [ ] **8.** Update tests (test_trading.py) + nové (test_exit_policy.py)
- [ ] **9.** Smoke test: spustit bot, sledovat OPEN → SELL → znovu OPEN cyklus

## Default knoby

| Parametr | Default | Důvod |
|---|---|---|
| `paper_take_profit_delta` | `0.05` | 5¢ move = ~10% ROI na entry 0.50; reálný v 5/15min markets |
| `paper_stop_loss_delta` | `0.10` | Asymetrický: necháme loss "běžet" mírně víc, brzdíme jen větší driftu |
| `paper_exit_before_resolution_seconds` | `60` | 1 min před rezolucí jdeme ven — nebudeme se vystavovat binárnímu 0/1 |

## Co se NEMĚNÍ

- AI prompt + Strategy vrstva (bot dál dostává signály od OpenRouter)
- Hub push (mmaly.cz dashboard)
- Indikátory, candle buffer, feed
- Test coverage (jen update existujících + přidání exit testů)
