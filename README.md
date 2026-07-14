# tradey

Local-first algorithmic trading monorepo (IBKR paper → cloud later).

## Quick start

```bash
cd __REPO_ROOT__
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

Default is **dry-run**. IB Gateway login is still manual. Dual-mode: `MARKET=us|asx` (default **us**).

```bash
./scripts/start_paper_day.sh                    # US dry-run (default)
./scripts/start_paper_day.sh --market asx       # ASX dry-run (Sydney session)
# TRADE_PAPER=1 ./scripts/start_paper_day.sh --market asx   # or --paper → --no-dry-run
./scripts/stop_paper_day.sh --market asx        # SIGTERM mode pids (+ shared dashboard)
./scripts/stop_paper_day.sh --all               # us + asx runners
```

Dashboard **Bot status** panel (and `GET /api/bot_status`): Running / Stopped / Stale, **market**, session phase, dry-run vs paper, last tick age, open managed count. Heartbeats: `data/{us|asx}/run/status.json`.

Overnight US (local Mac clock): start **21:30**; stop ~**09:00**. ASX daytime: start ~**09:30**; stop ~**16:30**. Run modes **sequentially** (distinct IBKR client ids: US `18`, ASX `19`). Optional LaunchAgents: [`deploy/launchd/`](deploy/launchd/README.md). Full clock map: [US session timeline](#us-session-timeline) / [ASX session](#asx-dual-mode).

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

`session_runner` auto-runs the gap prefilter → `data/{market}/watchlist.txt` (top 20) when the file is missing/stale (see `WATCHLIST_STALE_HOURS`, default 12). Runner `--dry-run` still writes the watchlist; it only skips IBKR orders. Each tick writes `data/{market}/run/status.json` for the dashboard.

### Dual-mode US + ASX

One shared stack (`MARKET=us|asx`). Isolated paths: `data/us/` vs `data/asx/` (watchlist, open positions, closed trades, status/pids). Rules: `rules.json` (US) / `rules.asx.json` (ASX gap scan, I1 off). Yahoo: US tickers as-is; ASX appends `.AX`. IBKR: US `SMART/USD` client **18**; ASX `SMART/AUD` + `primaryExchange=ASX` client **19**.

```bash
# US dry-run (default)
python scripts/session_runner.py --once --dry-run
./scripts/start_paper_day.sh

# ASX dry-run
python scripts/session_runner.py --market asx --once --dry-run
./scripts/start_paper_day.sh --market asx
python scripts/morning_prefilter.py --market asx --limit 30 --dry-run
```

ASX Sydney phases (continuous ~10:00–16:00): `too_early` &lt;10:00 → `manage_only` 10:00–10:05 → `ok` 10:05–15:30 → `manage_only` 15:30–15:51 → `force_close` 15:51–16:00 → `closed`. Sequential handoff: ASX day → stop → US overnight. See [`plans/09_ASX_LOCAL_PAPER.md`](plans/09_ASX_LOCAL_PAPER.md).

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

## US session timeline

Times below come from `rules.json` `time_filter` / `exit` / `risk` and `classify_session` (ET = `America/New_York`). **AEST (Jul)** columns use AEST = ET+14h (AU winter / US EDT). During AEDT (Oct–Apr), AEST numbers shift −1h relative to the Mac clock — LaunchAgents still fire on **local** wall clock (21:30 / 09:00).

Sources: `services/risk_manager/policy.py`, `services/session/*`, `services/position_manager/policy.py`, `scripts/session_runner.py`, `scripts/start_paper_day.sh`.

### Day clock (weekday)

| Phase | ET | ≈ AEST (Jul) | What runs |
|---|---|---|---|
| Bot start | ~07:30 | **21:30** (prior evening) | LaunchAgent / `start_paper_day.sh` — dashboard + `session_runner` (+ caffeinate). Process start, not a trade. |
| `too_early` | before 10:00 | before 00:00 | Idle ticks; **prefilter** may run (Yahoo → `watchlist.txt`). No manage, no entries. |
| `manage_only` | 10:00–10:05 | 00:00–00:05 | Manage open positions if any; prefilter if needed; **no new entries**. |
| `ok` | **10:05–15:30** | **00:05–05:30** | Scan + risk gates + entries; manage every tick. Only window for new orders. |
| `manage_only` | 15:30–15:51 | 05:30–05:51 | Manage only; no new entries. |
| `force_close` | **15:51–16:00** | **05:51–06:00** | Flatten managed longs (cancel stops + market sell). No partial/trail/entries. |
| `closed` | ≥ 16:00 | ≥ 06:00 | Early-exit ticks (no prefilter / scan / IB mutate). |
| Bot stop | ~19:00 | **09:00** | LaunchAgent / `stop_paper_day.sh` — SIGTERM processes. Not a trade trigger. |

Weekend → `weekend` (early-exit). Session codes also surface in dashboard Bot status.

### When is the top-20 watchlist built?

Short version: **prefilter is not a fixed clock** (not “always at 9:45 ET”). It runs on the **session_runner tick** (default **every 60 seconds** — `--interval 60`).

**Two different jobs**

| Job | What it does | When |
|---|---|---|
| **Prefilter** | Build / refresh the short list → `watchlist.txt` (at most **20** names) | On a tick, only if the list needs refresh |
| **Scan** | Analyze those names with D1–D3 / I1–I3 for entry signals | Every 60s tick during `ok` (10:05–15:30 ET) |

Prefilter = *make the 20-name list*. Scan = *look at that same list for trades each tick*. Scanning does **not** rebuild the 20 unless a refresh is needed.

**When prefilter actually runs**

- Only during `too_early` / `manage_only` / `ok` (not on weekend / after close).
- Only if the watchlist is **missing**, **empty** (and not already a completed empty screen for today ET), **not from today ET**, or **older than 12 hours** (`WATCHLIST_STALE_HOURS`, default 12).
- Otherwise that tick **skips** prefilter and keeps the existing list.
- Screens S&P 500 on Yahoo: gap **≥ 3%**, price **≥ $3**, sorted by gap % descending, capped at **20** (often fewer).
- **Never places orders.** Runner `--dry-run` still writes the file. `--skip-prefilter` disables auto-build. Manual: `python scripts/morning_prefilter.py`.

**During each phase**

- **`too_early`:** may build the list on the first eligible tick after start; then idle. **No orders.**
- **`ok` (10:05–15:30 ET):** every 60s tick **scans** the current watchlist; rebuilds only if refresh rules say so. Orders only here (after Risk). Max **5** concurrent. Default is dry-run; paper: `TRADE_PAPER=1` / `--paper` / `--no-dry-run`.

**Typical AU night (start ~21:30 AEST, Jul)**

1. ~21:30 AEST — bot starts; first tick often sees a missing/stale list → **prefilter runs once** → writes up to 20 names.
2. 21:30 → ~00:00 AEST (`too_early`) — ticks every 60s; list stays if still fresh; **no scanning for entries, no orders**.
3. ~00:05–05:30 AEST (`ok`) — every 60s tick **scans those same names**; does not rebuild 20 each minute.
4. After entry window — manage / force-close as in the day clock above; no new entries.

### Manage / exits

While positions are open (and always during `force_close`):

| Step | Rule |
|---|---|
| Stop-out | Protective stop fill matched by `stop_order_id` |
| Partial | Mark ≥ **0.75R** → sell ~1/3; stop on remainder → entry×0.99 |
| Breakeven | Mark ≥ **1.0R** → stop to entry |
| Trail | After BE: ratchet stop up via `swing_low_5m_2_2` (never down) |
| Force-close | 15:51–16:00 ET flatten remaining |

### What you see

| Surface | Signal |
|---|---|
| Dashboard Bot status | Running / Stopped / Stale; **phase** (`too_early`, `ok`, …); dry-run vs paper; last tick age; open managed count (`data/run/status.json`) |
| Telegram | `paper day started` / `paper day stopped`; prefilter ran/failed; manage events; tick errors |

## Layout

```text
packages/contracts/              shared schemas (signals, risk, execution, position, events)
packages/notify/                 Telegram alerts
packages/ledger/                 SQLite trade/event store
packages/observability/          expectancy / closed-trade helpers
services/market_profile.py       MARKET=us|asx thin switch (TZ, paths, Yahoo/IBKR)
services/signal_generator/       universe (S&P + ASX200), prefilter, D1–I3 filters
services/risk_manager/           session gates + sizing + daily-loss + AUD capital
services/position_manager/       exit ladder policy + JSON state (no IBKR)
services/order_executor/         ExecutionService + IbkrAdapter + paper guard
services/session/                entry_tick + manage_tick + prefilter_tick
services/dashboard/              local web UI
scripts/                         CLIs + session_runner + pnl/expectancy reports
rules.json / rules.asx.json      US Trend Join Long / ASX Gap Scan
data/us/ data/asx/               per-mode watchlist, positions, run status
data/experiments.md              change-control experiment log
plans/                           design docs
```
