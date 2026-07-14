# Local paper session — manage loop + day-cycle runner

**Status:** Implemented (Phase A code) — soak still open  
**Phase:** A (local Mac paper only)  
**Depends on:** [`06_SIGNAL_BLOG_SETUP.md`](06_SIGNAL_BLOG_SETUP.md) (signal lock), [`02_RISK_MANAGER.md`](02_RISK_MANAGER.md) (session gates + A$10k), [`03_ORDER_EXECUTOR.md`](03_ORDER_EXECUTOR.md) (paper entry + stop)

Fills the gap called out in `06` as out of scope: blog `cycle.py` **position manager** and a continuous US-session day loop — without collapsing into a monolith.

---

## Problem

Phase A can **enter** paper longs (`signal_cycle.py` → Risk → Executor + protective stop). It cannot yet:

1. Manage open trades through the blog exit ladder (partial TP → breakeven → trail)
2. Force-close at `force_close_et` (15:51 ET)
3. Run a full US session without repeatedly launching one-shot scripts by hand

Without this, “paper simulation” is entry-only and leaves unprotected/mismanaged inventory until someone intervenes in IB Gateway.

## Scope

- Local Mac + **IB Gateway paper on port 4002** (TWS paper 7497 also paper-guarded but not our setup)
- Position management per `rules.json` `exit` + session gates already in `classify_session`
- Continuous day-cycle runner: prefilter → entries in `ok` → manage every tick → force-close → idle
- Modular Signal / Risk / Executor (+ thin Position Manager); dry-run default; paper guard; A$10k sizing cap unchanged

## Non-goals

- TradingView webhooks
- Cloud / VPS / Docker compose (`00_CLOUD_INFRA`)
- Live ports (`4001` / `7496`) or live trading
- Monolith `cycle.py` blob (blog shape is the **behavior** reference, not the file layout)
- Short selling / options / futures
- Windows Task Scheduler
- Full ATO CSV export (ledger events for manage actions are enough for Phase A)
- Real-time paid market data (delayed paper data is acceptable)

---

## Architecture

Keep orchestration thin; put rules in testable modules.

```text
scripts/
  morning_prefilter.py          # exists — gap screen → watchlist.txt (also callable manually)
  signal_cycle.py               # exists — one-shot entry path
  session_runner.py             # day-cycle loop (or --once for tests)

services/session/               # thin orchestration helpers
  prefilter_tick.py             # stale watchlist → run_prefilter (top 20)
  entry_tick.py
  manage_tick.py

services/position_manager/      # NEW — exit policy + managed state (no IBKR SDK)
  policy.py                     # decide: partial | move_stop | trail | flatten | noop
  swing.py                      # swing_low_5m_2_2 from 5m bars
  state.py                      # load/save ManagedPosition store
  r_math.py                     # R = entry - initial_stop; trigger prices

services/risk_manager/          # exists — classify_session, sizing, daily-loss, A$10k
services/signal_generator/      # exists — watchlist scan → NormalizedSignal
services/order_executor/        # extend adapters — stop modify / partial / flatten
  paper_guard.py                # unchanged contract
```

### Ownership

| Concern | Owner |
|---|---|
| Session phase (`weekend` … `force_close`) | Risk (`classify_session`) |
| New entries + sizing + A$10k / daily-loss | Risk → Executor |
| Exit ladder decisions (when to partial / BE / trail) | Position Manager (pure) |
| IBKR order mutate / cancel / market close | Executor (`IbkrAdapter`) |
| Bars for trail swings / mark price | Signal data helpers or thin Yahoo/IB bars injected into Position Manager |
| Day loop timing + dry-run flag | `session_runner.py` |
| Persist open trade metadata | Position Manager state file (+ ledger events) |

### Why not blog `cycle.py`?

Blog Step 10 puts time gate, stop-out detection, manage, force-close, and entries in one script. That fights our locked quality bar (modular packages, pydantic contracts, unit-tested pure math). Replicate the **same time gates and exit ladder**, split across modules, with a thin runner that switches on `SessionStatus.code`.

---

## Session state machine

**Operator clock map (ET + ≈AEST Jul, bot start/stop, prefilter/scan/orders/exits):** see [README — US session timeline](../README.md#us-session-timeline).

Already implemented in `services/risk_manager/policy.py` — runner must call it every tick and branch. Times from `rules.json` `time_filter` (defaults below); hard day bounds match the blog (10:00 / 16:00 ET).

| Code | ET window (defaults) | New entries | Manage | Force flatten |
|---|---|---|---|---|
| `weekend` | Sat/Sun | no | no | no |
| `too_early` | before 10:00 | no | no | no |
| `manage_only` | 10:00–10:05 **or** 15:30–15:51 | no | yes | no |
| `ok` | 10:05–15:30 | yes | yes | no |
| `force_close` | 15:51–16:00 | no | no* | **yes** |
| `closed` | ≥ 16:00 | no | no | no |

\*During `force_close`, skip partial/trail; only flatten.

**Runner early-exit:** on `weekend` / `too_early` / `closed`, exit the tick in under ~1s (no Yahoo scan, no IB mutations). That makes “poll every N minutes all day” cheap — blog design requirement.

```text
                    ┌─────────────┐
         weekday    │  too_early  │
      before 10:00  └──────┬──────┘
                           │ 10:00
                    ┌──────▼──────┐
                    │ manage_only │◄──────────────┐
                    └──────┬──────┘               │
                           │ 10:05                │
                    ┌──────▼──────┐               │
                    │     ok      │── entries +   │
                    └──────┬──────┘   manage      │
                           │ 15:30                │
                    ┌──────▼──────┐               │
                    │ manage_only │───────────────┘
                    └──────┬──────┘
                           │ 15:51
                    ┌──────▼──────┐
                    │ force_close │── cancel stops + market SELL all
                    └──────┬──────┘
                           │ 16:00
                    ┌──────▼──────┐
                    │   closed    │
                    └─────────────┘
```

---

## Exit rules (`rules.json`)

```json
"exit": {
  "initial_stop_rule": "lod_minus_1pct",
  "partial_profit_trigger_R": 0.75,
  "partial_profit_fraction": 0.3333,
  "breakeven_trigger_R": 1.0,
  "post_breakeven_trail": "swing_low_5m_2_2"
}
```

### Definitions

- **Initial stop (entry time):** `lod_minus_1pct` → `stop = low_of_day * 0.99` (already in Risk `initial_stop_from_lod`).
- **1R:** `R = entry_fill_price - initial_stop_price` (positive for longs). Persist both on the managed position at entry.
- **Partial trigger:** mark ≥ `entry + partial_profit_trigger_R * R` (0.75R).
- **Partial size:** `ceil(qty * partial_profit_fraction)` with blog/Mac behavior ≈ `ceil(qty / 3)` for fraction `0.3333`. Never sell more than remaining qty − 1 if fraction would flatten early (keep at least 1 share for trail path when qty ≥ 2; if qty == 1, skip partial, wait for BE/trail/force-close).
- **Breakeven trigger:** mark ≥ `entry + breakeven_trigger_R * R` (1.0R).
- **Post-BE trail `swing_low_5m_2_2`:** on 5m bars, a swing low is a bar whose low is strictly lower than the **2** bars before **and** the **2** bars after. Candidate stop = `newest_completed_swing_low - 0.01`. **Ratchet only up** (never lower the stop). Apply only after the position has reached a post-breakeven state.

### Per-position lifecycle (blog Step 10, modularized)

| State | Condition / action |
|---|---|
| `pre_breakeven` | Default after entry + protective stop. |
| → partial | If still `pre_breakeven` and mark ≥ 0.75R: market SELL partial qty; replace stop on **remaining** at `entry * 0.99`; state → `post_breakeven_partial_done`. |
| → BE without partial | If mark ≥ 1.0R before/without taking partial first: cancel old stop; place stop at **entry**; state → `post_breakeven_no_partial`. |
| `post_breakeven_*` | Trail: if newest swing candidate > current stop, cancel + place higher stop. |
| stopped out | Detect via **`stop_order_id` match in fills** (not qty). Clear from store. |
| force-close | Cancel stop(s); market SELL remaining; clear store. |

**Ordering within a tick (important):**

1. Reconcile stop-outs (`stop_order_id` in recent fills — blog: last hour). **Do not match by filled quantity** (false stop-outs after partials — known Mac bug in the blog).
2. If session == `force_close` → flatten all and return.
3. Else manage each open position (partial / BE / trail) once per tick, idempotent flags so we don’t double-partial.
4. If session == `ok` → run entry path (reuse `signal_cycle` logic / shared function).
5. If `manage_only` → stop after manage (no scan/entries).
6. Persist state + append ledger / Telegram on meaningful transitions.

### Managed position record (minimum)

Persist locally (JSON file under `data/` or ledger-backed table — prefer JSON for Phase A parity with blog `open_positions.json`):

- `symbol`, `qty_remaining`, `entry_price`, `initial_stop`, `R`
- `stop_order_id`, `entry_order_id`
- `state`: `pre_breakeven` | `post_breakeven_partial_done` | `post_breakeven_no_partial`
- `partial_done: bool`, `breakeven_done: bool`
- `opened_at_et`, `account_id`

Reconcile qty against broker `positions()` each tick; if broker flat but state file still open → treat as external close and clear (notify).

---

## IBKR / ib_insync details

Current `IbkrAdapter.execute`: BUY limit → wait fill → separate GTC `StopOrder` SELL (not a full OCA bracket). Manage APIs should extend that model.

### Client IDs

- **Runner / portfolio / manage:** `IBKR_CLIENT_ID` (orchestration socket)
- **Optional dedicated exec:** `IBKR_EXEC_CLIENT_ID` if entry placement stays in a short-lived path
- Never two long-lived processes fighting the same `clientId`
- Prefer **one connected adapter** for the duration of a session-runner tick (same lesson as current adapter sync API: don’t tear down the event loop between ops)

### Patterns

| Action | Pattern |
|---|---|
| Modify stop price | `stop.auxPrice = new_px`; `placeOrder(contract, stop)` with **same `orderId`**. If IB returns “can’t find parent”, clear `parentId` on the modify (ib_insync [#647](https://github.com/erdewit/ib_insync/issues/647)). |
| Raise stop after partial | Cancel old stop **or** modify qty + auxPrice; safest Phase A: cancel → new `StopOrder` SELL for remaining qty; update `stop_order_id` in state. |
| Breakeven | Same as modify; `auxPrice = entry_price`. |
| Soft trail (swing) | Prefer **discrete stop moves** to swing level (blog). Do **not** require IB `TRAIL` order type for v1 — swing formula is strategy-specific. Optional later: convert to `orderType='TRAIL'` with `auxPrice` trail amount ([#629](https://github.com/erdewit/ib_insync/issues/629)). |
| Partial TP | `MarketOrder('SELL', partial_qty)`; then resize/replace protective stop. |
| Flatten / force-close | (1) `cancelOrder` open stops for symbol (2) `MarketOrder('SELL', abs(position))` (3) confirm flat / clear state. Cancel before opposite market to avoid racing the stop. |
| Stop-out detect | Match `execution.orderId` / fill to stored `stop_order_id`; ignore unrelated sells. |

### Adapter methods to add (sketch)

```text
IbkrAdapter
  modify_stop(symbol, stop_order_id, new_stop, qty) -> ExecutionResult
  cancel_order(order_id) -> None
  market_sell(symbol, qty) -> ExecutionResult   # partial or flatten
  flatten_symbol(symbol) -> ExecutionResult     # cancel stops + market sell position
  open_orders_for(symbol) -> list
```

All paths call `assert_paper_safe()` / `check_paper_guard` first. Dry-run: Position Manager still emits intents; Executor logs would-be actions and writes ledger `info` events without `placeOrder`.

---

## Runner / scheduling

### Phase A (preferred): in-process loop

```bash
python scripts/session_runner.py --dry-run --interval 60
# paper manage + entries when ready:
# python scripts/session_runner.py --no-dry-run --interval 60
python scripts/session_runner.py --once   # single tick (CI / manual)
```

Behavior:

- `while True` (or until `--until closed` / Ctrl+C): `classify_session` → branch → `sleep(interval)`.
- Off-hours / weekend: sleep longer optional (e.g. 5 min) but still early-exit work.
- Auto-refresh watchlist via `services/session/prefilter_tick.py` on `too_early` / `manage_only` / `ok` when `watchlist.txt` is missing, empty, not today (ET), or older than `WATCHLIST_STALE_HOURS` (default 12). Writes top-20 survivors; never places orders. `--skip-prefilter` disables. Runner `--dry-run` still writes the watchlist (Yahoo only).
- Mac must stay awake for the US session (user responsibility: `caffeinate` or Energy Saver). Document; don’t build a power-management daemon.

### Optional later (same Mac): launchd

Phase A start/stop scripts are preferred. Example LaunchAgents (Mac **local** clock):

- Start **21:30** weekdays → `scripts/start_paper_day.sh` (verify Bot status before bed; idles until US RTH)
- Stop **09:00** weekdays → `scripts/stop_paper_day.sh`

See [`deploy/launchd/`](../deploy/launchd/README.md). Absolute venv/path in plists; Gateway login remains manual.

Blog uses Windows Task Scheduler; on Mac, launchd is the analogue.

### What not to use in Phase A

- APScheduler / Celery / Redis (unnecessary local complexity)
- Cloud cron / VPS

---

## Safety

| Control | Rule |
|---|---|
| Dry-run default | `session_runner` and manage paths default to no orders |
| Paper guard | `PAPER_TRADING=true` + paper ports only; abort on live port mismatch |
| A$10k cap | Unchanged — Risk `CapitalConfig` / `sizing_portfolio_usd` on every entry tick |
| Daily loss kill | Still blocks **new** entries; manage/force-close still run |
| Force-close | Hard deadline — must run even if Signal/Yahoo fails |
| Telegram | Notify on partial / BE / trail / stop-out / force-close / runner errors; **never** raise into the pipeline |
| Idempotency | `partial_done` / `breakeven_done` flags; single tick lock optional |
| Qty truth | Broker position is source of truth; state file is strategy metadata |
| ClientId | No shared live socket across competing long-lived processes |

---

## Acceptance criteria

- [x] Outside RTH / weekend: `--once` returns in &lt;1s with session code logged, no IB mutate
- [x] `ok` + dry-run: scans + prints SizedOrderIntent path without orders
- [x] Open paper position: at 0.75R dry-run logs partial decision; `--no-dry-run` sells ~1/3 and replaces stop; state advances
- [x] At 1.0R: stop moves to entry (or entry×0.99 path after partial per ladder)
- [x] Trail only ratchets stop upward using `swing_low_5m_2_2`
- [x] Stop-out detected by `stop_order_id`, not qty (unit test covers partial-then-stop)
- [x] `force_close` tick: all managed longs flattened; state empty; Telegram high-priority notify
- [x] Paper guard blocks live ports; dry-run works with Gateway down for decision-only (manage ticks that need marks may skip or use Yahoo)
- [x] Entries still respect max concurrent + A$10k + daily-loss
- [x] No TradingView / cloud dependencies introduced
- [x] Unit tests for swing detection, R triggers, session branching of runner (mocked executor)

## Implementation todos

- [x] Contracts: `ManagedPosition`, `ExitAction` (partial / modify_stop / flatten / clear)
- [x] `services/position_manager/` — swing math, exit policy, JSON state store
- [x] Extend `IbkrAdapter` — modify/cancel stop, market sell, flatten
- [x] Wire ledger + Telegram events for manage transitions
- [x] `scripts/session_runner.py` — session switch, `--dry-run` / `--once` / `--interval`
- [x] Refactor shared “entry tick” from `signal_cycle.py` for runner reuse (no behavior change to one-shot CLI)
- [x] Tests: swing_low_5m_2_2, partial/BE ordering, stop-out id matching, force-close cancel-then-sell
- [x] Auto prefilter in day path (`prefilter_tick` + `--skip-prefilter` + stale threshold); unit tests for `needs_watchlist_refresh`
- [x] Local automation: `start_paper_day.sh` / `stop_paper_day.sh`, `data/run/status.json` heartbeats, dashboard Bot status + `/api/bot_status`, launchd 21:30/09:00 examples
- [ ] Soak: ≥1 full US paper session with force-close observed; positions match IB Gateway vs state file

---

## References

- [Build an AI Trading Bot with Claude + IBKR — Humbled Trader](https://www.humbledtrader.com/blog/ai-trading-bot-claude-ibkr/) — `rules.json` exit keys; Step 10 `cycle.py` time gate / manage / force-close; stop-out by `stop_order_id`; client ID split; Mac partial/qty bug note
- Repo lock: [`06_SIGNAL_BLOG_SETUP.md`](06_SIGNAL_BLOG_SETUP.md) — modular constraints; manage loop currently out of scope
- [`rules.json`](../rules.json) — live `time_filter` + `exit` + `risk`
- [`services/risk_manager/policy.py`](../services/risk_manager/policy.py) — `classify_session` already matches blog gates
- [ib_insync `IB.bracketOrder` / `placeOrder`](https://github.com/erdewit/ib_insync/blob/master/ib_insync/ib.py) — bracket helper; modify via re-`placeOrder` same id
- [ib_insync #647](https://github.com/erdewit/ib_insync/issues/647) — modify child stop: `parentId=None` workaround
- [ib_insync #629](https://github.com/erdewit/ib_insync/issues/629) — converting protective stop toward `TRAIL` (optional; v1 uses swing reprice)
- [IBKR trailing stops (product)](https://www.interactivebrokers.com/en/trading/orders/trailing-stops.php) — native TRAIL semantics if upgraded later
- macOS scheduling: launchd `StartInterval` / LaunchAgents for `--once` cadence; Phase A prefers in-process loop + manual/`caffeinate` soak
