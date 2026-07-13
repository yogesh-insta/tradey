"""Bot run status heartbeat (status.json) for dashboard + start/stop scripts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATUS_PATH = ROOT / "data" / "run" / "status.json"
DEFAULT_RUNNER_PID_PATH = ROOT / "data" / "run" / "runner.pid"
DEFAULT_STALE_AFTER_SECONDS = 180  # ~3× default 60s tick


def default_status_path() -> Path:
    raw = os.getenv("BOT_STATUS_PATH", "").strip()
    if not raw:
        return DEFAULT_STATUS_PATH
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def default_runner_pid_path() -> Path:
    raw = os.getenv("BOT_RUNNER_PID_PATH", "").strip()
    if not raw:
        return DEFAULT_RUNNER_PID_PATH
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def stale_after_seconds() -> int:
    raw = os.getenv("BOT_STATUS_STALE_SECONDS", "").strip()
    if not raw:
        return DEFAULT_STALE_AFTER_SECONDS
    try:
        return max(30, int(raw))
    except ValueError:
        return DEFAULT_STALE_AFTER_SECONDS


def parse_status(raw: Any) -> Optional[dict[str, Any]]:
    """Validate / normalize a status.json payload. Returns None if unusable."""
    if not isinstance(raw, dict):
        return None
    phase = raw.get("phase")
    last_tick = raw.get("last_tick_utc")
    if not isinstance(phase, str) or not phase.strip():
        return None
    if not isinstance(last_tick, str) or not last_tick.strip():
        return None
    dry_run = raw.get("dry_run", True)
    if not isinstance(dry_run, bool):
        dry_run = str(dry_run).lower() in {"1", "true", "yes"}
    open_count = raw.get("open_count", 0)
    watchlist_count = raw.get("watchlist_count", 0)
    try:
        open_count = int(open_count)
    except (TypeError, ValueError):
        open_count = 0
    try:
        watchlist_count = int(watchlist_count)
    except (TypeError, ValueError):
        watchlist_count = 0
    last_error = raw.get("last_error")
    if last_error is not None and not isinstance(last_error, str):
        last_error = str(last_error)
    pid = raw.get("pid")
    try:
        pid = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid = None
    return {
        "phase": phase.strip(),
        "dry_run": dry_run,
        "last_tick_utc": last_tick.strip(),
        "open_count": max(0, open_count),
        "watchlist_count": max(0, watchlist_count),
        "last_error": last_error,
        "pid": pid,
    }


def load_status(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    status_path = Path(path) if path else default_status_path()
    if not status_path.is_file():
        return None
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return None
    return parse_status(raw)


def write_status(
    *,
    phase: str,
    dry_run: bool,
    open_count: int = 0,
    watchlist_count: int = 0,
    last_error: Optional[str] = None,
    pid: Optional[int] = None,
    path: Optional[Path] = None,
    last_tick_utc: Optional[str] = None,
) -> Path:
    status_path = Path(path) if path else default_status_path()
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": phase,
        "dry_run": bool(dry_run),
        "last_tick_utc": last_tick_utc
        or datetime.now(timezone.utc).isoformat(),
        "open_count": int(open_count),
        "watchlist_count": int(watchlist_count),
        "last_error": last_error,
        "pid": pid if pid is not None else os.getpid(),
    }
    status_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return status_path


def tick_age_seconds(
    last_tick_utc: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Seconds since last_tick_utc, or None if unparseable."""
    try:
        ts = datetime.fromisoformat(last_tick_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return max(0.0, (now_utc - ts).total_seconds())


def is_stale(
    status: dict[str, Any],
    *,
    now: Optional[datetime] = None,
    stale_after: Optional[int] = None,
) -> bool:
    """True when last_tick_utc is older than stale_after seconds."""
    age = tick_age_seconds(str(status.get("last_tick_utc") or ""), now=now)
    if age is None:
        return True
    threshold = stale_after if stale_after is not None else stale_after_seconds()
    return age > float(threshold)


def pid_is_alive(pid: Optional[int]) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_pid_file(path: Optional[Path] = None) -> Optional[int]:
    pid_path = Path(path) if path else default_runner_pid_path()
    if not pid_path.is_file():
        return None
    try:
        raw = pid_path.read_text(encoding="utf-8").strip().splitlines()[0]
        return int(raw)
    except (OSError, ValueError, IndexError):
        return None


def classify_bot_state(
    status: Optional[dict[str, Any]],
    *,
    runner_pid: Optional[int] = None,
    now: Optional[datetime] = None,
    stale_after: Optional[int] = None,
) -> str:
    """
    Return Running | Stopped | Stale.

    - Stopped: no usable status and no live runner PID
    - Running: live runner PID (or status pid alive) and heartbeat fresh
    - Stale: status exists but heartbeat old, or PID dead while status remains
    """
    alive = pid_is_alive(runner_pid)
    if status is None:
        return "Running" if alive else "Stopped"

    status_pid = status.get("pid")
    if isinstance(status_pid, int) and pid_is_alive(status_pid):
        alive = True
    elif runner_pid is None and isinstance(status_pid, int):
        alive = pid_is_alive(status_pid)

    if is_stale(status, now=now, stale_after=stale_after):
        return "Stale" if (alive or status is not None) else "Stopped"
    if alive:
        return "Running"
    # Fresh status but process gone → treat as stale (died mid-tick window)
    return "Stale"


def watchlist_symbol_count(watchlist_path: Optional[Path] = None) -> int:
    path = watchlist_path or (ROOT / "watchlist.txt")
    if not path.is_file():
        return 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    return sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))


def build_bot_status_report(
    *,
    status_path: Optional[Path] = None,
    runner_pid_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    stale_after: Optional[int] = None,
) -> dict[str, Any]:
    """Dashboard / API payload including computed state and tick age."""
    status = load_status(status_path)
    runner_pid = read_pid_file(runner_pid_path)
    state = classify_bot_state(
        status,
        runner_pid=runner_pid,
        now=now,
        stale_after=stale_after,
    )
    age: Optional[float] = None
    if status:
        age = tick_age_seconds(str(status["last_tick_utc"]), now=now)
    return {
        "state": state,
        "phase": (status or {}).get("phase"),
        "dry_run": (status or {}).get("dry_run"),
        "last_tick_utc": (status or {}).get("last_tick_utc"),
        "last_tick_age_seconds": age,
        "open_count": (status or {}).get("open_count"),
        "watchlist_count": (status or {}).get("watchlist_count"),
        "last_error": (status or {}).get("last_error"),
        "runner_pid": runner_pid,
        "status_pid": (status or {}).get("pid"),
        "stale_after_seconds": stale_after
        if stale_after is not None
        else stale_after_seconds(),
        "gateway_note": "IB Gateway login is manual — start Gateway and authenticate before --paper / TRADE_PAPER=1.",
        "status_path": str(Path(status_path) if status_path else default_status_path()),
    }
