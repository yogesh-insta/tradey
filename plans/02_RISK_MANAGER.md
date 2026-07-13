# Risk Manager Module Plan

Owns **whether and how much** to trade. Heart of account survival.

## Responsibility

- Consume `NormalizedSignal`
- Apply hard risk rules (including blog `rules.risk` + `time_filter`)
- Produce `SizedOrderIntent` (approved) or `RiskRejection`
- Attach stop intent from strategy exit rule (e.g. `lod_minus_1pct`)
- Never talk to IBKR directly; never invent signals

## v1 risk policy

Aligned with blog Trend Join Long + **AUD proof capital A$10,000**:

- Max concurrent from `rules.risk.max_concurrent_positions` (blog default 5; may tighten for small accounts)
- Risk per trade `%` from `rules.risk.max_risk_per_trade_pct`
- Max position notional `%` of portfolio
- Session gates ET: earliest/latest entry + force-close window (no new entries outside `ok`)
- Reject SELL/short unless explicitly enabled (v1 long-only)
- Minimum size: qty < 1 → reject
- **Daily loss kill:** halt new entries when day PnL ≤ `-max_daily_loss_pct` of sizing portfolio
- **Capital ceiling:** `PORTFOLIO_VALUE_AUD=10000`; sizing USD = `min(broker NL USD, AUD / USD_AUD)`

## Module layout (local Phase A — no FastAPI required yet)

```text
services/risk_manager/
  policy.py              # classify_session, size_long, daily_loss, evaluate_signal
  capital.py             # AUD proof-cap → USD sizing notional
packages/contracts/risk.py
```

FastAPI `/v1/signals` comes in cloud Phase B; keep policy pure and importable.

## Inputs / outputs

- In: `NormalizedSignal` (+ portfolio open count / day PnL from Executor snapshot)
- Out: `SizedOrderIntent` → Order Executor
- Out: `RiskRejection` (logged / notified)

## Non-goals

- yfinance scanning / D1–I3 filters (Signal)
- Broker order types (Executor)
- Guaranteeing returns

## Acceptance

- Outside entry window → reject
- At max concurrent → reject
- Oversized → capped via risk%/position% math
- Approved intent has integer qty + stop_price
- Daily loss breach → reject
- Sizing never exceeds A$10k proof-cap (converted to USD)

## Todos

- [x] `SizedOrderIntent` + `RiskRejection` contracts
- [x] Session gate + sizer + evaluate_signal module
- [x] Daily loss kill switch
- [x] Portfolio snapshot from Executor read API (via `signal_cycle` / `ExecutionService`)
- [x] AUD proof-capital ceiling (`PORTFOLIO_VALUE_AUD` + `USD_AUD`)
- [ ] FastAPI `/v1/signals` forwarder
