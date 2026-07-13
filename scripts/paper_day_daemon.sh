#!/usr/bin/env bash
# Thin wrapper for launchd: start paper day (default dry-run) at schedule.
# Prefer loading com.tradey.paper-day-start.plist once; stop via
# com.tradey.paper-day-stop.plist (~09:00) or ./scripts/stop_paper_day.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "${ROOT}/scripts/start_paper_day.sh" "$@"
