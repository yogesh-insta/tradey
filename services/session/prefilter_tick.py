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
from services.market_profile import get_market_profile
from services.signal_generator.prefilter import (
    PrefilterResult,
    default_watchlist_path,
    run_prefilter,
)

# Session codes where a fresh watchlist matters (before entries / morning gate).
PREFILTER_SESSION_CODES = frozenset({"too_early", "manage_only", "ok"})

DEFAULT_STALE_HOURS = 12.0
# After a Yahoo download failure, wait before re-screening (avoids 429 storms).
ERROR_COOLDOWN_HOURS = 0.25  # 15 minutes


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
    tz: Optional[ZoneInfo] = None,
) -> WatchlistFreshness:
    """Pure freshness check: missing, empty, not today (market TZ), or older than max_age_hours.

    An empty watchlist written today (market TZ) within max_age is treated as fresh so a
    zero-survivor morning screen does not re-hit Yahoo every tick.
    """
    zone = tz or get_market_profile().tz
    now_local = (now or datetime.now(zone)).astimezone(zone)
    if not path.is_file():
        return WatchlistFreshness(True, "missing")

    text = path.read_text(encoding="utf-8")
    has_symbol = any(
        line.strip() and not line.strip().startswith("#") for line in text.splitlines()
    )

    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=zone)
    age_hours = (now_local - mtime).total_seconds() / 3600.0

    if not has_symbol:
        is_error = any(
            line.strip().startswith("# ERROR:") for line in text.splitlines()
        )
        # Overnight soak: 0 gappers still count as a completed screen for today.
        # Failed Yahoo downloads use a shorter cooldown so we can recover same session.
        empty_max_age = ERROR_COOLDOWN_HOURS if is_error else max_age_hours
        if mtime.date() == now_local.date() and age_hours <= empty_max_age:
            reason = "empty_error_cooldown" if is_error else "empty_today"
            return WatchlistFreshness(False, reason)
        return WatchlistFreshness(True, "empty")

    if mtime.date() < now_local.date():
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

    profile = get_market_profile()
    path = Path(watchlist_path) if watchlist_path else default_watchlist_path()
    hours = DEFAULT_STALE_HOURS if max_age_hours is None else max_age_hours
    freshness = needs_watchlist_refresh(path, now=now, max_age_hours=hours, tz=profile.tz)
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
                    "market": profile.market,
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
        hhmm = (now or datetime.now(profile.tz)).astimezone(profile.tz).strftime("%H:%M")
        tz_label = "Sydney" if profile.market == "asx" else "ET"
        if result.success:
            notify(
                f"Prefilter {hhmm} {tz_label} [{profile.market}]",
                f"prefilter ran: {result.survivors_count} survivors "
                f"({freshness.reason}) in {result.elapsed_seconds:.1f}s",
                "default",
            )
        else:
            notify(
                f"Prefilter FAILED {hhmm} {tz_label} [{profile.market}]",
                result.error or "unknown error",
                "high",
            )

    return PrefilterTickResult(
        ran=True,
        skipped=False,
        freshness=freshness,
        prefilter=result,
    )
