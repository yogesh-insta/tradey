"""Unit tests for daily P/L rollup (dashboard strip)."""

from __future__ import annotations

from datetime import date

from packages.observability.pnl_days import (
    SOURCE_CLOSED,
    SOURCE_CRUDE,
    SOURCE_EMPTY,
    build_pnl_days,
    crude_pnl_from_fills,
    rollup_closed_by_day,
    trade_exit_day,
)


def test_trade_exit_day():
    assert trade_exit_day({"exit_ts_utc": "2026-07-14T15:30:00+00:00"}) == "2026-07-14"
    assert trade_exit_day({"exit_ts": "2026-07-13"}) == "2026-07-13"
    assert trade_exit_day({}) is None


def test_crude_pnl_from_fills():
    fills = [
        {"side": "BUY", "qty": 10, "price": 100.0},
        {"side": "SELL", "qty": 10, "price": 101.0},
    ]
    c = crude_pnl_from_fills(fills)
    assert c["crude_pnl_usd"] == 10.0
    assert c["fill_count"] == 2


def test_rollup_closed_by_day():
    trades = [
        {
            "exit_ts_utc": "2026-07-13T20:00:00Z",
            "realized_pnl_usd": 5.0,
            "r_net": 1.0,
        },
        {
            "exit_ts_utc": "2026-07-13T21:00:00Z",
            "realized_pnl_usd": -2.0,
            "r_net": -0.5,
        },
        {
            "exit_ts_utc": "2026-07-14T16:00:00Z",
            "realized_pnl_usd": 3.0,
            "R": 1.5,  # r_net derived: 2.0
        },
    ]
    by_day = rollup_closed_by_day(trades)
    assert by_day["2026-07-13"]["trade_count"] == 2
    assert by_day["2026-07-13"]["day_pnl_usd"] == 3.0
    assert abs(by_day["2026-07-13"]["expectancy_R"] - 0.25) < 1e-9
    assert by_day["2026-07-14"]["trade_count"] == 1
    assert abs(by_day["2026-07-14"]["expectancy_R"] - 2.0) < 1e-9


def test_build_pnl_days_prefers_closed_over_crude():
    trades = [
        {
            "exit_ts_utc": "2026-07-14T12:00:00Z",
            "realized_pnl_usd": 7.5,
            "r_net": 0.75,
        }
    ]
    ledger = {
        "2026-07-14": {"crude_pnl_usd": 999.0, "fill_count": 4},
        "2026-07-13": {"crude_pnl_usd": -1.0, "fill_count": 2},
    }
    report = build_pnl_days(
        trades,
        days=3,
        end=date(2026, 7, 14),
        ledger_by_day=ledger,
    )
    rows = {r["date"]: r for r in report["rows"]}
    assert len(report["rows"]) == 3
    assert rows["2026-07-14"]["source"] == SOURCE_CLOSED
    assert rows["2026-07-14"]["day_pnl_usd"] == 7.5
    assert rows["2026-07-14"]["expectancy_R"] == 0.75
    assert rows["2026-07-13"]["source"] == SOURCE_CRUDE
    assert rows["2026-07-13"]["day_pnl_usd"] == -1.0
    assert rows["2026-07-13"]["expectancy_R"] is None
    assert rows["2026-07-12"]["source"] == SOURCE_EMPTY
    assert report["used_crude_fallback"] is True
    assert report["note"]


def test_build_pnl_days_empty_window():
    report = build_pnl_days([], days=2, end=date(2026, 7, 1))
    assert len(report["rows"]) == 2
    assert all(r["source"] == SOURCE_EMPTY for r in report["rows"])
    assert report["used_crude_fallback"] is False
    assert report["note"] is None
