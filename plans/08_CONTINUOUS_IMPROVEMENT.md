# Continuous improvement — observe, tweak, soak (paper expectancy)

**Status:** Implemented (Phase A instrumentation) — soak / weekly review still open  
**Phase:** A (local Mac paper + IB Gateway)  
**Depends on:** [`07_LOCAL_PAPER_SESSION.md`](07_LOCAL_PAPER_SESSION.md) for manage/exit events; existing ledger + [`scripts/pnl_report.py`](../scripts/pnl_report.py) + dashboard for day observability; [`rules.json`](../rules.json) as the versioned rule surface

Honest framing: **no plan guarantees profits.** The goal is to raise **positive expectancy on paper** (average R per completed trade) by a disciplined loop: measure → hypothesize → tweak **one** knob → soak → compare. Income flow may follow if expectancy and sample size cooperate; treat “positive income” as an **outcome metric**, not a promise.

---

## 1. Goal & non-goals

### Goal

- Make **every module observable** so agents and humans can see *why* trades entered, how they sized, and *how* they exited.
- Track **expectancy in R**, win rate, avg win R / avg loss R, and dollar P&L (broker + ledger) on a rolling window.
- Run a **change-controlled** improvement loop against `rules.json` (and related params) that can evolve toward positive paper income **without** unsupervised live optimization.

### Non-goals

- Guaranteed profitability or “AI that finds the edge”
- Live trading / live ports (`4001` / `7496`)
- Auto-promote of `rules.json` without **human approve**
- Loosening paper guard, A$10k cap, or daily-loss kill to “get more trades”
- TradingView / cloud research pipelines (Phase B later)
- Full institutional walk-forward platform (keep a **lightweight** retail OOS discipline)
- Overfitting via overnight grid search on the same paper sample that will “validate” it

---

## 2. Observability surface (per module)

Prefer **structured ledger events** (`packages/ledger` `events` table + `metadata_json`) first; add a thin `trades` / `metrics` view later for closed-trade rollups. Do not invent a second write path until attribution needs it.

| Module | What to record | Suggested `event_type` / channel | Key `metadata` fields |
|---|---|---|---|
| **Signal** | Watchlist build; per-symbol pass/fail; emitted `NormalizedSignal` | `info` (`signal_eval`, `signal_emit`, `prefilter`) | `rules_version`, `filter_results` (D1–D3, I1–I3 bools), `metrics` (gap%, rvol, …), `reasons[]`, `signal_id`, `dry_run` |
| **Risk** | Approve / reject + sizing math | `info` (`risk_approve`, `risk_reject`) | `session_code`, `reject_reason`, `qty`, `risk_usd`, `R_budget`, `stop_price`, `portfolio_usd_used`, `day_pnl_usd`, `max_daily_loss_hit`, `rules_version` |
| **Executor** | Submit / fill / error (exists); extend manage fills | `order_submitted`, `fill`, `error` | `intent_id`, `signal_id`, `trade_id`, `stop_order_id`, `action_kind` (`entry` \| `partial` \| `stop_modify` \| `flatten`), `dry_run` |
| **Position Manager** | Partial / BE / trail / stop-out / force-close decisions | `info` (`pm_partial`, `pm_breakeven`, `pm_trail`, `pm_stop_out`, `pm_force_close`, `pm_noop`) | `trade_id`, `state_before` / `state_after`, `mark`, `R_progress`, `exit_stage`, `stop_old` / `stop_new`, `qty_before` / `qty_after` |
| **Session runner** | Tick summary; session transitions; early-exit | `info` (`session_tick`, `session_phase`) | `session_code`, `duration_ms`, `entries_attempted`, `manage_actions`, `errors`, `dry_run`, `interval_s` |

### Metrics to derive (daily / weekly — `pnl_report` extension or sibling script)

| Metric | Definition | Why |
|---|---|---|
| `win_rate` | wins / closed trades | Alone is insufficient |
| `avg_win_R` / `avg_loss_R` | Mean R on winners / losers | Structure of the edge |
| `expectancy_R` | `(WR × avg_win_R) − ((1−WR) × |avg_loss_R|)` or mean of per-trade `r_net` | Primary north star on paper |
| `profit_factor_R` | sum(+R) / \|sum(−R)\| | Robustness check |
| `day_pnl_usd` | Prefer IBKR day PnL; else ledger crude | Capital truth |
| `exit_attribution` | Counts + expectancy by `exit_reason` | Where edge leaks |
| `filter_attribution` | Expectancy by filter combo / session bucket | Which setups fire |

**R definition (must match plan 07):** `R = entry_fill − initial_stop` (longs). Per-leg or closed-trade `r_net = realized_pnl / R` (fees inside when using broker net). Do not mix planned R with broker-net R in the same formula without labeling the method.

### Existing hooks to reuse

- Ledger already stores append-only events + `metadata_json` ([`packages/ledger/store.py`](../packages/ledger/store.py)).
- [`scripts/pnl_report.py`](../scripts/pnl_report.py): day fill/event counts + optional IBKR snapshot — extend toward expectancy once closed trades exist.
- Dashboard (`127.0.0.1:8080`): surface closed-trade R and experiment tag when available.
- `NormalizedSignal.reasons` / `FilterEvaluation` already carry human-readable filter notes ([`packages/contracts/signals.py`](../packages/contracts/signals.py)).

---

## 3. Attribution schema (closed trade)

Every completed round-trip gets one logical **trade record** (ledger `metadata` glued by `trade_id`, or a small `closed_trades` table later). Minimum fields:

```text
trade_id              # uuid; minted at entry intent approval
rules_version         # see §5
experiment_id         # null | id from experiment log
symbol
signal_id
session_code_at_entry # ok | manage_only (should be rare) | …
entry_ts_utc / exit_ts_utc

# Why entered
filters_fired         # {D1,D2,D3,I1,I2,I3: bool}
signal_reasons[]      # from NormalizedSignal.reasons
signal_metrics{}      # gap_pct, rvol, lod, hod, …

# Sizing
qty_initial
entry_price
initial_stop
R
risk_usd
sizing_portfolio_usd
max_risk_pct_used

# Exit ladder path
partial_done          bool
breakeven_done        bool
trail_updates_count   int
exit_reason           partial | breakeven_then_stop | trail | stop | force_close | external
exit_stage_final      # same enum vocabulary as Position Manager states
qty_final             0
realized_pnl_usd
r_net                 realized_pnl_usd / R
fees_usd
```

**Exit reason discipline**

| `exit_reason` | When |
|---|---|
| `stop` | Fill matched `stop_order_id` (never by qty alone — plan 07) |
| `partial` | Only for the sold slice if you attribute legs separately; prefer one trade with nested `legs[]` |
| `trail` | Stop-out after ≥1 post-BE trail raise |
| `force_close` | Session `force_close` flatten |
| `external` | Broker flat, state cleared without matching our exit path |

Attribution question for weekly review: *Did losers skip partial/BE? Do force-closes dominate negative R after 15:30? Do gaps that barely clear D3 have worse expectancy?*

---

## 4. Improvement loop cadence

```text
measure → hypothesize → one tweak → soak → compare → promote or revert
```

| Cadence | Actions | Artifacts |
|---|---|---|
| **Daily (after US close or next morning AU)** | Run `pnl_report.py` (± `--ibkr --notify`); skim session/tick errors; note open vs closed | Day note in experiment log or Telegram |
| **Weekly** | Compute rolling expectancy (≥ N closed trades if available); break down by `exit_reason` + filter bins; **one** hypothesis max | Weekly review checklist (§ agent playbook) |
| **Soak before promote** | After a `rules.json` change: run ≥ **5 full US paper sessions** *or* ≥ **20 closed trades** (whichever comes second) before declaring a win | Compare `expectancy_R` and max DD vs baseline tagged with prior `rules_version` |
| **Promote** | Only if soak metrics ≥ baseline (or clear hypothesis confirmed) **and** human approves | Bump `rules_version`; archive prior rules snapshot |

**Sample-size humility:** Below ~20–30 closed trades, treat expectancy as noisy. Prefer not to declare edge; keep changing rate low.

---

## 5. Change control

### Rules versioning

- Add (or document) a `rules_version` string in `rules.json` (e.g. `"2026-07-14.a"`) **or** stamp `metadata.rules_version` from file hash / git SHA of `rules.json` at runtime.
- On every Signal / Risk / PM decision event, copy `rules_version` into ledger metadata.
- Keep dated snapshots under `data/rules_history/rules_<version>.json` when promoting (manual or tiny script — implementation later).

### Experiment log

Maintain `data/experiments.md` (or `data/experiments.jsonl`) with one row per change:

| Field | Example |
|---|---|
| `experiment_id` | `exp_2026-07-20_partial_R` |
| `date` | ISO |
| `hypothesis` | “Raising partial trigger from 0.75R → 0.9R reduces small winners that then stop out” |
| `change` | `exit.partial_profit_trigger_R: 0.75 → 0.9` |
| `baseline_rules_version` | `2026-07-14.a` |
| `candidate_rules_version` | `2026-07-20.b` |
| `soak_plan` | 5 sessions / 20 trades |
| `result` | `expectancy_R 0.05 → 0.12` / `revert` / `inconclusive` |
| `decision` | `promote` \| `revert` \| `extend soak` |
| `approved_by` | human |

### One change at a time

- **A/B or before/after:** only **one** parameter (or one tightly coupled pair documented as a single hypothesis) per experiment.
- Forbidden in the same soak: “tighten D3 gap **and** raise trail **and** cut max concurrent.”
- Prefer changing **exits / filters** before **risk limits**. Never widen risk limits to compensate for a weak signal.

### Lightweight walk-forward / OOS (retail)

Not a full WFO engine. Practical Phase A discipline:

1. **Baseline soak** on current `rules.json` → lock `rules_version` as in-sample paper baseline.
2. Propose tweak from attributed losses (not from cherry-picked winners).
3. **Holdout:** evaluate candidate only on **sessions after** the decision date (time-based OOS). Do not retune on those same sessions.
4. Optional later: split historical Yahoo bars into train / test windows for filter sensitivity; keep parameter grids tiny (QuantStart: minimise parameter count; avoid jumpy performance surfaces).
5. If candidate underperforms holdout → **revert**; do not immediately try the next three knobs on the same failed sample.

---

## 6. Agent playbook (Cursor)

### Observe (read-only by default)

1. `python scripts/pnl_report.py --date YYYY-MM-DD` and optionally `--ibkr --json`
2. Ledger: recent events / fills (`Ledger.recent`, DB at `data/ledger.sqlite3`)
3. Dashboard http://127.0.0.1:8080
4. Open manage state (plan 07 JSON) vs Gateway positions when runner exists
5. Current `rules.json` + `data/experiments.md` + last `rules_version`

### Propose

- Cite evidence: trade_ids, exit_reason histogram, expectancy Δ, sample size.
- Propose **exactly one** rules change + soak plan + kill criteria.
- Prefer filter/exit hypotheses grounded in attribution, not “more trades.”

### Require human approve

- Any edit to `rules.json`, risk env caps, paper-guard env, or Gateway ports.
- Agent may draft a patch / PR text; **must not** apply promote without explicit user OK.
- After approve: stamp version, append experiment log, start soak clock.

### Hard agent prohibitions

- Never disable / weaken `PAPER_TRADING`, paper-port guard, dry-run default, A$10k cap, or `max_daily_loss_pct` to increase fill count.
- Never “optimize” live or touch live ports.
- Never commit git unless the user asks.
- Never claim a change *will* produce profits — only that it is a testable hypothesis for paper expectancy.

### Suggested weekly agent prompt (pasteable)

```text
Read ledger + pnl_report for the last 5 US sessions. Summarize expectancy_R,
win rate, exit_reason breakdown, and any risk rejects. One hypothesis only;
do not edit rules.json. Ask me to approve before any change.
```

---

## 7. Safety rails

| Control | Rule |
|---|---|
| Dry-run default | Entries + manage stay dry-run until user opts in |
| Paper guard | Paper ports only; abort on live mismatch |
| A$10k ceiling | Unchanged; sizing uses proof capital | 
| Max daily loss | Blocks **new** entries; manage / force-close still run |
| Experiment kill switch | If after a promote, rolling 10-trade `expectancy_R` &lt; **−0.25R** *or* week DD &gt; daily-loss budget × N days → **auto-revert suggestion** + halt new *candidate* entries (stay on last good `rules_version` or dry-run) |
| Sample kill | If candidate soak ends with expectancy clearly below baseline → revert; do not stack further tweaks |
| Change freeze | During kill / revert: agents only observe + report |
| Telegram | Notify on promote / revert / kill; never raise into pipeline |

Kill switch is **paper process discipline**, not a guarantee against paper drawdowns.

---

## 8. Acceptance criteria + ordered todos

### Acceptance criteria

- [x] Every entry path stamps `signal_id`, `trade_id`, `rules_version`, session code, and filter reasons into ledger metadata
- [x] Every manage/exit path (plan 07) stamps `exit_reason` / PM stage into ledger
- [x] Closed-trade rollup yields `r_net`, win rate, `expectancy_R` (script or pnl_report extension)
- [x] `data/experiments.md` (or jsonl) exists with at least the schema above; one-change rule documented
- [ ] Agent playbook followed in a dry review (propose-only) without editing `rules.json`
- [x] Kill / revert criteria written and checked in weekly review checklist
- [x] No loosening of paper guard / A$10k / daily-loss in any accepted experiment
- [x] STATUS.md lists plan 08 as in progress / done when landed

### Ordered todos (instrumentation first — can start alongside / right after plan 07)

1. [x] **Stamp IDs on existing paths:** ensure `signal_id` / `intent_id` / proposed `trade_id` flow Signal → Risk → Executor metadata (tiny; no behavior change)
2. [x] **`rules_version`:** add field or hash stamp; copy into all decision/fill events
3. [x] **Plan 07 manage events:** ledger `pm_*` events with exit stages as position manager lands
4. [x] **Closed-trade rollup:** script or `pnl_report` mode → expectancy / R histogram / exit_reason counts
5. [x] **`data/experiments.md` template** + `data/rules_history/` convention
6. [x] **Dashboard:** past-10-days P/L strip + expectancy stub / last experiment line (read-only; `/api/pnl_days`)
7. [ ] **Weekly review checklist** doc section (this plan) used once end-to-end on paper soak
8. [x] **Kill-switch helper:** report-only first (flag when rolling expectancy collapses); optional auto dry-run later
9. [ ] **(Later)** Lightweight Yahoo OOS sensitivity for one filter at a time — still human-gated promote

---

## References

- [Humbled Trader — Importance of a Trade Journal](https://www.humbledtrader.com/blog/the-importance-of-a-trade-journal/) — journal setups, review discipline; pairs with their bot/`rules.json` workflow
- [Humbled Trader — AI Trading Bot + IBKR](https://www.humbledtrader.com/blog/ai-trading-bot-claude-ibkr/) — repo’s blog-shaped filters, exits, session cycle (behavior reference for what we attribute)
- [QuantStart — Successful Backtesting of Algorithmic Trading Strategies (Part I)](https://www.quantstart.com/articles/successful-backtesting-of-algorithmic-trading-strategies-part-i/) — optimisation / curve-fitting bias; keep parameter count low; sensitivity over blind search
- [QuantStart — Should You Build Your Own Backtester?](https://www.quantstart.com/articles/Should-You-Build-Your-Own-Backtester/) — in-sample inflation, regime change, OOS discipline
- Ernest Chan, *Quantitative Trading* / *Algorithmic Trading* (Wiley) — scientific method, simple robust systems, automation + risk; avoid overfitted complexity for retail capital
- [Expectancy & R-multiples (plain-English)](https://www.pnlledger.com/expectancy-r-multiples-the-plain-english-guide/) — expectancy as combination of win rate and avg win/loss in R
- [Walk-forward analysis overview (Surmount)](https://surmount.ai/walk-forward-analysis-vs-backtesting-pros-cons-best-practices) — rolling IS/OOS idea; Phase A uses a lightweight time-based holdout, not a full WFO stack
- Repo: [`07_LOCAL_PAPER_SESSION.md`](07_LOCAL_PAPER_SESSION.md), [`02_RISK_MANAGER.md`](02_RISK_MANAGER.md), [`rules.json`](../rules.json), [`scripts/pnl_report.py`](../scripts/pnl_report.py), [`packages/ledger/store.py`](../packages/ledger/store.py)
