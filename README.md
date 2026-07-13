# tradey

Local-first algorithmic trading monorepo (IBKR paper → cloud later).

## Quick start

```bash
cd /Users/ym/mws26/tradey
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill TELEGRAM_* and IBKR_*
```

### Telegram test

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy token  
2. Message [@userinfobot](https://t.me/userinfobot) → copy chat id  
3. Put both in `.env`  
4. Run:

```bash
python scripts/test_telegram.py
```

### Dashboard

```bash
python -m uvicorn services.dashboard.app:app --host 127.0.0.1 --port 8080
```

Open http://127.0.0.1:8080

### Paper day (one start / one stop)

Default is **dry-run**. IB Gateway login is still manual.

```bash
./scripts/start_paper_day.sh              # dashboard :8080 + session_runner + caffeinate (+ Telegram)
# TRADE_PAPER=1 ./scripts/start_paper_day.sh   # or --paper → --no-dry-run
./scripts/stop_paper_day.sh               # SIGTERM via data/run/*.pid (+ Telegram; flatten is separate)
```

Dashboard **Bot status** panel (and `GET /api/bot_status`): Running / Stopped / Stale, session phase, dry-run vs paper, last tick age, open managed count. Heartbeats: `data/run/status.json`.

Overnight (local Mac clock, AEST/AEDT): start **21:30** so you can verify before bed; runner idles through `too_early`/prefilter until US RTH; stop ~**09:00**. Optional LaunchAgents: [`deploy/launchd/`](deploy/launchd/README.md).

### IBKR smoke

Gateway paper on port `4002`, API enabled, not read-only:

```bash
python scripts/test_ibkr_connect.py
python scripts/paper_buy_one.py   # optional; places 1-share paper order
```

### Signal v1 (blog pattern, dry-run)

```bash
python scripts/morning_prefilter.py --dry-run
python scripts/morning_prefilter.py
python scripts/signal_evaluate.py AAPL
python scripts/signal_cycle.py --dry-run
# Paper orders only when Gateway healthy (respects A$10k cap + paper guard):
# python scripts/signal_cycle.py --no-dry-run
pytest tests/ -q
```

IB Gateway is not required for prefilter / dry-run / unit tests.

### Overnight paper session

Prefer the one-shot scripts above. Manual runner:

```bash
# caffeinate is started by start_paper_day.sh; or: caffeinate -dimsu
python scripts/session_runner.py --once --dry-run
python scripts/session_runner.py --dry-run --interval 60
# When Gateway paper (port 4002) ready:
# python scripts/session_runner.py --no-dry-run --interval 60
# Skip auto gap prefilter (debug):
# python scripts/session_runner.py --once --dry-run --skip-prefilter
```

`session_runner` auto-runs the S&P gap prefilter → `watchlist.txt` (top 20) when the file is missing/stale (see `WATCHLIST_STALE_HOURS`, default 12). Runner `--dry-run` still writes the watchlist; it only skips IBKR orders. Each tick writes `data/run/status.json` for the dashboard.

### Proof capital (A$10,000)

Set in `.env` (see `.env.example`):

- `PORTFOLIO_VALUE_AUD=10000` — hard sizing / risk ceiling
- `USD_AUD=1.52` — approx AUD per 1 USD (not live FX); converts the AUD cap to USD notional for US stocks
- Risk uses `min(broker NetLiquidation USD, AUD / USD_AUD)`

### P&L + expectancy

```bash
python scripts/pnl_report.py
python scripts/pnl_report.py --ibkr --notify
python scripts/pnl_report.py --expectancy
python scripts/expectancy_report.py --json
```

Experiment log: `data/experiments.md`. Do not edit `rules.json` without human approve.

## Layout

```text
packages/contracts/              shared schemas (signals, risk, execution, position, events)
packages/notify/                 Telegram alerts
packages/ledger/                 SQLite trade/event store
packages/observability/          expectancy / closed-trade helpers
services/signal_generator/       universe, prefilter, D1–I3 filters
services/risk_manager/           session gates + sizing + daily-loss + AUD capital
services/position_manager/       exit ladder policy + JSON state (no IBKR)
services/order_executor/         ExecutionService + IbkrAdapter + paper guard
services/session/                entry_tick + manage_tick + prefilter_tick
services/dashboard/              local web UI
scripts/                         CLIs + session_runner + pnl/expectancy reports
rules.json                       Trend Join Long strategy (+ rules_version)
data/experiments.md              change-control experiment log
plans/                           design docs
```
