#!/usr/bin/env bash
# Start local paper-day processes: dashboard + session_runner (+ caffeinate).
# Default: dry-run (no IBKR orders). Opt into paper with TRADE_PAPER=1 or --paper.
#
# Usage:
#   ./scripts/start_paper_day.sh              # dry-run (default)
#   ./scripts/start_paper_day.sh --paper       # paper orders (--no-dry-run)
#   TRADE_PAPER=1 ./scripts/start_paper_day.sh
#
# Manual before overnight soak:
#   - IB Gateway paper (port 4002) running + logged in (human)
#   - Prefer dry-run first; open http://127.0.0.1:8080 for Bot status
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RUN_DIR="${ROOT}/data/run"
LOG_DIR="${ROOT}/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

DRY_RUN=1
EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --paper)
      DRY_RUN=0
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    *)
      EXTRA+=("$arg")
      ;;
  esac
done
if [[ "${TRADE_PAPER:-0}" == "1" ]]; then
  DRY_RUN=0
fi

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

_pid_alive() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_start_if_needed() {
  local name="$1"
  local pid_file="$2"
  shift 2
  if [[ -f "$pid_file" ]]; then
    local old
    old="$(tr -d '[:space:]' <"$pid_file" || true)"
    if _pid_alive "$old"; then
      echo "${name} already running (pid ${old})"
      return 0
    fi
    rm -f "$pid_file"
  fi
  nohup "$@" >>"${LOG_DIR}/${name}.log" 2>&1 &
  local pid=$!
  echo "$pid" >"$pid_file"
  echo "started ${name} pid=${pid} log=${LOG_DIR}/${name}.log"
}

# Keep Mac awake while bot processes run (killed by stop_paper_day.sh)
_start_if_needed caffeinate "${RUN_DIR}/caffeinate.pid" \
  /usr/bin/caffeinate -dimsu

# Adopt orphan listener on 8080 (e.g. manual uvicorn without a pid file) so we
# don't spawn a second bind that fails overnight.
if [[ ! -f "${RUN_DIR}/dashboard.pid" ]]; then
  orphan="$(lsof -nP -tiTCP:8080 -sTCP:LISTEN 2>/dev/null | head -1 || true)"
  if [[ -n "${orphan}" ]] && _pid_alive "${orphan}"; then
    echo "${orphan}" >"${RUN_DIR}/dashboard.pid"
    echo "dashboard already listening (pid ${orphan}) — adopted"
  fi
fi

# Dashboard on 8080
_start_if_needed dashboard "${RUN_DIR}/dashboard.pid" \
  "$PYTHON" -m uvicorn services.dashboard.app:app --host 127.0.0.1 --port 8080

# Session runner
RUNNER_ARGS=(scripts/session_runner.py --interval 60)
if [[ "$DRY_RUN" -eq 1 ]]; then
  RUNNER_ARGS+=(--dry-run)
  MODE="dry-run"
else
  RUNNER_ARGS+=(--no-dry-run)
  MODE="paper (--no-dry-run)"
fi
if [[ ${#EXTRA[@]} -gt 0 ]]; then
  RUNNER_ARGS+=("${EXTRA[@]}")
fi

_start_if_needed runner "${RUN_DIR}/runner.pid" \
  "$PYTHON" "${RUNNER_ARGS[@]}"

# Seed status file so dashboard shows something before first tick
export TRADEY_START_DRY_RUN="$DRY_RUN"
"$PYTHON" - <<'PY'
from packages.observability.bot_status import write_status, watchlist_symbol_count
from services.position_manager.state import PositionStore
import os
dry = os.environ.get("TRADEY_START_DRY_RUN", "1") == "1"
try:
    open_count = len(PositionStore().load())
except Exception:
    open_count = 0
write_status(
    phase="starting",
    dry_run=dry,
    open_count=open_count,
    watchlist_count=watchlist_symbol_count(),
    last_error=None,
)
print("wrote data/run/status.json")
PY

echo ""
echo "Paper day started (${MODE})."
echo "  Dashboard: http://127.0.0.1:8080  (Bot status panel + GET /api/bot_status)"
echo "  PIDs:      ${RUN_DIR}/*.pid"
echo "  Logs:      ${LOG_DIR}/{dashboard,runner,caffeinate}.log"
echo "  Stop:      ./scripts/stop_paper_day.sh"
echo ""
echo "Note: IB Gateway login is still manual. Flatten is separate: python scripts/flatten_all.py"
if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "WARNING: paper orders enabled — Gateway paper (4002) must be up and logged in."
fi

# Telegram: never fail start/stop if notify is broken
MODE_LABEL="$MODE" "$PYTHON" - <<'PY' || true
import os
from packages.notify import notify
mode = os.environ.get("MODE_LABEL", "dry-run")
notify(
    "paper day started",
    f"mode={mode}\nDashboard: http://127.0.0.1:8080",
)
PY
