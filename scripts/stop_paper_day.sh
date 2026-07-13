#!/usr/bin/env bash
# Stop local paper-day processes started by start_paper_day.sh (SIGTERM via PID files).
# Does NOT flatten IBKR positions — use scripts/flatten_all.py separately if needed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="${ROOT}/data/run"

_stop_one() {
  local name="$1"
  local pid_file="${RUN_DIR}/${name}.pid"
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

mkdir -p "$RUN_DIR"
_stop_one runner
_stop_one dashboard
_stop_one caffeinate

echo ""
echo "Bot processes stopped (runner + dashboard + caffeinate)."
echo "status.json left in place so the dashboard can show Stale/Stopped until next start."
echo "Flatten is separate if you still have open paper positions:"
echo "  python scripts/flatten_all.py --dry-run"
echo "  python scripts/flatten_all.py"

# Telegram: never fail start/stop if notify is broken
PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
cd "$ROOT"
"$PYTHON" - <<'PY' || true
from packages.notify import notify
notify("paper day stopped", "runner + dashboard + caffeinate SIGTERM'd")
PY
