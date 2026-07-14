"""Shared test fixtures — keep MARKET=us default isolated between tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_market_profile(monkeypatch):
    monkeypatch.delenv("MARKET", raising=False)
    monkeypatch.delenv("MANAGED_POSITIONS_PATH", raising=False)
    monkeypatch.delenv("BOT_STATUS_PATH", raising=False)
    monkeypatch.delenv("BOT_RUNNER_PID_PATH", raising=False)
    monkeypatch.delenv("CLOSED_TRADES_PATH", raising=False)
    try:
        from services.market_profile import set_market_profile

        set_market_profile(None)
    except Exception:
        pass
    yield
    try:
        from services.market_profile import set_market_profile

        set_market_profile(None)
    except Exception:
        pass
