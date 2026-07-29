"""Unit tests for pure filter + prefilter math (no network)."""

from __future__ import annotations

from pathlib import Path

from datetime import datetime
from zoneinfo import ZoneInfo

from packages.contracts.signals import NormalizedSignal
from services.risk_manager.policy import classify_session, size_long
from services.signal_generator.filters import (
    check_d1_above_prior_day_high,
    check_d2_prior_close_above_sma200,
    check_d3_min_gap,
    check_i1_above_premarket_high,
    check_i2_above_today_hod,
    check_i3_rvol,
    evaluate_from_metrics,
    gap_pct,
    relative_volume,
)
from services.signal_generator.prefilter import compute_gap_pct
from services.signal_generator.rules import StrategyRules, load_rules
from services.order_executor.paper_guard import check_paper_guard


ET = ZoneInfo("America/New_York")


def test_gap_math():
    assert abs(compute_gap_pct(100.0, 103.0) - 3.0) < 1e-9
    assert abs(gap_pct(100.0, 103.0) - 3.0) < 1e-9


def test_rvol():
    assert relative_volume(200, 100) == 2.0
    assert relative_volume(50, 0) == 0.0


def test_individual_filters():
    assert check_d1_above_prior_day_high(101, 100)
    assert not check_d1_above_prior_day_high(100, 100)
    assert check_d2_prior_close_above_sma200(50, 49)
    assert check_d3_min_gap(3.0, 3.0)
    assert not check_d3_min_gap(2.9, 3.0)
    assert check_i1_above_premarket_high(10, 9)
    assert check_i2_above_today_hod(10, 10)
    assert check_i3_rvol(2.0, 2.0)


def test_evaluate_from_metrics_pass():
    rules = StrategyRules()
    result = evaluate_from_metrics(
        "AAPL",
        price=110,
        prior_day_high=100,
        prior_close=105,
        sma200=90,
        gap_pct_value=4.0,
        premarket_high=108,
        today_hod=110,
        rvol=2.5,
        rules=rules,
    )
    assert result.passed
    assert "D1 ok" in result.reasons


def test_evaluate_from_metrics_fail_d3():
    rules = StrategyRules()
    result = evaluate_from_metrics(
        "AAPL",
        price=110,
        prior_day_high=100,
        prior_close=105,
        sma200=90,
        gap_pct_value=1.0,
        premarket_high=108,
        today_hod=110,
        rvol=2.5,
        rules=rules,
    )
    assert not result.passed
    assert any(r.startswith("D3") for r in result.reasons)


def test_load_rules_from_repo():
    rules = load_rules(Path(__file__).resolve().parents[1] / 'rules.json')
    assert rules.strategy_name == "Trend Join Long"
    assert rules.daily_filters.D3_min_gap_pct_from_prior_close == 3.0


def test_session_weekend():
    saturday = datetime(2026, 7, 11, 12, 0, tzinfo=ET)
    status = classify_session(saturday, tz=ET)
    assert status.code == "weekend"
    assert not status.allow_new_entries


def test_session_ok_window():
    midday = datetime(2026, 7, 13, 11, 0, tzinfo=ET)  # Monday
    status = classify_session(midday, tz=ET)
    assert status.code == "ok"
    assert status.allow_new_entries


def test_size_long():
    qty = size_long(
        price=100,
        stop_price=99,
        portfolio_value_usd=10_000,
        risk=StrategyRules().risk,
    )
    # 1% of 10k = 100 risk dollars / $1 R = 100 shares; 10% cap = 10 shares
    assert qty == 10


def test_paper_guard_blocks_live_port():
    result = check_paper_guard(port=4001, paper_trading=True)
    assert not result.ok


def test_paper_guard_allows_gateway_paper():
    result = check_paper_guard(port=4002, paper_trading=True)
    assert result.ok


def test_normalized_signal_roundtrip():
    sig = NormalizedSignal(symbol="NVDA", price=100.0, reasons=["D1 ok"])
    data = sig.model_dump()
    assert data["source"] == "custom_model"
    assert data["action"] == "BUY"


def test_prefilter_with_injected_bars(tmp_path):
    import pandas as pd
    from services.signal_generator.prefilter import run_prefilter

    # Session day = 2026-07-13 ET. Gap uses OPEN vs prior close (not close-to-close).
    idx = pd.DatetimeIndex(
        [
            datetime(2026, 7, 10, 16, 0, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 7, 13, 16, 0, tzinfo=ZoneInfo("America/New_York")),
        ]
    )
    # Open gap +4% (survives); close only +1% (would fail under the old close-gap bug).
    aapl = pd.DataFrame(
        {
            "Open": [100.0, 104.0],
            "High": [101.0, 106.0],
            "Low": [99.0, 103.0],
            "Close": [100.0, 101.0],
            "Volume": [1e6, 2e6],
        },
        index=idx,
    )
    # Open flat; close +6% — must NOT survive opening-gap screen (regression for PANW-style).
    msft = pd.DataFrame(
        {
            "Open": [200.0, 200.5],
            "High": [212.0, 212.0],
            "Low": [199.0, 199.0],
            "Close": [200.0, 212.0],
            "Volume": [1e6, 1e6],
        },
        index=idx,
    )
    bulk = pd.concat({"AAPL": aapl, "MSFT": msft}, axis=1)

    def fake_download(tickers, period="5d", threads=5):
        return bulk

    out = tmp_path / "watchlist.txt"
    as_of = datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    result = run_prefilter(
        tickers=["AAPL", "MSFT"],
        min_gap_pct=3.0,
        min_price=3.0,
        dry_run=False,
        watchlist_path=out,
        download_fn=fake_download,
        as_of=as_of,
    )
    assert result.success
    assert result.survivors_count == 1
    assert result.survivors[0].symbol == "AAPL"
    assert abs(result.survivors[0].gap_pct - 4.0) < 1e-9
    text = out.read_text()
    assert "gap +4.00%" in text
    assert "open $104.00" in text
    assert "prev $100.00" in text
    body = [
        line.split()[0]
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert body == ["AAPL"]


def test_session_open_from_intraday_picks_first_bar():
    from datetime import date
    from zoneinfo import ZoneInfo

    import pandas as pd

    from services.signal_generator.bars import session_open_from_intraday

    et = ZoneInfo("America/New_York")
    idx = pd.DatetimeIndex(
        [
            datetime(2026, 7, 14, 15, 55, tzinfo=et),
            datetime(2026, 7, 15, 9, 30, tzinfo=et),
            datetime(2026, 7, 15, 9, 35, tzinfo=et),
        ]
    )
    frame = pd.DataFrame(
        {
            "Open": [99.0, 104.0, 104.5],
            "High": [100.0, 105.0, 105.0],
            "Low": [98.0, 103.0, 104.0],
            "Close": [99.5, 104.2, 104.8],
            "Volume": [1e5, 2e5, 2e5],
        },
        index=idx,
    )
    pair = session_open_from_intraday(frame, session_date=date(2026, 7, 15), tz=et)
    assert pair == (104.0, 104.8)
    assert session_open_from_intraday(frame, session_date=date(2026, 7, 16), tz=et) is None


def test_prefilter_awaits_session_bar_pre_open(tmp_path):
    """Pre-open: last daily row is prior session → short-cooldown stub, not fake gappers."""
    import pandas as pd
    from services.signal_generator.prefilter import run_prefilter

    idx = pd.DatetimeIndex(
        [
            datetime(2026, 7, 10, 16, 0, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 7, 13, 16, 0, tzinfo=ZoneInfo("America/New_York")),
        ]
    )
    aapl = pd.DataFrame(
        {
            "Open": [100.0, 110.0],
            "High": [101.0, 112.0],
            "Low": [99.0, 109.0],
            "Close": [100.0, 111.0],
            "Volume": [1e6, 2e6],
        },
        index=idx,
    )
    bulk = pd.concat({"AAPL": aapl}, axis=1)

    out = tmp_path / "watchlist.txt"
    # "Today" is Jul 14 — Yahoo still only has Jul 13 bar; no 5m yet.
    as_of = datetime(2026, 7, 14, 7, 30, tzinfo=ZoneInfo("America/New_York"))
    result = run_prefilter(
        tickers=["AAPL"],
        min_gap_pct=3.0,
        min_price=3.0,
        dry_run=False,
        watchlist_path=out,
        download_fn=lambda *a, **k: bulk,
        session_open_fn=lambda *a, **k: {},
        as_of=as_of,
    )
    assert result.success is False
    assert result.pending_session == 1
    assert "awaiting today's session open" in (result.error or "")
    assert "# ERROR:" in out.read_text()


def test_prefilter_5m_open_fallback_when_daily_lags(tmp_path):
    """Daily chart still prior session, but 5m has today's open → screen normally."""
    import pandas as pd
    from services.signal_generator.prefilter import run_prefilter

    idx = pd.DatetimeIndex(
        [
            datetime(2026, 7, 10, 16, 0, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 7, 13, 16, 0, tzinfo=ZoneInfo("America/New_York")),
        ]
    )
    # Last daily = Jul 13 close 100; "today" Jul 14 open comes from 5m only.
    aapl = pd.DataFrame(
        {
            "Open": [98.0, 99.0],
            "High": [101.0, 101.0],
            "Low": [97.0, 98.0],
            "Close": [99.0, 100.0],
            "Volume": [1e6, 2e6],
        },
        index=idx,
    )
    bulk = pd.concat({"AAPL": aapl}, axis=1)
    as_of = datetime(2026, 7, 14, 10, 15, tzinfo=ZoneInfo("America/New_York"))
    out = tmp_path / "watchlist.txt"

    def fake_opens(tickers, *, session_date, tz):
        assert "AAPL" in tickers
        assert session_date == as_of.date()
        return {"AAPL": (104.0, 104.5)}  # +4% open gap vs prior close 100

    result = run_prefilter(
        tickers=["AAPL"],
        min_gap_pct=3.0,
        min_price=3.0,
        dry_run=False,
        watchlist_path=out,
        download_fn=lambda *a, **k: bulk,
        session_open_fn=fake_opens,
        as_of=as_of,
    )
    assert result.success
    assert result.open_fallback == 1
    assert result.pending_session == 0
    assert result.survivors_count == 1
    assert result.survivors[0].symbol == "AAPL"
    assert abs(result.survivors[0].gap_pct - 4.0) < 1e-9
    assert "gap +4.00%" in out.read_text()
    assert "open $104.00" in out.read_text()
    assert "prev $100.00" in out.read_text()


def test_daily_bars_failure_reason_surfaces_yahoo_http():
    from services.signal_generator.filters import _daily_bars_failure_reason

    assert (
        _daily_bars_failure_reason(None, "yahoo HTTP 403 for BHP.AX")
        == "daily bars unavailable: yahoo HTTP 403 for BHP.AX"
    )
    assert _daily_bars_failure_reason(None, None) == "daily bars unavailable: empty response"

    import pandas as pd

    short = pd.DataFrame({"Close": [1.0] * 50})
    assert _daily_bars_failure_reason(short, None) == (
        "insufficient daily history for SMA200 (50 bars)"
    )
    assert "last Yahoo error" in _daily_bars_failure_reason(
        short, "yahoo rate-limited (HTTP 429) for KAR.AX after 4 retries"
    )


def test_evaluate_symbol_surfaces_yahoo_error(monkeypatch):
    """Empty daily must not look like a genuine short SMA200 series."""
    import pandas as pd

    from services.signal_generator import filters as filters_mod

    calls: list[tuple[str, str]] = []

    def fake_download(yahoo_symbol, *, period="1y", interval="1d"):
        calls.append((period, interval))
        return pd.DataFrame()

    monkeypatch.setattr(filters_mod, "download_symbol_history", fake_download)
    monkeypatch.setattr(
        filters_mod,
        "get_last_yahoo_error",
        lambda: "yahoo HTTP 403 for KAR.AX: Forbidden",
    )

    ev = filters_mod.evaluate_symbol("KAR")
    assert not ev.passed
    assert ev.reasons == ["daily bars unavailable: yahoo HTTP 403 for KAR.AX: Forbidden"]
    assert calls == [("1y", "1d"), ("5d", "5m")]
