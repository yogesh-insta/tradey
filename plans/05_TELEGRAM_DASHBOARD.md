# Telegram + Dashboard Plan

Ops layer on top of the four trading services: push alerts to your phone, and a local web dashboard for status/P&L.

## Goals

- **Telegram:** fire-and-forget alerts for connect, orders, fills, risk rejects, cycle errors, daily summary
- **Dashboard:** local HTML UI (refreshable) showing bot status, open positions, recent trades, simple P&L
- **Non-blocking:** notify failures never break trading logic

## Architecture

```text
Signal / Risk / Executor / ATO / scripts
              │
              ├── packages/notify  → Telegram Bot API
              │
              └── packages/ledger (or ato_tax events)
                        │
                        ▼
              services/dashboard  → http://127.0.0.1:8080
```

## Telegram events (v1)

| Event | Priority |
|---|---|
| IBKR connected / disconnected | default / high |
| Order submitted | default |
| Fill | default |
| Risk rejection | default |
| Cycle / scanner error | high |
| Daily summary | default |
| Manual test ping | default |

Config via `.env` (never git):

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- Optional: `NOTIFY_ENABLED=true`

## Dashboard pages (v1)

Single page at `/`:

1. Status strip — paper/live flag, last update time
2. Open positions table
3. Recent trades (last 50)
4. Today P&L summary (wins/losses/gross if closable)

Data source v1: SQLite ledger file written by executor smoke scripts and later ATO service.

## Delivery order

1. Scaffold packages + `.env.example`
2. `packages/notify` + `scripts/test_telegram.py`
3. Minimal ledger writer + dashboard FastAPI
4. Hook notify into IBKR connect / order smoke scripts
5. Later: wire into full Risk/Executor/cycle services

## Acceptance

- [x] `python scripts/test_telegram.py` delivers a message to your phone
- [x] `uvicorn` dashboard shows events from ledger
- [x] Notify failure (bad token) does not raise in trading code
- [x] Local process runner: `python scripts/run_local_process.py`
