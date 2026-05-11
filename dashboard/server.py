"""
dashboard/server.py — Lokální dashboard pro BTC Polymarket Signal Bot.

Spuštění:
  cd /cesta/k/Polybot
  uvicorn dashboard.server:app --port 8001 --reload

Pak nastav v .env:
  HUB_API_URL=http://localhost:8001/api/signals
  HUB_BOT_SECRET=local_secret
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

load_dotenv()

SIGNALS_LOG = Path(os.environ.get("SIGNALS_LOG", "logs/signals.jsonl"))
HUB_BOT_SECRET = os.environ.get("HUB_BOT_SECRET", "")

app = FastAPI(title="Polybot Dashboard")


def _load_signals(limit: int = 50) -> list[dict]:
    if not SIGNALS_LOG.exists():
        return []
    lines = SIGNALS_LOG.read_text(encoding="utf-8").strip().splitlines()
    signals = []
    for line in reversed(lines[-limit:]):
        try:
            signals.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return signals


@app.post("/api/signals")
async def receive_signal(request: Request):
    if HUB_BOT_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {HUB_BOT_SECRET}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    SIGNALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SIGNALS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(body, ensure_ascii=False) + "\n")
    return {"status": "ok"}


@app.get("/api/signals")
async def get_signals():
    return JSONResponse(_load_signals())


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    signals = _load_signals()

    ACTION_COLOR = {
        "BUY_UP":   "#22c55e",
        "BUY_DOWN": "#ef4444",
        "SKIP":     "#6b7280",
    }
    ACTION_LABEL = {
        "BUY_UP":   "▲ BUY UP",
        "BUY_DOWN": "▼ BUY DOWN",
        "SKIP":     "— SKIP",
    }

    rows = ""
    for s in signals:
        action = s.get("action", "?")
        color = ACTION_COLOR.get(action, "#fff")
        label = ACTION_LABEL.get(action, action)
        conf = f"{float(s.get('confidence', 0)):.0%}"
        price = f"${float(s.get('btc_price', 0)):,.0f}"
        ind = s.get("indicators", {})
        rsi = ind.get("rsi_14", "—")
        cross = ind.get("ema_cross", "—")
        rows += f"""
        <tr>
          <td>{s.get('timestamp', '—')}</td>
          <td style="color:{color};font-weight:bold">{label}</td>
          <td>{conf}</td>
          <td>{price}</td>
          <td>{s.get('suggested_market', '—')}</td>
          <td>RSI {rsi} | {cross}</td>
          <td style="color:#9ca3af;font-size:0.85em">{s.get('reasoning', '—')}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="30">
  <title>Polybot Dashboard</title>
  <style>
    body {{ background:#0f172a; color:#e2e8f0; font-family:monospace; padding:2rem; }}
    h1 {{ color:#38bdf8; margin-bottom:0.25rem; }}
    p.sub {{ color:#64748b; margin-bottom:1.5rem; font-size:0.9em; }}
    table {{ width:100%; border-collapse:collapse; font-size:0.9em; }}
    th {{ text-align:left; color:#64748b; border-bottom:1px solid #1e293b; padding:0.5rem 0.75rem; }}
    td {{ padding:0.5rem 0.75rem; border-bottom:1px solid #1e293b; vertical-align:top; }}
    tr:hover td {{ background:#1e293b; }}
    .empty {{ color:#475569; text-align:center; padding:3rem; }}
  </style>
</head>
<body>
  <h1>⚡ Polybot Dashboard</h1>
  <p class="sub">Posledních {len(signals)} signálů · auto-refresh každých 30s</p>
  <table>
    <thead>
      <tr>
        <th>Čas</th><th>Akce</th><th>Confidence</th>
        <th>BTC cena</th><th>Market</th><th>Indikátory</th><th>Zdůvodnění</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows) if rows else '<tr><td colspan="7" class="empty">Zatím žádné signály.</td></tr>'}
    </tbody>
  </table>
</body>
</html>"""
    return html
