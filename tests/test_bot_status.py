"""Pure tests for bot status.json parse + stale / Running|Stopped|Stale."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from packages.observability.bot_status import (
    classify_bot_state,
    is_stale,
    parse_status,
    tick_age_seconds,
    watchlist_symbol_count,
    write_status,
    load_status,
)


def test_parse_status_ok():
    raw = {
        "phase": "ok",
        "dry_run": True,
        "last_tick_utc": "2026-07-14T01:00:00+00:00",
        "open_count": 2,
        "watchlist_count": 20,
        "last_error": None,
        "pid": 1234,
    }
    got = parse_status(raw)
    assert got is not None
    assert got["phase"] == "ok"
    assert got["dry_run"] is True
    assert got["open_count"] == 2
    assert got["watchlist_count"] == 20
    assert got["pid"] == 1234


def test_parse_status_rejects_bad():
    assert parse_status(None) is None
    assert parse_status([]) is None
    assert parse_status({"phase": "ok"}) is None  # missing last_tick
    assert parse_status({"last_tick_utc": "x"}) is None  # missing phase


def test_parse_status_coerces_strings():
    got = parse_status(
        {
            "phase": " too_early ",
            "dry_run": "true",
            "last_tick_utc": "2026-07-14T01:00:00Z",
            "open_count": "3",
            "watchlist_count": "7",
        }
    )
    assert got is not None
    assert got["phase"] == "too_early"
    assert got["dry_run"] is True
    assert got["open_count"] == 3
    assert got["watchlist_count"] == 7


def test_tick_age_and_stale():
    now = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(seconds=30)).isoformat()
    old = (now - timedelta(seconds=400)).isoformat()
    assert tick_age_seconds(fresh, now=now) == 30.0
    assert is_stale({"last_tick_utc": fresh}, now=now, stale_after=180) is False
    assert is_stale({"last_tick_utc": old}, now=now, stale_after=180) is True
    assert is_stale({"last_tick_utc": "not-a-date"}, now=now) is True


def test_classify_stopped_running_stale(monkeypatch):
    now = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    assert classify_bot_state(None, runner_pid=None, now=now) == "Stopped"

    monkeypatch.setattr(
        "packages.observability.bot_status.pid_is_alive",
        lambda pid: pid == 99,
    )
    status = {
        "phase": "ok",
        "dry_run": True,
        "last_tick_utc": (now - timedelta(seconds=10)).isoformat(),
        "open_count": 0,
        "watchlist_count": 0,
        "last_error": None,
        "pid": 99,
    }
    assert classify_bot_state(status, runner_pid=99, now=now, stale_after=180) == "Running"

    stale_status = dict(status)
    stale_status["last_tick_utc"] = (now - timedelta(seconds=500)).isoformat()
    assert (
        classify_bot_state(stale_status, runner_pid=99, now=now, stale_after=180)
        == "Stale"
    )

    dead = dict(status)
    dead["pid"] = 1
    assert classify_bot_state(dead, runner_pid=1, now=now, stale_after=180) == "Stale"


def test_write_load_roundtrip(tmp_path: Path):
    path = tmp_path / "status.json"
    write_status(
        phase="manage_only",
        dry_run=True,
        open_count=1,
        watchlist_count=5,
        last_error=None,
        pid=42,
        path=path,
        last_tick_utc="2026-07-14T11:00:00+00:00",
    )
    got = load_status(path)
    assert got is not None
    assert got["phase"] == "manage_only"
    assert got["open_count"] == 1
    assert got["pid"] == 42


def test_watchlist_symbol_count(tmp_path: Path):
    path = tmp_path / "watchlist.txt"
    path.write_text("# comment\nAAPL\n\nMSFT\n# skip\nNVDA\n", encoding="utf-8")
    assert watchlist_symbol_count(path) == 3
    assert watchlist_symbol_count(tmp_path / "missing.txt") == 0
