# ATO Tax Module Plan

Owns **immutable records and export**. Not an accountant and not tax advice.

## Responsibility

- Append-only log of trading events from Order Executor
- Store enough detail for EOFY handoff (what/when/how much/costs)
- Export CSV (and later JSON) for accountant
- Tag market/currency/broker for multi-market future
- Never place orders or change risk

## Why it exists

Australian share-trading / business record-keeping needs clear execution history. This module is the system of record for **fills, fees, and corporate actions you choose to log**—not legal determination of investor vs business.

## v1 data model (append-only)

Event types examples:

- `order_submitted`, `fill`, `partial_fill`, `cancel`, `bracket_exit`, `fee`
- Fields: `event_id`, `timestamp_utc`, `broker`, `account_id`, `venue`, `symbol`, `currency`, `side`, `qty`, `price`, `fees`, `fx_rate_if_any`, `signal_id`, `order_intent_id`, `raw_ref` (broker order id)

Storage v1: SQLite (simple, file-backed). Postgres later if needed.

## Service layout

```text
services/ato_tax/
  app/main.py
  ledger.py
  models.py
  export.py              # CSV for EOFY
  storage/sqlite.py
  config.py
```

## Endpoints (v1)

- `POST /v1/events` — ingest normalized events (from Executor)
- `GET /v1/events` — query/filter
- `GET /v1/export/csv` — date-range export
- `GET /health`

## Non-goals

- Tax rulings, CGT calculation engine, BAS automation
- Deduction advice (laptop, VPS, data feeds)—optional **notes** field only if user enters costs manually later
- Rewriting history (corrections = compensating events, not DELETE)

## Acceptance

- Every paper fill from Executor appears in ledger
- CSV export opens cleanly and is accountant-readable
- Duplicate `event_id` is idempotent (no double count)
- No IBKR SDK dependency in this service

## Todos

- [ ] Define `FillEvent`/`OrderEvent` ledger schema in contracts + SQLite
- [ ] `POST /v1/events` with idempotent `event_id`
- [ ] CSV export by date range
- [ ] Tests for append-only, idempotency, export columns
