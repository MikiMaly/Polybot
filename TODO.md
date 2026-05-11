# Polybot – TODO / Optimalizace

Výsledek code review. Úkoly seřazeny podle priority.

---

## Kritické

- [ ] **feed.py** – Obalit `json.loads` + zpracování `k` do `try-except json.JSONDecodeError` ve funkci `stream_candles` (nevalidní WS zpráva jinak crashne bota)
- [ ] **feed.py** – Guard pro prázdný REST response: `if not candles: raise ValueError(...)` před přístupem na `candles[0]` / `candles[-1]` (řádek 62)
- [ ] **feed.py** – Validace délky row při parsování svíček: `if len(row) < 6: continue` (řádky 48–58, potenciální `IndexError`)

## Střední priorita

- [ ] **bot.py** – Zrušit `_analysis_task` v `finally` bloku (`task.cancel()` + `await asyncio.gather(...)`) – task jinak přežije shutdown bota
- [ ] **bot.py** – Rozlišit `except` bloky v `_run_analysis`: místo `except Exception` zachytávat `httpx.HTTPError`, `json.JSONDecodeError`, `ValueError` zvlášť
- [ ] **polymarket.py** – Sdílený `httpx.AsyncClient` místo nového klienta per request (funkce `_fetch_market`, řádek 34)
- [ ] **polymarket.py** – Změnit `log.debug` → `log.warning` při selhání fetche trhu (řádek 61) – jinak se chyba tiše propadne
- [ ] **dashboard/server.py** – Přidat Pydantic validaci příchozích signálů na `POST /api/signals` (řádek 49) – bez validace lze zapsat libovolná data do `signals.jsonl`

## Nízká priorita

- [ ] **models.py** – Odstranit unused import `field` z `from dataclasses import dataclass, field, asdict`
- [ ] **feed.py** – Odstranit redundantní `import asyncio` uvnitř `except` bloku (řádek 109) – asyncio je již importováno na úrovni modulu
- [ ] **feed.py** – Sjednotit typové hinty: odstranit `List` z `typing`, používat lowercase `list` (Python 3.9+)
- [ ] **indicators.py** – Efektivnější delta výpočet v `compute_rsi`: nepočítat celý list, jen posledních `period` prvků (řádky 53–54)
