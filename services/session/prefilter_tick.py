"""Auto-refresh watchlist via Signal prefilter (Yahoo only; never places orders)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from packages.contracts.events import TradeEvent
from packages.ledger import Ledger
from packages.notify import notify
from services.signal_generator.prefilter import (
    DEFAULT_WATCHLIST_PATH,
    PrefilterResult,
    run_prefilter,
)

ET = ZoneInfo("America/New_York")

# Session codes where a fresh watchlist matters (before entries / morning gate).
PREFILTER_SESSION_CODES = frozenset({"too_early", "manage_only", "ok"})

DEFAULT_STALE_HOURS = 12.0


@dataclass(frozen=True)
class WatchlistFreshness:
    needs_refresh: bool
    reason: str  # missing | not_today_et | stale_age | fresh | empty


@dataclass
class PrefilterTickResult:
    ran: bool
    skipped: bool = False
    skip_reason: Optional[str] = None
    freshness: Optional[WatchlistFreshness] = None
    prefilter: Optional[PrefilterResult] = None

    def to_summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ran": self.ran,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }
        if self.freshness is not None:
            out["freshness"] = {
                "needs_refresh": self.freshness.needs_refresh,
                "reason": self.freshness.reason,
            }
        if self.prefilter is not None:
            out["survivors_count"] = self.prefilter.survivors_count
            out["success"] = self.prefilter.success
            out["error"] = self.prefilter.error
            out["elapsed_seconds"] = round(self.prefilter.elapsed_seconds, 2)
        return out


def stale_hours_from_env(default: float = DEFAULT_STALE_HOURS) -> float:
    raw = os.getenv("WATCHLIST_STALE_HOURS", "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def needs_watchlist_refresh(
    path: Path,
    *,
    now: Optional[datetime] = None,
    max_age_hours: float = DEFAULT_STALE_HOURS,
) -> WatchlistFreshness:
    """Pure freshness check: missing, empty, not today (ET), or older than max_age_hours.

    An empty watchlist written today (ET) within max_age is treated as fresh so a
    zero-survivor morning screen does not re-hit Yahoo every tick overnight.
    """
    now_et = (now or datetime.now(ET)).astimezone(ET)
    if not path.is_file():
        return WatchlistFreshness(True, "missing")

    text = path.read_text(encoding="utf-8")
    has_symbol = any(
        line.strip() and not line.strip().startswith("#") for line in text.splitlines()
    )

    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=ET)
    age_hours = (now_et - mtime).total_seconds() / 3600.0

    if not has_symbol:
        # Overnight soak: 0 gappers still count as a completed screen for today.
        if mtime.date() == now_et.date() and age_hours <= max_age_hours:
            return WatchlistFreshness(False, "empty_today")
        return WatchlistFreshness(True, "empty")

    if mtime.date() < now_et.date():
        return WatchlistFreshness(True, "not_today_et")

    if age_hours > max_age_hours:
        return WatchlistFreshness(True, f"stale_age_hours={age_hours:.1f}")

    return WatchlistFreshness(False, "fresh")


def run_prefilter_tick(
    *,
    session_code: str,
    skip: bool = False,
    watchlist_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    max_age_hours: Optional[float] = None,
    ledger: Optional[Ledger] = None,
    notify_enabled: bool = True,
    run_fn=None,
) -> PrefilterTickResult:
    """Refresh watchlist when missing/stale before entry window. Never places orders.

    `run_fn` is injectable for tests (defaults to `run_prefilter`).
    Session dry-run still writes the watchlist (Yahoo screening only).
    """
    if skip:
        return PrefilterTickResult(ran=False, skipped=True, skip_reason="skip_prefilter")

    if session_code not in PREFILTER_SESSION_CODES:
        return PrefilterTickResult(
            ran=False,
            skipped=True,
            skip_reason=f"session={session_code}",
        )

    path = Path(watchlist_path) if watchlist_path else DEFAULT_WATCHLIST_PATH
    hours = DEFAULT_STALE_HOURS if max_age_hours is None else max_age_hours
    freshness = needs_watchlist_refresh(path, now=now, max_age_hours=hours)
    if not freshness.needs_refresh:
        return PrefilterTickResult(ran=False, skipped=True, skip_reason="fresh", freshness=freshness)

    fetch = run_fn or run_prefilter
    # Always persist watchlist here: runner dry-run means no IBKR orders, not skip Yahoo write.
    result: PrefilterResult = fetch(watchlist_path=path, dry_run=False)

    if ledger is not None:
        ledger.append(
            TradeEvent(
                event_type="info",
                message="prefilter",
                metadata={
                    "channel": "prefilter",
                    "session_code": session_code,
                    "reason": freshness.reason,
                    "survivors_count": result.survivors_count,
                    "success": result.success,
                    "error": result.error,
                    "elapsed_seconds": round(result.elapsed_seconds, 2),
                    "watchlist_path": str(path),
                },
            )
        )

    if notify_enabled:
        hhmm = (now or datetime.now(ET)).astimezone(ET).strftime("%H:%M")
        if result.success:
            notify(
                f"Prefilter {hhmm} ET",
                f"prefilter ran: {result.survivors_count} survivors "
                f"({freshness.reason}) in {result.elapsed_seconds:.1f}s",
                "default",
            )
        else:
            notify(
                f"Prefilter FAILED {hhmm} ET",
                result.error or "unknown error",
                "high",
            )

    return PrefilterTickResult(
        ran=True,
        skipped=False,
        freshness=freshness,
        prefilter=result,
    )
