# Local paper-day LaunchAgents

Schedule uses the **Mac local clock** (AEST/AEDT). Overnight window:

| When (local) | Action |
|---|---|
| **21:30** weekdays | `start_paper_day.sh` — dashboard + session_runner (+ caffeinate). Default **dry-run**. Runner idles in `too_early` / prefilter until US RTH. |
| **09:00** weekdays | `stop_paper_day.sh` — SIGTERM runner + dashboard + caffeinate |

IB Gateway login is still manual (human) before enabling paper orders.

## One-time install

Templates use `__REPO_ROOT__`. The install script rewrites that to this clone and loads the agents. The installed copies live in `~/Library/LaunchAgents`, not in git.

```bash
./deploy/launchd/install.sh
```

## Unload

```bash
launchctl unload ~/Library/LaunchAgents/com.tradey.paper-day-start.plist
launchctl unload ~/Library/LaunchAgents/com.tradey.paper-day-stop.plist
```

## Manual start/stop (preferred for first soak)

```bash
./scripts/start_paper_day.sh          # dry-run default
./scripts/stop_paper_day.sh
# Paper orders only when Gateway paper is ready:
# TRADE_PAPER=1 ./scripts/start_paper_day.sh
# ./scripts/start_paper_day.sh --paper
```

Dashboard Bot status: http://127.0.0.1:8080 — Running/Stopped/Stale, phase, dry-run, last tick, open count.
