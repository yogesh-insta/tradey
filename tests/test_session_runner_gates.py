"""Session runner branching (early-exit / force_close) — no network."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from services.risk_manager.policy import classify_session
from services.signal_generator.rules import TimeFilter

ET = ZoneInfo("America/New_York")
TF = TimeFilter()


def test_weekend_early_exit_code():
    sat = datetime(2026, 7, 11, 12, 0, tzinfo=ET)  # Saturday
    assert classify_session(sat, time_filter=TF, tz=ET).code == "weekend"


def test_too_early_and_closed():
    early = datetime(2026, 7, 14, 9, 30, tzinfo=ET)
    assert classify_session(early, time_filter=TF, tz=ET).code == "too_early"
    late = datetime(2026, 7, 14, 16, 5, tzinfo=ET)
    assert classify_session(late, time_filter=TF, tz=ET).code == "closed"


def test_ok_manage_force_windows():
    assert classify_session(datetime(2026, 7, 14, 10, 2, tzinfo=ET), time_filter=TF, tz=ET).code == "manage_only"
    assert classify_session(datetime(2026, 7, 14, 11, 0, tzinfo=ET), time_filter=TF, tz=ET).code == "ok"
    assert classify_session(datetime(2026, 7, 14, 15, 40, tzinfo=ET), time_filter=TF, tz=ET).code == "manage_only"
    assert classify_session(datetime(2026, 7, 14, 15, 55, tzinfo=ET), time_filter=TF, tz=ET).code == "force_close"
