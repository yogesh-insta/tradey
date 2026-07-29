"""Unit tests for Risk Manager policy + AUD capital helpers (no network)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from packages.contracts.signals import NormalizedSignal
from services.risk_manager.capital import (
    CapitalConfig,
    aud_to_usd,
    sizing_portfolio_usd,
    usd_to_aud,
)
from services.risk_manager.policy import (
    classify_session,
    daily_loss_breached,
    evaluate_signal,
    size_long,
)
from services.signal_generator.rules import StrategyRules

ET = ZoneInfo("America/New_York")


def test_daily_loss_breached_at_threshold():
    # 3% of 10_000 = 300; -300 triggers
    assert daily_loss_breached(
        portfolio_value_usd=10_000,
        day_pnl_usd=-300,
        max_daily_loss_pct=3.0,
    )
    assert daily_loss_breached(
        portfolio_value_usd=10_000,
        day_pnl_usd=-301,
        max_daily_loss_pct=3.0,
    )


def test_daily_loss_not_breached_above_threshold():
    assert not daily_loss_breached(
        portfolio_value_usd=10_000,
        day_pnl_usd=-299,
        max_daily_loss_pct=3.0,
    )
    assert not daily_loss_breached(
        portfolio_value_usd=10_000,
        day_pnl_usd=50,
        max_daily_loss_pct=3.0,
    )


def test_daily_loss_disabled_when_pct_or_portfolio_invalid():
    assert not daily_loss_breached(
        portfolio_value_usd=0,
        day_pnl_usd=-999,
        max_daily_loss_pct=3.0,
    )
    assert not daily_loss_breached(
        portfolio_value_usd=10_000,
        day_pnl_usd=-999,
        max_daily_loss_pct=0,
    )


def test_evaluate_signal_daily_loss_kill():
    sig = NormalizedSignal(symbol="AAPL", price=100.0, reasons=["test"])
    midday = datetime(2026, 7, 13, 11, 0, tzinfo=ET)
    decision = evaluate_signal(
        sig,
        open_position_count=0,
        portfolio_value_usd=10_000,
        day_pnl_usd=-400,
        rules=StrategyRules(),
        now=midday,
    )
    assert decision.reason == "daily_loss_kill_switch"  # type: ignore[union-attr]


def test_aud_usd_roundtrip():
    assert abs(aud_to_usd(1520, usd_aud=1.52) - 1000.0) < 1e-9
    assert abs(usd_to_aud(1000, usd_aud=1.52) - 1520.0) < 1e-9


def test_sizing_portfolio_caps_broker_nl():
    cfg = CapitalConfig(portfolio_value_aud=10_000, usd_aud=1.52)
    # Cap ≈ 6578.95 USD
    capped = sizing_portfolio_usd(broker_net_liquidation_usd=50_000, config=cfg)
    assert abs(capped - cfg.portfolio_value_usd_cap) < 1e-6
    # Broker below cap → use broker
    below = sizing_portfolio_usd(broker_net_liquidation_usd=5_000, config=cfg)
    assert below == 5_000.0
    # Missing broker → AUD-derived cap
    dry = sizing_portfolio_usd(broker_net_liquidation_usd=None, config=cfg)
    assert abs(dry - cfg.portfolio_value_usd_cap) < 1e-6


def test_capital_usd_override():
    cfg = CapitalConfig(
        portfolio_value_aud=10_000,
        usd_aud=1.52,
        portfolio_value_usd_override=4_000,
    )
    assert cfg.portfolio_value_usd_cap == 4_000.0


def test_size_long_with_10k_aud_cap_usd():
    cfg = CapitalConfig(portfolio_value_aud=10_000, usd_aud=1.0)  # 1:1 for easy math
    qty = size_long(
        price=100,
        stop_price=99,
        portfolio_value_usd=cfg.portfolio_value_usd_cap,
        risk=StrategyRules().risk,
    )
    # 1% of 10k = 100 risk / $1 R = 100; 10% notional = 10 shares
    assert qty == 10


def test_session_manage_only_rejects_entries():
    # After latest_entry (15:30) but before force_close (15:51)
    late = datetime(2026, 7, 13, 15, 40, tzinfo=ET)
    status = classify_session(late, tz=ET)
    assert status.code == "manage_only"
    assert not status.allow_new_entries
