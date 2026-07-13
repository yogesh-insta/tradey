"""Unit tests for expectancy / attribution helpers (plan 08)."""

from __future__ import annotations

from packages.observability.expectancy import (
    expectancy_R,
    expectancy_from_wr,
    exit_attribution,
    per_trade_r_net,
    rolling_expectancy_flag,
    summarize_closed_trades,
    win_rate,
)


def test_per_trade_r_net():
    assert per_trade_r_net(4.0, 2.0) == 2.0
    assert per_trade_r_net(-1.0, 2.0) == -0.5
    assert per_trade_r_net(1.0, 0) is None


def test_expectancy_and_win_rate():
    rs = [1.0, 1.0, -1.0, -1.0]
    assert win_rate(rs) == 0.5
    assert expectancy_R(rs) == 0.0
    assert abs(expectancy_from_wr(0.5, 1.0, -1.0) - 0.0) < 1e-9


def test_exit_attribution():
    trades = [
        {"exit_reason": "stop", "r_net": -1.0},
        {"exit_reason": "trail", "r_net": 1.5},
        {"exit_reason": "force_close", "r_net": -0.2},
        {"exit_reason": "trail", "r_net": 0.5},
    ]
    attr = exit_attribution(trades)
    assert attr["trail"]["count"] == 2
    assert attr["stop"]["count"] == 1
    assert abs(attr["trail"]["expectancy_R"] - 1.0) < 1e-9


def test_summarize_and_kill_flag():
    trades = [{"exit_reason": "stop", "r_net": -0.5} for _ in range(10)]
    summary = summarize_closed_trades(trades)
    assert summary["n_closed"] == 10
    assert summary["expectancy_R"] == -0.5
    assert summary["kill_check"]["triggered"] is True


def test_kill_not_triggered_on_empty():
    flag = rolling_expectancy_flag([])
    assert flag["triggered"] is False
    assert expectancy_R([]) is None
