#!/usr/bin/env bash
# Start local paper-day processes: dashboard + session_runner (+ caffeinate).
# Default: dry-run (no IBKR orders). Opt into paper with TRADE_PAPER=1 or --paper.
# Dual-mode: --market us|asx (default us). Paths/pids under data/{market}/run/.
#
# Usage:
#   ./scripts/start_paper_day.sh                    # US dry-run (default)
#   ./scripts/start_paper_day.sh --market asx       # ASX dry-run
#   MARKET=asx ./scripts/start_paper_day.sh
#   ./scripts/start_paper_day.sh --market asx --paper
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MARKET="${MARKET:-us}"
DRY_RUN=1
EXTRA=()
prev=""
for arg in "$@"; do
  if [[ "$prev" == "--market" ]]; then
    MARKET="$arg"
    prev=""
    continue
  fi
  case "$arg" in
    --market)
      prev="--market"
      ;;
    --market=*)
      MARKET="${arg#--market=}"
      ;;
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
if [[ -n "$prev" ]]; then
  echo "error: --market requires us|asx" >&2
  exit 1
fi
MARKET="$(echo "$MARKET" | tr '[:upper:]' '[:lower:]')"
if [[ "$MARKET" != "us" && "$MARKET" != "asx" ]]; then
  echo "error: MARKET must be us|asx (got $MARKET)" >&2
  exit 1
fi
if [[ "${TRADE_PAPER:-0}" == "1" ]]; then
  DRY_RUN=0
fi

export MARKET
# Per-mode paths (Python activate_market also sets these; export early for dashboard)
if [[ "$MARKET" == "asx" ]]; then
  export BOT_STATUS_PATH="${ROOT}/data/asx/run/status.json"
  export BOT_RUNNER_PID_PATH="${ROOT}/data/asx/run/runner.pid"
  export MANAGED_POSITIONS_PATH="${ROOT}/data/asx/open_positions.json"
  export CLOSED_TRADES_PATH="${ROOT}/data/asx/closed_trades.jsonl"
  export IBKR_EXEC_CLIENT_ID="${IBKR_EXEC_CLIENT_ID_ASX:-19}"
else
  export BOT_STATUS_PATH="${ROOT}/data/us/run/status.json"
  export BOT_RUNNER_PID_PATH="${ROOT}/data/us/run/runner.pid"
  export MANAGED_POSITIONS_PATH="${ROOT}/data/us/open_positions.json"
  export CLOSED_TRADES_PATH="${ROOT}/data/us/closed_trades.jsonl"
  export IBKR_EXEC_CLIENT_ID="${IBKR_EXEC_CLIENT_ID_US:-${IBKR_EXEC_CLIENT_ID:-18}}"
fi

RUN_DIR="${ROOT}/data/${MARKET}/run"
SHARED_RUN="${ROOT}/data/run"
LOG_DIR="${ROOT}/logs"
mkdir -p "$RUN_DIR" "$SHARED_RUN" "$LOG_DIR" "${ROOT}/data/${MARKET}"

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
  nohup env PYTHONUNBUFFERED=1 "$@" >>"${LOG_DIR}/${name}.${MARKET}.log" 2>&1 &
  local pid=$!
  echo "$pid" >"$pid_file"
  echo "started ${name} pid=${pid} log=${LOG_DIR}/${name}.${MARKET}.log"
}

# Keep Mac awake while bot processes run
_start_if_needed caffeinate "${RUN_DIR}/caffeinate.pid" \
  /usr/bin/caffeinate -dimsu

# Dashboard: share port 8080; pid in shared data/run so stop can find it
if [[ ! -f "${SHARED_RUN}/dashboard.pid" ]]; then
  orphan="$(lsof -nP -tiTCP:8080 -sTCP:LISTEN 2>/dev/null | head -1 || true)"
  if [[ -n "${orphan}" ]] && _pid_alive "${orphan}"; then
    echo "${orphan}" >"${SHARED_RUN}/dashboard.pid"
    echo "dashboard already listening (pid ${orphan}) — adopted"
  fi
fi

_start_if_needed dashboard "${SHARED_RUN}/dashboard.pid" \
  env MARKET="$MARKET" BOT_STATUS_PATH="$BOT_STATUS_PATH" BOT_RUNNER_PID_PATH="$BOT_RUNNER_PID_PATH" \
  MANAGED_POSITIONS_PATH="$MANAGED_POSITIONS_PATH" CLOSED_TRADES_PATH="$CLOSED_TRADES_PATH" \
  "$PYTHON" -m uvicorn services.dashboard.app:app --host 127.0.0.1 --port 8080

RUNNER_ARGS=(scripts/session_runner.py --market "$MARKET" --interval 60)
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

export TRADEY_START_DRY_RUN="$DRY_RUN"
export TRADEY_START_MARKET="$MARKET"
"$PYTHON" - <<'PY'
from packages.observability.bot_status import write_status, watchlist_symbol_count
from services.market_profile import activate_market
from services.position_manager.state import PositionStore
import os
market = os.environ.get("TRADEY_START_MARKET", "us")
profile = activate_market(market)
dry = os.environ.get("TRADEY_START_DRY_RUN", "1") == "1"
try:
    open_count = len(PositionStore(profile.open_positions_path).load())
except Exception:
    open_count = 0
write_status(
    phase="starting",
    dry_run=dry,
    open_count=open_count,
    watchlist_count=watchlist_symbol_count(profile.watchlist_path),
    last_error=None,
    path=profile.status_path,
    market=profile.market,
)
print(f"wrote {profile.status_path}")
PY

echo ""
echo "Paper day started (${MODE}) market=${MARKET}."
echo "  Dashboard: http://127.0.0.1:8080  (Bot status + GET /api/bot_status)"
echo "  Status:    ${BOT_STATUS_PATH}"
echo "  PIDs:      ${RUN_DIR}/*.pid (+ shared dashboard ${SHARED_RUN}/dashboard.pid)"
echo "  Logs:      ${LOG_DIR}/*.${MARKET}.log"
echo "  Stop:      ./scripts/stop_paper_day.sh --market ${MARKET}"
echo ""
echo "Note: IB Gateway login is still manual. Flatten is separate: python scripts/flatten_all.py"
if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "WARNING: paper orders enabled — Gateway paper (4002) must be up and logged in."
fi

MODE_LABEL="$MODE" MARKET_LABEL="$MARKET" "$PYTHON" - <<'PY' || true
import os
from packages.notify import notify
mode = os.environ.get("MODE_LABEL", "dry-run")
market = os.environ.get("MARKET_LABEL", "us")
notify(
    "paper day started",
    f"market={market}\nmode={mode}\nDashboard: http://127.0.0.1:8080",
)
PY
