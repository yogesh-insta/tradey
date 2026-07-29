#!/usr/bin/env bash
# Stop local paper-day processes started by start_paper_day.sh (SIGTERM via PID files).
# Does NOT flatten IBKR positions — use scripts/flatten_all.py separately if needed.
#
#   ./scripts/stop_paper_day.sh              # stop MARKET env or us
#   ./scripts/stop_paper_day.sh --market asx
#   ./scripts/stop_paper_day.sh --all        # stop us + asx runners + shared dashboard
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

MARKET="${MARKET:-us}"
STOP_ALL=0
prev=""
for arg in "$@"; do
  if [[ "$prev" == "--market" ]]; then
    MARKET="$arg"
    prev=""
    continue
  fi
  case "$arg" in
    --market) prev="--market" ;;
    --market=*) MARKET="${arg#--market=}" ;;
    --all) STOP_ALL=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 1 ;;
  esac
done
if [[ -n "$prev" ]]; then
  echo "error: --market requires us|asx" >&2
  exit 1
fi
MARKET="$(echo "$MARKET" | tr '[:upper:]' '[:lower:]')"

_stop_one() {
  local name="$1"
  local pid_file="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "${name}: no pid file"
    return 0
  fi
  local pid
  pid="$(tr -d '[:space:]' <"$pid_file" || true)"
  if [[ -z "$pid" ]]; then
    echo "${name}: empty pid file — removing"
    rm -f "$pid_file"
    return 0
  fi
  if kill -0 "$pid" 2>/dev/null; then
    echo "stopping ${name} pid=${pid} (SIGTERM)"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 0.4
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "  ${name} still alive — SIGKILL"
      kill -KILL "$pid" 2>/dev/null || true
    fi
  else
    echo "${name}: pid ${pid} not running"
  fi
  rm -f "$pid_file"
}

_stop_market() {
  local m="$1"
  local run_dir="${ROOT}/data/${m}/run"
  mkdir -p "$run_dir"
  echo "--- stop market=${m} ---"
  _stop_one "runner(${m})" "${run_dir}/runner.pid"
  _stop_one "caffeinate(${m})" "${run_dir}/caffeinate.pid"
  # Legacy flat pid paths (pre dual-mode)
  if [[ "$m" == "us" ]]; then
    _stop_one "runner(legacy)" "${ROOT}/data/run/runner.pid"
    _stop_one "caffeinate(legacy)" "${ROOT}/data/run/caffeinate.pid"
  fi
}

if [[ "$STOP_ALL" -eq 1 ]]; then
  _stop_market us
  _stop_market asx
else
  if [[ "$MARKET" != "us" && "$MARKET" != "asx" ]]; then
    echo "error: MARKET must be us|asx (got $MARKET)" >&2
    exit 1
  fi
  _stop_market "$MARKET"
fi

# Shared dashboard (one per host)
_stop_one dashboard "${ROOT}/data/run/dashboard.pid"

echo ""
echo "Bot processes stopped."
echo "status.json left in place so the dashboard can show Stale/Stopped until next start."
echo "Flatten is separate if you still have open paper positions:"
echo "  python scripts/flatten_all.py --dry-run"

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
cd "$ROOT"
MARKET_LABEL="${MARKET}" STOP_ALL_LABEL="${STOP_ALL}" "$PYTHON" - <<'PY' || true
import os
from packages.notify import notify
market = os.environ.get("MARKET_LABEL", "us")
stop_all = os.environ.get("STOP_ALL_LABEL", "0") == "1"
label = "all markets" if stop_all else f"market={market}"
notify("paper day stopped", f"{label}: runner + dashboard + caffeinate SIGTERM'd")
PY
