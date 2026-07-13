# Signal Generator Module Plan

Owns **ideas only**. No sizing, no broker calls, no tax logic.

**Locked v1 source:** Humbled Trader blog pattern (`CustomModelSource`) — see [`06_SIGNAL_BLOG_SETUP.md`](06_SIGNAL_BLOG_SETUP.md). TradingView is optional later.

## Responsibility

- Maintain S&P 500 universe (hardcoded IBKR symbols)
- Morning yfinance prefilter → `watchlist.txt`
- Evaluate D1–D3 + I1–I3 from `rules.json`
- Emit `NormalizedSignal` to Risk Manager
- Never place orders; never size positions

## v1 scope (locked)

- **Source:** `CustomModelSource` — Trend Join Long via `rules.json`
- **Market data for filters:** Yahoo Finance chart API via httpx (blog uses yfinance; same data, more reliable locally)
- **Execution data:** IBKR stays in Order Executor
- **Output:** `NormalizedSignal` (`packages/contracts`)
- **Universe:** hardcoded `SP500_TICKERS` (~503), prefilter top ~20 gappers
- **Transport out (later):** HTTP POST to Risk Manager; local Phase A uses `scripts/signal_cycle.py`

## Plugin design

```text
SignalSource (interface)
  ├── CustomModelSource   # v1 LOCKED (blog scanner + filters)
  └── TradingViewSource   # optional later (same NormalizedSignal)
```

Swap via config (`SIGNAL_SOURCE=custom|tradingview`), not by editing Risk/Executor.

## Service layout

```text
services/signal_generator/
  rules.py                 # pydantic StrategyRules + load rules.json
  bars.py                  # yfinance helpers / IBKR↔Yahoo symbol map
  prefilter.py             # morning gap scan → watchlist
  filters.py               # D1–D3 / I1–I3 evaluation
  evaluate.py              # watchlist scan → NormalizedSignal list
  universe/sp500_tickers.py
scripts/morning_prefilter.py
scripts/signal_evaluate.py
scripts/signal_cycle.py    # dry-run default; wires Signal → Risk
```

## Non-goals (this module)

- Position sizing, max concurrent, session kill switches → Risk Manager
- IBKR order placement → Order Executor
- TradingView webhooks (deferred)

## Acceptance

- `rules.json` loads and validates
- Prefilter dry-run works with Gateway down
- Filter pure logic unit-tested
- Passing symbols become `NormalizedSignal` with reasons + metrics
- No live ports; no auto-orders from this module

## Todos

- [x] Lock blog `rules.json` + `StrategyRules`
- [x] Hardcoded S&P universe + morning prefilter
- [x] D1–D3 / I1–I3 → `NormalizedSignal`
- [ ] FastAPI `/v1/signals` producer (cloud Phase B)
- [ ] TradingViewSource behind config (optional)
