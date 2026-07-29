# Dual-mode local paper — US + ASX

**Status:** Phase A code done (dual-mode dry-run); paper soak TBD  
**Phase:** A (local Mac paper only)  
**Depends on:** [`07_LOCAL_PAPER_SESSION.md`](07_LOCAL_PAPER_SESSION.md), [`06_SIGNAL_BLOG_SETUP.md`](06_SIGNAL_BLOG_SETUP.md), [`04_ATO_TAX.md`](04_ATO_TAX.md)

The bot runs in **two modes** — **US markets** and **ASX** — on **one codebase**. ASX does **not** replace US. A thin **market mode** switch (`MARKET=us` | `MARKET=asx`) selects timezone, universe, Yahoo mapping, IBKR contract defaults, rules file, and per-mode data paths so modes never clobber each other.

---

## 1. Goal / non-goals

### Goal

- **Dual-mode:** keep full US S&P / Humbled Trader local paper path; **add** ASX cash equities as a second first-class mode
- Single shared pipeline per process: prefilter → scan → Risk → Executor (+ stop) → Position Manager → force-close
- Mode selected by env and CLI: `MARKET=us|asx` on `start_paper_day.sh` / `session_runner.py` (default **`us`** so existing overnight soak stays intact)
- **Sequential** on one Mac: ASX daytime → stop → US overnight (or separate LaunchAgents with non-overlapping windows)
- Proof capital `PORTFOLIO_VALUE_AUD=10000` — US still sizes via `USD_AUD`; ASX sizes in native AUD (no FX)

### Non-goals

- **No fork:** no `services_asx/`, no copy-paste second stack
- **No dual-market in one process** (v1): one `MARKET` per `session_runner` / start script invocation
- Cloud / TradingView / live ports / shorts / options
- Paid real-time ASX feeds (Yahoo delayed + IBKR paper OK for Phase A)
- Full ATO CGT engine (ledger `venue` / `currency` tags only)
- Replacing or deleting US overnight tooling

---

## 2. How dual-mode works

```text
                    ┌─────────────────────────────────────┐
                    │  Shared: Signal / Risk / Executor /  │
                    │  Position Manager / session_runner / │
                    │  ledger / dashboard / notify         │
                    └──────────────┬──────────────────────┘
                                   │
              market_profile(MARKET=us|asx)
                                   │
           ┌───────────────────────┴───────────────────────┐
           ▼                                               ▼
    MARKET=us                                       MARKET=asx
    America/New_York                                Australia/Sydney
    rules.json                                      rules.asx.json
    S&P 500 universe                                ASX 200 universe
    Yahoo: AAPL                                     Yahoo: BHP.AX
    IBKR: SMART / USD                               IBKR: SMART+ASX / AUD
    data/us/…                                       data/asx/…
```

| Concern | `MARKET=us` | `MARKET=asx` |
|---|---|---|
| Timezone / session gates | `America/New_York` + `rules.json` `time_filter` | `Australia/Sydney` + `rules.asx.json` times |
| Universe | `sp500_tickers.py` | `asx200_tickers.py` (new) |
| Yahoo map | existing (class shares, etc.) | IBKR symbol → `.AX` |
| IBKR | `Stock(sym, "SMART", "USD")` | `Stock(sym, "SMART", "AUD")` + `primaryExchange="ASX"` |
| Rules | `rules.json` | `rules.asx.json` |
| Watchlist / open state / run heartbeat | under `data/us/` (or `*.us.*`) | under `data/asx/` (or `*.asx.*`) |
| Capital sizing | `min(NL USD, AUD/USD_AUD)` | AUD ceiling directly |
| Start/stop | overnight LaunchAgents (existing) | daytime agents (new, optional) |

**CLI / env (target):**

```bash
# US (default — unchanged behavior)
./scripts/start_paper_day.sh
MARKET=us ./scripts/start_paper_day.sh --paper
python scripts/session_runner.py --market us --dry-run --interval 60

# ASX
MARKET=asx ./scripts/start_paper_day.sh
python scripts/session_runner.py --market asx --once --dry-run
./scripts/stop_paper_day.sh   # stops the active mode’spids; see §4
```

US remains default when `MARKET` / `--market` omitted.

---

## 3. What reuses vs what is new

### Shared (inject profile — do not fork)

| Layer | Reuse |
|---|---|
| `packages/contracts`, `notify`, `ledger`, `observability` | Unchanged; tag `market` / venue / currency on events |
| `filters.py` D1–I3, gap %, RVOL | Pure math shared |
| `prefilter.py`, `services/session/*` | Parameterize from profile |
| Risk: `classify_session`, `size_long`, daily-loss, concurrency | Same phase codes; TZ + HH:MM + capital path from profile |
| Position Manager exit ladder | Unchanged |
| Executor + `paper_guard` | Unchanged; contract fields from intent / profile |
| `session_runner`, start/stop scripts, dashboard | Same binaries; `MARKET` selects profile + data root |

### New-only (thin)

| Piece | What |
|---|---|
| `services/market_profile.py` | Resolve mode → paths, TZ, Yahoo/IBKR defaults, rules path |
| `universe/asx200_tickers.py` | ASX 200 list (IBKR symbols, no `.AX`) |
| `rules.asx.json` | Sydney times + ASX universe knobs + adapted I1 |
| Per-mode data dirs | See §5 — isolation so modes don't clobber |
| Daytime LaunchAgents | Optional ASX 09:30 / 16:30 — **alongside** US overnight agents |
| Distinct `IBKR_CLIENT_ID` per mode | Avoid Gateway collision if both ever overlap |

### Explicit anti-pattern

```text
❌ services_asx/   ❌ session_runner_asx.py   ❌ “ASX replaces US”
```

---

## 4. Scheduling — sequential on one Mac

Markets do not overlap in wall-clock usefulness for AU:

| Window (Mac local, approx) | Mode | Notes |
|---|---|---|
| **~09:30 → ~16:30** | `MARKET=asx` | Sydney RTH; start before 10:00, stop after force-close |
| **~16:30 → ~21:30** | idle / handoff | Optional; Gateway stays up |
| **~21:30 → ~09:00** | `MARKET=us` | Existing overnight US RTH (ET); stop after US force-close |

**Operator pattern (recommended v1):**

1. Morning: `MARKET=asx ./scripts/start_paper_day.sh` → ASX day  
2. After ASX stop (~16:30): `./scripts/stop_paper_day.sh`  
3. Evening: `MARKET=us ./scripts/start_paper_day.sh` (or existing LaunchAgent) → US night  
4. Morning: US stop → optional ASX start again  

**Optional LaunchAgents (two pairs — do not share one plist):**

| Agent | Fire | Action |
|---|---|---|
| `tradey.asx.start` | weekdays **09:30** local | `MARKET=asx ./scripts/start_paper_day.sh` |
| `tradey.asx.stop` | weekdays **16:30** local | stop ASX pids |
| `tradey.us.start` | weekdays **21:30** local | `MARKET=us ./scripts/start_paper_day.sh` (existing) |
| `tradey.us.stop` | weekdays **09:00** local | stop US pids |

**Overlap / clientId warning:**

- v1 assumes **one active runner** at a time. If ASX stop is late and US start fires, both may fight for Gateway API slots.
- Assign **different `IBKR_CLIENT_ID`** per mode (e.g. US `18`, ASX `19`) in `.env` / profile — IBKR rejects duplicate client ids on the same Gateway.
- Prefer mode-scoped pid files (`data/us/run/*.pid`, `data/asx/run/*.pid`) so stop scripts only kill the intended mode.
- Dashboard: one process per host is OK; prefer writing `market=` into `status.json` under that mode’s run dir (or multiplex later).

**Do not** run ASX + US paper **paper-trading the same account concurrently** without proving isolated client ids + non-overlapping manage state — sequential is the supported design.

---

## 5. Isolated paths (no clobber)

Prefer directory roots (clearer than long filename suffixes):

```text
data/us/
  watchlist.txt
  open_positions.json
  run/status.json
  run/*.pid
data/asx/
  watchlist.txt
  open_positions.json
  run/status.json
  run/*.pid

rules.json          # US
rules.asx.json      # ASX
```

Migration note: today’s flat `watchlist.txt` / `data/open_positions.json` / `data/run/` become the **US** tree (move or symlink when implementing). ASX never writes into US paths.

Alternative equally acceptable: `watchlist.us.txt` / `watchlist.asx.txt` + `open_positions.us.json` — pick one style in Phase 1 and stick to it.

---

## 6. ASX strategy v1 (US strategy unchanged)

**US:** keep locked Trend Join Long / `rules.json` as today.  
**ASX:** **simpler gap scan first**, then adapt blog filters — do not paste I1 premarket unchanged.

| Concern | ASX adaptation |
|---|---|
| I1 “premarket high” | ASX pre-open + ~09:59 auction ≠ US premarket; disable I1 or use “above open / first 5m high” |
| Liquidity | ASX 200 only; spreads wider — expect fewer survivors |
| Entry clock | earliest **10:05** Sydney (past open noise) |
| Data | Yahoo `.AX` ~20 min delay — OK for screen/dry-run |

Suggested `rules.asx.json` knobs: gap 2–3%, `min_price_aud` ≥ 0.50–1.00, I3 RVOL start ~1.5 or observe, exit ladder / risk % can match US.

---

## 7. ASX session timeline (Sydney)

`ZoneInfo("America/New_York")` stays for US. ASX uses `Australia/Sydney` (AEST/AEDT). Continuous trading ~10:00–16:00 Sydney (~09:59:45 after OSPA).

| Phase | Sydney | What runs |
|---|---|---|
| Bot start | **09:30** | `MARKET=asx` start — dashboard + runner |
| `too_early` | before 10:00 | Prefilter → `data/asx/watchlist.txt`; no entries |
| `manage_only` | 10:00–10:05 | Manage only |
| `ok` | **10:05–15:30** | Entries + manage |
| `manage_only` | 15:30–15:51 | Manage only |
| `force_close` | **15:51–16:00** | Flatten |
| `closed` | ≥ 16:00 | Early-exit |
| Bot stop | **16:30** | ASX stop (hand off to US evening) |

**Settlement:** T+2 CHESS (paper-irrelevant). **Lots:** 1 share OK. **Stamp duty:** abolished on quoted ASX securities (2001). Early close (e.g. 24/31 Dec 14:10): manual skip in v1.

US timeline remains in README — unchanged.

---

## 8. IBKR / data notes

**ASX contract (same Gateway paper `4002`):**

```python
Stock(symbol, "SMART", "AUD")  # primaryExchange="ASX"
```

Spike: qualify + 1-share paper `BHP`/`CBA` before full ASX runner. Yahoo: `BHP.AX` → IBKR `BHP`.

**Universe sources:** S&P/ASX 200 via Wikipedia or IOZ (iShares) holdings proxy; All Ords out of scope for v1.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Mode path clobber | Enforced `data/us` vs `data/asx` (+ mode in status/pids) |
| Dual runners / same clientId | Sequential schedule; distinct `IBKR_CLIENT_ID`; stop-before-start |
| Yahoo `.AX` quality | Delay/gaps; screen + dry-run; abort on wide failure |
| IBKR paper ASX availability | Phase 0 spike before runner |
| Liquidity | ASX 200; lower concurrency if needed |
| Operator error (`MARKET` wrong) | Default `us`; echo market in Telegram start + dashboard |

### Tax (light)

Ledger tags `venue` / `currency` / market per [`04_ATO_TAX.md`](04_ATO_TAX.md). Paper ≠ tax event. No stamp duty on listed transfers. Not advice.

---

## 10. Phased todos

### Phase 0 — ASX research spike

- [ ] Qualify + 1-share paper ASX stock on Gateway
- [ ] Sample Yahoo `.AX` gap / 5m bars
- [ ] Decide ASX I1 (off vs open-high)

### Phase 1 — Market profile + path isolation

- [ ] `market_profile.py` (`us` \| `asx`)
- [ ] Move/alias existing flat paths → `data/us/` (US default unbroken)
- [ ] `data/asx/` + `rules.asx.json` + `asx200_tickers.py`
- [ ] `MARKET` / `--market` on start scripts + `session_runner`

### Phase 2 — Session clock per mode

- [ ] Profile-driven TZ + HH:MM in `classify_session`
- [ ] Unit tests for both US and ASX phase tables

### Phase 3 — ASX dry-run + dual-mode soak docs

- [ ] ASX prefilter → `data/asx/watchlist.txt`
- [ ] `session_runner --market asx --once --dry-run`
- [ ] Document sequential handoff; optional ASX LaunchAgents; clientId split

### Phase 4 — ASX paper day

- [ ] Paper entry + manage + force-close in `MARKET=asx`
- [ ] Confirm US overnight still works same night (after ASX stop)
- [ ] `data/experiments.md` note

Stop if Phase 0 ASX fills fail.

---

## 11. References

- [ASX cash market trading hours](https://www.asx.com.au/markets/market-resources/trading-hours-calendar/cash-market-trading-hours)
- [ASX trading calendar](https://www.asx.com.au/markets/market-resources/trading-hours-calendar/cash-market-trading-hours/trading-calendar)
- [ASX Settlement (T+2)](https://www.asx.com.au/markets/clearing-and-settlement-services/asx-settlement)
- [IBKR API contracts](https://www.interactivebrokers.com/campus/ibkr-api-page/contracts/)
- Yahoo `.AX` ~20 min delay: [Yahoo exchanges list](https://au.help.yahoo.com/kb/finance-for-web/exchanges-data-providers-yahoo-finance-sln2310.html)
- [S&P/ASX 200](https://www.spglobal.com/spdji/en/indices/equity/sp-asx-200/); IOZ holdings as constituent proxy
- Stamp duty on quoted securities abolished 1 Jul 2001
- Internal: [`07_LOCAL_PAPER_SESSION.md`](07_LOCAL_PAPER_SESSION.md), [`06_SIGNAL_BLOG_SETUP.md`](06_SIGNAL_BLOG_SETUP.md), [`STATUS.md`](STATUS.md), README US session timeline

---

## Acceptance (later implementation)

- [ ] `MARKET=us` behavior matches today’s US paper day (paths may live under `data/us/`)
- [ ] `MARKET=asx` uses **same** Signal / Risk / Executor / PM / `session_runner` — no parallel tree
- [ ] Modes cannot overwrite each other’s watchlist / open positions / pids
- [ ] Sequential ASX-day + US-night documented; distinct IBKR client ids reserved
- [ ] Default remains `us` when unset
- [ ] ASX paper 1-share smoke under paper guard
