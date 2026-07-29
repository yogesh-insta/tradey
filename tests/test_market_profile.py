"""Unit tests for MARKET=us|asx profile + ASX session clock (no network)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.market_profile import (
    DEFAULT_EXEC_CLIENT_ID_ASX,
    DEFAULT_EXEC_CLIENT_ID_US,
    activate_market,
    normalize_market,
    resolve_market,
    set_market_profile,
)
from services.risk_manager.policy import classify_session
from services.signal_generator.rules import TimeFilter, load_rules

ET = ZoneInfo("America/New_York")
SYD = ZoneInfo("Australia/Sydney")
TF = TimeFilter(
    earliest_entry_et="10:05",
    latest_entry_et="15:30",
    force_close_et="15:51",
)


@pytest.fixture(autouse=True)
def _clear_profile(monkeypatch):
    monkeypatch.delenv("MARKET", raising=False)
    monkeypatch.delenv("MANAGED_POSITIONS_PATH", raising=False)
    monkeypatch.delenv("BOT_STATUS_PATH", raising=False)
    monkeypatch.delenv("BOT_RUNNER_PID_PATH", raising=False)
    monkeypatch.delenv("CLOSED_TRADES_PATH", raising=False)
    set_market_profile(None)
    yield
    set_market_profile(None)
    monkeypatch.delenv("MARKET", raising=False)


def test_normalize_default_us(monkeypatch):
    monkeypatch.delenv("MARKET", raising=False)
    assert normalize_market(None) == "us"


def test_normalize_rejects_bad():
    with pytest.raises(ValueError):
        normalize_market("nyc")


def test_resolve_us_paths_and_client_id():
    p = resolve_market("us")
    assert p.market == "us"
    assert p.timezone == "America/New_York"
    assert p.currency == "USD"
    assert p.yahoo_suffix == ""
    assert p.exec_client_id == DEFAULT_EXEC_CLIENT_ID_US
    assert p.data_dir.name == "us"
    assert p.watchlist_path.name == "watchlist.txt"
    assert "data/us" in str(p.watchlist_path).replace("\\", "/")
    assert p.to_yahoo("AAPL") == "AAPL"
    assert p.to_yahoo("BRK B") == "BRK-B"


def test_resolve_asx_yahoo_and_client_id():
    p = resolve_market("asx")
    assert p.market == "asx"
    assert p.timezone == "Australia/Sydney"
    assert p.currency == "AUD"
    assert p.yahoo_suffix == ".AX"
    assert p.ibkr_primary_exchange == "ASX"
    assert p.exec_client_id == DEFAULT_EXEC_CLIENT_ID_ASX
    assert p.to_yahoo("BHP") == "BHP.AX"
    assert p.to_yahoo("BHP.AX") == "BHP.AX"
    assert "data/asx" in str(p.open_positions_path).replace("\\", "/")
    assert p.rules_path.name == "rules.asx.json"


def test_asx_client_id_env_override(monkeypatch):
    monkeypatch.setenv("IBKR_EXEC_CLIENT_ID_ASX", "29")
    assert resolve_market("asx").exec_client_id == 29


def test_us_and_asx_paths_isolated():
    us = resolve_market("us")
    asx = resolve_market("asx")
    assert us.watchlist_path != asx.watchlist_path
    assert us.open_positions_path != asx.open_positions_path
    assert us.status_path != asx.status_path


def test_asx_rules_load():
    p = activate_market("asx")
    rules = load_rules(p.rules_path)
    assert rules.strategy_name.startswith("ASX")
    assert rules.intraday_filters.I1_above_premarket_high is False
    assert rules.daily_filters.D3_min_gap_pct_from_prior_close == 2.5


def test_asx_session_phases_sydney():
    # Tuesday 2026-07-14 in Sydney
    assert classify_session(
        datetime(2026, 7, 14, 9, 45, tzinfo=SYD),
        time_filter=TF,
        tz=SYD,
    ).code == "too_early"
    assert classify_session(
        datetime(2026, 7, 14, 10, 2, tzinfo=SYD),
        time_filter=TF,
        tz=SYD,
    ).code == "manage_only"
    ok = classify_session(
        datetime(2026, 7, 14, 11, 0, tzinfo=SYD),
        time_filter=TF,
        tz=SYD,
    )
    assert ok.code == "ok"
    assert ok.allow_new_entries
    assert classify_session(
        datetime(2026, 7, 14, 15, 40, tzinfo=SYD),
        time_filter=TF,
        tz=SYD,
    ).code == "manage_only"
    assert classify_session(
        datetime(2026, 7, 14, 15, 55, tzinfo=SYD),
        time_filter=TF,
        tz=SYD,
    ).code == "force_close"
    assert classify_session(
        datetime(2026, 7, 14, 16, 5, tzinfo=SYD),
        time_filter=TF,
        tz=SYD,
    ).code == "closed"


def test_us_session_still_et():
    assert classify_session(
        datetime(2026, 7, 14, 11, 0, tzinfo=ET),
        time_filter=TF,
        tz=ET,
    ).code == "ok"


def test_asx_universe_nonempty():
    p = resolve_market("asx")
    u = p.universe()
    assert "BHP" in u
    assert "CBA" in u
    assert len(u) >= 100
