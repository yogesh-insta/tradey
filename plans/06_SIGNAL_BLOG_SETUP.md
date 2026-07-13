# Signal v1 — Humbled Trader blog setup (LOCKED)

Source: [Build an AI Trading Bot with Claude + IBKR](https://www.humbledtrader.com/blog/ai-trading-bot-claude-ibkr/)  
Local mirror: Cursor uploads `ai-trading-bot-claude-ibkr-0.md`

This doc locks **signal generation v1**. Strategy knobs may be refined later; the architecture split stays.

## Quality / modularity constraints

- Do **not** collapse Signal + Risk + Executor into one `cycle.py` blob.
- Clear package boundaries, type hints, pydantic contracts.
- Dry-run default for anything that could trade.
- Telegram notify on failures must never break the pipeline.
- Unit-test pure filter/prefilter/session math; network paths stay thin wrappers.

## Ownership split

| Concern | Owner | Notes |
|---|---|---|
| Universe (S&P 500 list) | Signal | `services/signal_generator/universe/sp500_tickers.py` |
| Morning gap prefilter | Signal | Yahoo chart API (httpx) → `watchlist.txt` (blog: yfinance) |
| D1–D3 / I1–I3 filters | Signal | Yahoo bars; emit `NormalizedSignal` |
| Time gates / max positions / sizing | Risk | Reads `rules.time_filter` + `rules.risk` |
| IBKR orders / client IDs | Executor | Paper Gateway `4002`; separate exec client id later |
| Ledger + Telegram | `packages/ledger`, `packages/notify` | Reused by scripts |

Blog `rules.risk` and `time_filter` live in `rules.json` for strategy fidelity, but **Risk Manager enforces them**.

## `rules.json` shape (exact blog keys)

```json
{
  "strategy_name": "Trend Join Long",
  "direction": "long_only",
  "trade_timeframe": "5m",
  "universe_filters": { "index": "S&P 500", "min_price_usd": 3.0 },
  "daily_filters": {
    "D1_above_prior_day_high": true,
    "D2_prior_close_above_sma200": true,
    "D3_min_gap_pct_from_prior_close": 3.0
  },
  "intraday_filters": {
    "I1_above_premarket_high": true,
    "I2_above_today_hod": true,
    "I3_rvol_min": 2.0,
    "I3_rvol_lookback_days": 14
  },
  "time_filter": {
    "earliest_entry_et": "10:05",
    "latest_entry_et": "15:30",
    "force_close_et": "15:51"
  },
  "exit": { "...": "lod_minus_1pct / partial / trail" },
  "risk": {
    "max_risk_per_trade_pct": 1.0,
    "max_position_size_pct_of_portfolio": 10,
    "max_concurrent_positions": 5
  }
}
```

## Filter meanings

**Daily:** D1 price > prior day high · D2 prior close > SMA200 · D3 gap ≥ 3%  
**Intraday:** I1 price > premarket high · I2 at/near today HOD · I3 rvol ≥ 2× (14d)

## Runtime flow (Phase A local)

```text
morning_prefilter.py  →  watchlist.txt
signal_cycle.py --dry-run
   Signal.scan_watchlist  →  NormalizedSignal[]
   Risk.evaluate_signal   →  SizedOrderIntent | RiskRejection
   (no IBKR unless future --no-dry-run + paper guard)
```

## Client IDs (when execution is wired)

- Cycle / signal orchestration: `IBKR_CLIENT_ID`
- Order placement subprocess: `IBKR_EXEC_CLIENT_ID`
- Never share one live socket across both processes

## Paper safety

- `PAPER_TRADING=true` + port `4002` (Gateway paper) or `7497` (TWS paper)
- Abort if paper flag + live port (`4001` / `7496`)
- Prefilter and dry-run **must** work with Gateway down

## Out of scope for this lock

- Full autonomous manage/trail/force-close loop (blog `cycle.py` position manager)
- Windows Task Scheduler / launchd pack
- TradingView webhooks
- Live trading
