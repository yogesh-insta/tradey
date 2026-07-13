# Order Executor Module Plan

Owns **execution only**. Translates approved intents into broker orders and reports fills.

## Responsibility

- Accept `SizedOrderIntent` from Risk Manager
- Execute via `BrokerAdapter`
- Emit `FillEvent` / `OrderEvent` to ATO Tax (and optionally back to Risk for portfolio state)
- Expose read APIs: balance, positions, connection health
- No signal logic; no discretionary re-sizing (may reject if broker disconnect / qualify fails)

## Broker plugin design

```text
BrokerAdapter (interface)
  ├── IbkrAdapter         # v1 — ib-insync, paper port 4002
  └── PepperstoneAdapter  # later stub only
```

Config: `BROKER=ibkr`. Upstream services never import IBKR SDKs.

### IbkrAdapter v1 (local experiments first, then cloud)

- **Now:** connect to IB Gateway on the **local Mac** — paper port `4002` (or TWS `7497`)
- Host for local: `127.0.0.1`
- **Later:** same adapter, host=`ib-gateway` on Docker VPS ([`00_CLOUD_INFRA.md`](00_CLOUD_INFRA.md))
- Qualify contracts for US stocks/ETFs
- Place entry limit + protective stop from intent
- Map IBKR callbacks → normalized `ExecutionResult` / ledger fill
- Keep IBKR quirks inside adapter (overnight margin, clientId, reconnect, sync `_run` wrapper)

Prerequisite: IB Gateway running locally with API enabled (see [`IBKR_MANUAL_SETUP.md`](../IBKR_MANUAL_SETUP.md)). Cloud deploy comes after local paper works.

## Service layout

```text
services/order_executor/
  app.py                   # thin FastAPI (health, balance, positions, orders)
  brokers/base.py
  brokers/ibkr.py
  brokers/pepperstone.py   # stub
  execution_service.py     # intent → adapter → ledger + notify
  paper_guard.py
  config.py
```

Wired from `scripts/signal_cycle.py --no-dry-run` (paper guard + portfolio snapshot + execute).

## Endpoints (v1)

- `POST /v1/orders` — execute SizedOrderIntent
- `GET /v1/balance`
- `GET /v1/positions`
- `GET /health` — includes broker connected true/false

## Non-goals

- TradingView webhooks
- Tax report formatting (ATO module)
- Live trading until paper soak passes
- Second broker implementation beyond stub

## Acceptance

- On **cloud VPS** with ib-gateway paper healthy: intent → entry + stop in IBKR
- Laptop off: executor still works
- Disconnect → 503, no silent “success”
- Fill events posted to ATO with broker-neutral schema
- Switching `BROKER` config does not require Risk/Signal code changes

## Todos

- [x] `BrokerAdapter` interface + `IbkrAdapter` (local paper `4002`; cloud host later)
- [x] FastAPI orders/balance/positions/health endpoints
- [x] `PepperstoneAdapter` stub implementing interface
- [x] `ExecutionService` + wire from `signal_cycle --no-dry-run`
- [ ] Emit normalized fill events to dedicated ATO Tax service (ledger append done)
- [ ] Paper integration test **on VPS** against compose `ib-gateway`
