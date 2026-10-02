# Local paper-day LaunchAgents

Schedule uses the **Mac local clock** (AEST/AEDT). Dual-mode sequential window:

| When (local) | Agent | Action |
|---|---|---|
| **09:00** weekdays | `com.tradey.paper-day-stop` | `stop_paper_day.sh --market us` |
| **09:30** weekdays | `com.tradey.asx-day-start` | `start_paper_day.sh --market asx --paper` |
| **16:30** weekdays | `com.tradey.asx-day-stop` | `stop_paper_day.sh --market asx` |
| **21:30** weekdays | `com.tradey.paper-day-start` | `start_paper_day.sh --market us --paper` |

ASX daytime → idle handoff → US overnight. One active runner at a time (US client id `18`, ASX `19`).

Start plists set `AbandonProcessGroup` so launchd does not SIGTERM the nohup’d dashboard/runner when `start_paper_day.sh` exits.

IB Gateway login is still manual (human) before enabling paper orders.

## One-time install

Templates use `__REPO_ROOT__`. The install script rewrites that to this clone and loads the agents. The installed copies live in `~/Library/LaunchAgents`, not in git.

```bash
./deploy/launchd/install.sh
launchctl list | grep tradey
```

## Unload

```bash
launchctl unload ~/Library/LaunchAgents/com.tradey.asx-day-start.plist
launchctl unload ~/Library/LaunchAgents/com.tradey.asx-day-stop.plist
launchctl unload ~/Library/LaunchAgents/com.tradey.paper-day-start.plist
launchctl unload ~/Library/LaunchAgents/com.tradey.paper-day-stop.plist
```

## Manual start/stop (preferred for first soak)

```bash
./scripts/start_paper_day.sh --market asx   # ASX dry-run
./scripts/stop_paper_day.sh --market asx
./scripts/start_paper_day.sh --market us    # US dry-run (default)
./scripts/stop_paper_day.sh --market us
# Paper orders only when Gateway paper is ready:
# TRADE_PAPER=1 ./scripts/start_paper_day.sh --market us
# ./scripts/start_paper_day.sh --market asx --paper
```

Dashboard Bot status: http://127.0.0.1:8080 — Running/Stopped/Stale, phase, dry-run, last tick, open count.
