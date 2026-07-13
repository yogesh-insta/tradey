"""Unit tests for watchlist freshness + prefilter tick (no network)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.session.prefilter_tick import (
    DEFAULT_STALE_HOURS,
    needs_watchlist_refresh,
    run_prefilter_tick,
    stale_hours_from_env,
)
from services.signal_generator.prefilter import PrefilterResult

ET = ZoneInfo("America/New_York")


def _write_watchlist(path: Path, *, symbol: str = "AAPL") -> None:
    path.write_text(
        f"# test watchlist\n{symbol}  # gap +5.00%\n",
        encoding="utf-8",
    )


def test_needs_refresh_missing(tmp_path: Path):
    path = tmp_path / "watchlist.txt"
    now = datetime(2026, 7, 14, 9, 0, tzinfo=ET)
    fres = needs_watchlist_refresh(path, now=now)
    assert fres.needs_refresh is True
    assert fres.reason == "missing"


def test_needs_refresh_empty(tmp_path: Path):
    path = tmp_path / "watchlist.txt"
    path.write_text("# only comments\n\n", encoding="utf-8")
    now = datetime(2026, 7, 14, 9, 0, tzinfo=ET)
    # Backdate to previous ET day → still needs refresh
    yesterday = datetime(2026, 7, 13, 15, 0, tzinfo=ET).timestamp()
    os.utime(path, (yesterday, yesterday))
    fres = needs_watchlist_refresh(path, now=now)
    assert fres.needs_refresh is True
    assert fres.reason == "empty"


def test_needs_refresh_empty_today_is_fresh(tmp_path: Path):
    """Zero survivors from today's screen must not re-run every tick."""
    path = tmp_path / "watchlist.txt"
    path.write_text("# only comments — no gappers today\n", encoding="utf-8")
    now = datetime(2026, 7, 14, 9, 0, tzinfo=ET)
    os.utime(path, (now.timestamp(), now.timestamp()))
    fres = needs_watchlist_refresh(path, now=now, max_age_hours=12.0)
    assert fres.needs_refresh is False
    assert fres.reason == "empty_today"


def test_needs_refresh_not_today_et(tmp_path: Path):
    path = tmp_path / "watchlist.txt"
    _write_watchlist(path)
    # Backdate mtime to previous ET calendar day
    yesterday = datetime(2026, 7, 13, 15, 0, tzinfo=ET).timestamp()
    os.utime(path, (yesterday, yesterday))
    now = datetime(2026, 7, 14, 9, 30, tzinfo=ET)
    fres = needs_watchlist_refresh(path, now=now, max_age_hours=24.0)
    assert fres.needs_refresh is True
    assert fres.reason == "not_today_et"


def test_needs_refresh_stale_age(tmp_path: Path):
    path = tmp_path / "watchlist.txt"
    _write_watchlist(path)
    now = datetime(2026, 7, 14, 14, 0, tzinfo=ET)
    # Same ET day but older than max_age
    old = (now - timedelta(hours=5)).timestamp()
    os.utime(path, (old, old))
    fres = needs_watchlist_refresh(path, now=now, max_age_hours=4.0)
    assert fres.needs_refresh is True
    assert fres.reason.startswith("stale_age_hours=")


def test_needs_refresh_fresh(tmp_path: Path):
    path = tmp_path / "watchlist.txt"
    _write_watchlist(path)
    now = datetime(2026, 7, 14, 10, 0, tzinfo=ET)
    recent = (now - timedelta(hours=1)).timestamp()
    os.utime(path, (recent, recent))
    fres = needs_watchlist_refresh(path, now=now, max_age_hours=DEFAULT_STALE_HOURS)
    assert fres.needs_refresh is False
    assert fres.reason == "fresh"


def test_stale_hours_from_env(monkeypatch):
    monkeypatch.delenv("WATCHLIST_STALE_HOURS", raising=False)
    assert stale_hours_from_env() == DEFAULT_STALE_HOURS
    monkeypatch.setenv("WATCHLIST_STALE_HOURS", "6")
    assert stale_hours_from_env() == 6.0
    monkeypatch.setenv("WATCHLIST_STALE_HOURS", "bogus")
    assert stale_hours_from_env() == DEFAULT_STALE_HOURS


def test_run_prefilter_tick_skips_when_fresh(tmp_path: Path):
    path = tmp_path / "watchlist.txt"
    _write_watchlist(path)
    now = datetime(2026, 7, 14, 10, 0, tzinfo=ET)
    os.utime(path, (now.timestamp(), now.timestamp()))

    calls: list[dict] = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return PrefilterResult(success=True, total_screened=1, survivors_count=1)

    out = run_prefilter_tick(
        session_code="ok",
        watchlist_path=path,
        now=now,
        max_age_hours=12.0,
        notify_enabled=False,
        run_fn=fake_run,
    )
    assert out.ran is False
    assert out.skip_reason == "fresh"
    assert calls == []


def test_run_prefilter_tick_runs_when_missing(tmp_path: Path):
    path = tmp_path / "watchlist.txt"
    now = datetime(2026, 7, 14, 9, 45, tzinfo=ET)

    def fake_run(**kwargs):
        assert kwargs.get("dry_run") is False
        assert kwargs.get("watchlist_path") == path
        return PrefilterResult(
            success=True,
            total_screened=503,
            survivors_count=7,
            elapsed_seconds=1.2,
        )

    out = run_prefilter_tick(
        session_code="too_early",
        watchlist_path=path,
        now=now,
        notify_enabled=False,
        run_fn=fake_run,
    )
    assert out.ran is True
    assert out.freshness is not None and out.freshness.reason == "missing"
    assert out.prefilter is not None
    assert out.prefilter.survivors_count == 7
    assert out.to_summary()["survivors_count"] == 7


def test_run_prefilter_tick_skip_flag_and_session():
    skipped = run_prefilter_tick(session_code="ok", skip=True, notify_enabled=False)
    assert skipped.skipped and skipped.skip_reason == "skip_prefilter"

    weekend = run_prefilter_tick(session_code="weekend", notify_enabled=False)
    assert weekend.skipped and "session=" in (weekend.skip_reason or "")
