# Plan implementation status

Last checked: 2026-07-14 (plan 09 dual-mode US+ASX implemented; ASX dry-run ready)

**Signal approach (locked v1):** Humbled Trader blog pattern — `rules.json` + morning gap prefilter + D1–D3/I1–I3 → `NormalizedSignal`. TradingView optional later. See [`06_SIGNAL_BLOG_SETUP.md`](06_SIGNAL_BLOG_SETUP.md).

**Quality bar:** modular Signal / Risk / Executor / Position Manager boundaries (no monolith `cycle.py`); pydantic contracts; dry-run default; paper-port guard; unit tests for pure filter/session/sizing/exit math; Telegram notify never breaks the pipeline; injectable bar download for offline prefilter tests.

**Market data:** Phase A screening/filters use Yahoo Finance **chart API via httpx** (blog uses yfinance; same data, fewer scraper breakages). Retry/backoff + early abort on Yahoo-wide failure.

**Proof capital:** `PORTFOLIO_VALUE_AUD=10000` (A$10k ceiling). US: Risk sizes against `min(broker NetLiquidation USD, AUD/USD_AUD)`. ASX: native AUD ceiling (no FX). `USD_AUD` is approx AUD per 1 USD (default 1.52) — not a live FX feed; update periodically.

| Plan / area | Status | Notes |
|---|---|---|
| IBKR manual setup / local Gateway | **Done** | Paper connect + fill worked (`DUxxxxxx`) |
| Telegram notify | **Done** | `@Tradey1122bot` → `@alphayogi` |
| Dashboard | **Done** | http://127.0.0.1:8080 |
| Ledger (ATO v0) | **Partial** | SQLite events; `scripts/pnl_report.py` for day summary; full ATO export later |
| Signal Generator / blog setup (Phase A) | **Done** | `services/signal_generator/`: rules, S&P universe (~503), prefilter, D1–D3/I1–I3 → `NormalizedSignal`; CLIs. FastAPI `/v1/signals` pending (Phase B). |
| Risk Manager | **Partial** | Session/size/concurrency + **daily-loss kill** + **AUD 10k capital cap**; portfolio snapshot via Executor; FastAPI `/v1/signals` still Phase B |
| Order Executor service | **Partial** | `BrokerAdapter` + `IbkrAdapter` + manage APIs (modify/cancel stop, market sell, flatten); `ExecutionService`; thin FastAPI; paper guard. ATO fill fan-out / VPS soak still open |
| Local paper session (manage + day-cycle) | **Done (Phase A code)** | [`07_LOCAL_PAPER_SESSION.md`](07_LOCAL_PAPER_SESSION.md) — manage + `session_runner`; start/stop scripts + `status.json` + dashboard Bot status; soak still user-run |
| Continuous improvement (observe → tweak → soak) | **Done (Phase A instrumentation)** | [`08_CONTINUOUS_IMPROVEMENT.md`](08_CONTINUOUS_IMPROVEMENT.md) — `trade_id`/`rules_version`, expectancy report, `data/experiments.md`; no profit guarantee |
| Dual-mode US + ASX (plan 09) | **Done (Phase A code)** | [`09_ASX_LOCAL_PAPER.md`](09_ASX_LOCAL_PAPER.md) — `MARKET=us\|asx`; shared stack; `data/us` vs `data/asx`; `rules.asx.json`; ASX200; client ids 18/19; US default unbroken |
| Cloud infra (Phase B) | **Not started** | Local Phase A only |
| TradingView webhook | **Deferred** | Optional after CustomModelSource soak |

## How to run signal v1 (paper-safe)

```bash
source .venv/bin/activate
# Optional one-shot (session_runner also auto-runs when watchlist stale):
python scripts/morning_prefilter.py --dry-run --limit 20
python scripts/morning_prefilter.py          # writes data/us/watchlist.txt
python scripts/morning_prefilter.py --market asx --limit 30
python scripts/signal_evaluate.py AAPL
python scripts/signal_cycle.py --dry-run     # default; no orders; A$10k sizing cap
# Only when Gateway healthy + paper guard ok (places paper orders):
# python scripts/signal_cycle.py --no-dry-run
pytest tests/ -q
```

Gateway may be down for prefilter / dry-run / unit tests. If Yahoo returns 429, wait and retry; CLIs exit non-zero with JSON error.

## Overnight paper session (plan 07) + dual-mode (plan 09)

```bash
source .venv/bin/activate
# US (default):
./scripts/start_paper_day.sh
# ASX daytime dry-run:
./scripts/start_paper_day.sh --market asx
./scripts/stop_paper_day.sh --market asx
# Paper orders when Gateway paper (4002) ready:
# ./scripts/start_paper_day.sh --market asx --paper

# Manual one-shot / loop:
python scripts/session_runner.py --once --dry-run
python scripts/session_runner.py --market asx --once --dry-run
python scripts/session_runner.py --market asx --dry-run --interval 60
```

Dashboard http://127.0.0.1:8080 shows **Bot status** (market, Running/Stopped/Stale, phase, dry-run, last tick age, open count). API: `GET /api/bot_status`. Heartbeat: `data/{us|asx}/run/status.json`.

Session gates: entries only in `ok`; manage in `ok`/`manage_only`; force-close flatten at `force_close`; early-exit on weekend/`closed` (and `too_early` after optional prefilter). Auto-refreshes watchlist (top 20 gappers) on `too_early`/`manage_only`/`ok` when missing, empty, not today (market TZ), or older than `WATCHLIST_STALE_HOURS` (default 12). State: `data/{market}/open_positions.json`.

## Observability / expectancy (plan 08)

```bash
python scripts/pnl_report.py                 # ledger day summary
python scripts/pnl_report.py --expectancy    # + win rate / expectancy_R / exit rollup
python scripts/expectancy_report.py --json
# Dashboard (127.0.0.1:8080): past-10-days P/L strip; API GET /api/pnl_days?days=10
```

Closed trades append to `data/closed_trades.jsonl` (preferred day P/L). Without them the dashboard falls back to ledger crude sell−buy notional and notes it in the UI. Experiment log: `data/experiments.md`. Current `rules_version` in `rules.json` (stamp on ledger events). Agents: observe/propose only — do not edit `rules.json` without human approve.

## Tomorrow P&L

```bash
python scripts/pnl_report.py                 # ledger summary
python scripts/pnl_report.py --ibkr --notify # + Gateway snapshot + Telegram
```

Or ask the agent tomorrow for a P&L update.

## Overnight services (local Phase A)

- One command: `./scripts/start_paper_day.sh` (dry-run default) → dashboard `127.0.0.1:8080` + `session_runner` + `caffeinate`
- Stop: `./scripts/stop_paper_day.sh` (flatten separate)
- Bot status on dashboard / `GET /api/bot_status` from `data/run/status.json`
- Optional launchd: start **21:30** local / stop **09:00** — [`deploy/launchd/`](../deploy/launchd/README.md)
- Prefer dry-run; do **not** enable live trading; Gateway login still manual
