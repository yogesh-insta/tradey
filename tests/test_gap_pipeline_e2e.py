"""End-to-end: open-gap prefilter → watchlist labels → entry filters → BUY signal.

No network. Proves the gap/HOD fixes would produce a signal on a valid setup
and reject the old close-to-close / daily-HOD failure modes.
"""

from __future__ import annotations

import datetime as dt_mod
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from services.market_profile import activate_market
from services.session.entry_tick import run_entry_tick
from services.signal_generator import filters as filters_mod
from services.signal_generator.evaluate import scan_watchlist
from services.signal_generator.prefilter import (
    compute_gap_pct,
    read_watchlist,
    run_prefilter,
)
from services.signal_generator.rules import StrategyRules, load_rules
from services.risk_manager.capital import CapitalConfig

ET = ZoneInfo("America/New_York")
SESSION = datetime(2026, 7, 15, 11, 5, tzinfo=ET)  # mid RTH entry window


def _freeze_session_clock(monkeypatch, when: datetime = SESSION) -> None:
    """evaluate_symbol uses datetime.now(profile.tz) for session date."""

    class _FrozenDateTime(dt_mod.datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return when.astimezone(tz) if tz is not None else when.replace(tzinfo=None)

    monkeypatch.setattr(filters_mod, "datetime", _FrozenDateTime)


def _daily_history(
    *,
    session: datetime,
    prior_close: float,
    prior_high: float,
    today_open: float,
    today_high: float,
    today_low: float,
    today_close: float,
    today_volume: float = 5_000_000,
    base_close: float = 80.0,
    base_volume: float = 1_000_000,
    n_prior: int = 220,
) -> pd.DataFrame:
    """Build ≥201 daily bars ending on `session` date (ET)."""
    rows = []
    # Flat history so SMA200 ≈ base_close < prior_close (D2 ok).
    start = session.date() - timedelta(days=n_prior + 5)
    d = start
    while len(rows) < n_prior:
        if d.weekday() < 5:
            ts = datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)
            rows.append(
                {
                    "ts": ts,
                    "Open": base_close,
                    "High": base_close + 1,
                    "Low": base_close - 1,
                    "Close": base_close,
                    "Volume": base_volume,
                }
            )
        d += timedelta(days=1)

    prior_day = session.date() - timedelta(days=1)
    while prior_day.weekday() >= 5:
        prior_day -= timedelta(days=1)
    rows.append(
        {
            "ts": datetime(prior_day.year, prior_day.month, prior_day.day, 16, 0, tzinfo=ET),
            "Open": prior_close - 1,
            "High": prior_high,
            "Low": prior_close - 2,
            "Close": prior_close,
            "Volume": base_volume,
        }
    )
    rows.append(
        {
            "ts": datetime(session.year, session.month, session.day, 16, 0, tzinfo=ET),
            "Open": today_open,
            "High": today_high,
            "Low": today_low,
            "Close": today_close,
            "Volume": today_volume,
        }
    )
    frame = pd.DataFrame(rows).set_index("ts")
    return frame


def _intraday_hod_join(
    *,
    session: datetime,
    open_px: float,
    hod: float,
    lod: float,
    last: float,
    pm_high: float,
) -> pd.DataFrame:
    """5m bars: premarket + RTH ending at HOD join (last == hod)."""
    day = session.date()
    bars = [
        # Premarket
        (
            datetime(day.year, day.month, day.day, 8, 0, tzinfo=ET),
            open_px - 1,
            pm_high,
            open_px - 2,
            open_px - 0.5,
            50_000,
        ),
        (
            datetime(day.year, day.month, day.day, 9, 0, tzinfo=ET),
            open_px - 0.5,
            pm_high,
            open_px - 1,
            open_px - 0.2,
            50_000,
        ),
        # RTH open
        (
            datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET),
            open_px,
            open_px + 0.5,
            lod,
            open_px + 0.2,
            200_000,
        ),
        (
            datetime(day.year, day.month, day.day, 10, 0, tzinfo=ET),
            open_px + 0.2,
            hod - 0.5,
            open_px,
            hod - 0.5,
            300_000,
        ),
        # Current bar prints the HOD — I2 join
        (
            datetime(day.year, day.month, day.day, 11, 0, tzinfo=ET),
            hod - 0.5,
            hod,
            hod - 1,
            last,
            2_000_000,
        ),
    ]
    idx = [b[0] for b in bars]
    return pd.DataFrame(
        {
            "Open": [b[1] for b in bars],
            "High": [b[2] for b in bars],
            "Low": [b[3] for b in bars],
            "Close": [b[4] for b in bars],
            "Volume": [b[5] for b in bars],
        },
        index=pd.DatetimeIndex(idx),
    )


@pytest.fixture
def us_market(tmp_path, monkeypatch):
    activate_market("us")
    monkeypatch.setenv("MANAGED_POSITIONS_PATH", str(tmp_path / "open.json"))
    return tmp_path


def test_e2e_open_gap_watchlist_to_buy_signal(us_market, monkeypatch):
    """Valid open gapper → prefilter file math matches → scan emits BUY."""
    _freeze_session_clock(monkeypatch)

    # Prior close 100, open 105 → true open gap +5%. Day fades in daily Close to 101
    # (old D3 used Close and would have failed); open-gap D3 must still pass.
    prior_close, prior_high = 100.0, 102.0
    today_open = 105.0
    hod, lod, last = 108.0, 104.0, 108.0  # at HOD, above PDH + PM
    pm_high = 104.5

    daily = _daily_history(
        session=SESSION,
        prior_close=prior_close,
        prior_high=prior_high,
        today_open=today_open,
        today_high=hod,
        today_low=lod,
        today_close=101.0,  # faded vs open — close-to-close gap only +1%
        today_volume=5_000_000,
        base_close=80.0,
    )
    intra = _intraday_hod_join(
        session=SESSION,
        open_px=today_open,
        hod=hod,
        lod=lod,
        last=last,
        pm_high=pm_high,
    )

    # Spot-check math the bug hid before
    assert abs(compute_gap_pct(prior_close, today_open) - 5.0) < 1e-9
    assert abs((101.0 - prior_close) / prior_close * 100 - 1.0) < 1e-9

    watchlist = us_market / "watchlist.txt"

    def fake_download_daily(tickers, period="5d", threads=5):
        return pd.concat({"GAPR": daily.tail(5)}, axis=1)

    pref = run_prefilter(
        tickers=["GAPR"],
        min_gap_pct=3.0,
        min_price=3.0,
        dry_run=False,
        watchlist_path=watchlist,
        download_fn=fake_download_daily,
        as_of=SESSION,
    )
    assert pref.success
    assert pref.survivors_count == 1
    assert abs(pref.survivors[0].gap_pct - 5.0) < 1e-9
    text = watchlist.read_text()
    # Labels MUST match open-gap math (the CRWD-list failure mode).
    assert "gap +5.00%" in text
    assert "open $105.00" in text
    assert "prev $100.00" in text
    assert read_watchlist(watchlist) == ["GAPR"]

    def fake_symbol_history(yahoo, period="1y", interval="1d"):
        if interval == "1d":
            return daily
        if interval == "5m":
            return intra
        return pd.DataFrame()

    monkeypatch.setattr(filters_mod, "download_symbol_history", fake_symbol_history)
    monkeypatch.setattr(filters_mod, "get_last_yahoo_error", lambda: None)

    rules = load_rules(Path(__file__).resolve().parents[1] / "rules.json")
    scan = scan_watchlist(["GAPR"], rules=rules, watchlist_path=watchlist)
    assert scan.passed_count == 1
    assert len(scan.signals) == 1
    sig = scan.signals[0]
    assert sig.action == "BUY"
    assert sig.symbol == "GAPR"
    assert abs(sig.price - last) < 1e-9
    metrics = sig.metadata["metrics"]
    assert metrics["gap_basis"] == "open_vs_prior_close"
    assert abs(metrics["gap_pct"] - 5.0) < 1e-9
    assert abs(metrics["today_open"] - today_open) < 1e-9
    # Entrypoint: dry-run still sees filter pass + sized intent path
    entry = run_entry_tick(
        rules=rules,
        capital=CapitalConfig(),
        dry_run=True,
        symbols=["GAPR"],
        watchlist_path=watchlist,
        session_code="ok",
        now=SESSION,
    )
    assert entry.evaluated == 1
    assert entry.filter_passed == 1
    assert entry.risk_approved == 1
    assert entry.executed == 0  # dry-run
    assert entry.intents[0].symbol == "GAPR"


def test_e2e_rejects_close_only_move_like_panw(us_market, monkeypatch):
    """Open flat / huge day-close → must NOT enter watchlist (old close-gap bug)."""
    _freeze_session_clock(monkeypatch)
    prior_close = 330.30
    today_open = 330.80  # ~0.15% open gap
    daily = _daily_history(
        session=SESSION,
        prior_close=prior_close,
        prior_high=332.0,
        today_open=today_open,
        today_high=355.0,
        today_low=330.0,
        today_close=353.0,  # huge close-to-close day
        base_close=300.0,
    )

    def fake_download(tickers, period="5d", threads=5):
        return pd.concat({"PANW": daily.tail(5)}, axis=1)

    out = us_market / "wl.txt"
    result = run_prefilter(
        tickers=["PANW"],
        min_gap_pct=3.0,
        min_price=3.0,
        dry_run=False,
        watchlist_path=out,
        download_fn=fake_download,
        as_of=SESSION,
    )
    assert result.success
    assert result.survivors_count == 0
    assert result.below_gap == 1
    assert read_watchlist(out) == []


def test_e2e_pre_open_does_not_seed_yesterdays_movers(us_market, monkeypatch):
    """07:30 ET: no session bar / no 5m → pending stub (not yesterday close-to-close list)."""
    pre_open = datetime(2026, 7, 15, 7, 30, tzinfo=ET)
    # Last bar is still Jul 14
    prior = datetime(2026, 7, 14, 16, 0, tzinfo=ET)
    earlier = datetime(2026, 7, 13, 16, 0, tzinfo=ET)
    frame = pd.DataFrame(
        {
            "Open": [100.0, 112.0],
            "High": [101.0, 115.0],
            "Low": [99.0, 110.0],
            "Close": [100.0, 114.0],
            "Volume": [1e6, 2e6],
        },
        index=pd.DatetimeIndex([earlier, prior]),
    )

    out = us_market / "wl.txt"
    result = run_prefilter(
        tickers=["CRWD"],
        min_gap_pct=3.0,
        min_price=3.0,
        dry_run=False,
        watchlist_path=out,
        download_fn=lambda *a, **k: pd.concat({"CRWD": frame}, axis=1),
        session_open_fn=lambda *a, **k: {},
        as_of=pre_open,
    )
    assert result.success is False
    assert result.pending_session == 1
    assert "awaiting today's session open" in (result.error or "")
    assert "# ERROR:" in out.read_text()
    assert "CRWD" not in {
        line.split()[0]
        for line in out.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def test_e2e_5m_fallback_builds_watchlist_when_daily_lags(us_market, monkeypatch):
    """ASX-style lag: daily still prior day, 5m open available → real open-gap list."""
    _freeze_session_clock(monkeypatch)
    session = datetime(2026, 7, 15, 10, 20, tzinfo=ET)
    prior = datetime(2026, 7, 14, 16, 0, tzinfo=ET)
    earlier = datetime(2026, 7, 13, 16, 0, tzinfo=ET)
    # Prior close 100; no Jul 15 daily row yet.
    frame = pd.DataFrame(
        {
            "Open": [98.0, 99.0],
            "High": [101.0, 101.0],
            "Low": [97.0, 98.0],
            "Close": [99.0, 100.0],
            "Volume": [1e6, 2e6],
        },
        index=pd.DatetimeIndex([earlier, prior]),
    )
    out = us_market / "wl.txt"
    result = run_prefilter(
        tickers=["BHP"],
        min_gap_pct=3.0,
        min_price=3.0,
        dry_run=False,
        watchlist_path=out,
        download_fn=lambda *a, **k: pd.concat({"BHP": frame}, axis=1),
        session_open_fn=lambda *a, **k: {"BHP": (105.0, 105.2)},
        as_of=session,
    )
    assert result.success
    assert result.open_fallback == 1
    assert result.survivors_count == 1
    assert abs(result.survivors[0].gap_pct - 5.0) < 1e-9
    assert read_watchlist(out) == ["BHP"]


def test_e2e_old_close_gap_d3_would_block_but_open_gap_passes(monkeypatch):
    """Regression: faded daily Close must not kill D3 when open gap was valid."""
    _freeze_session_clock(monkeypatch)
    activate_market("us")
    rules = StrategyRules()  # defaults: D1–I3 on

    # Metrics as evaluate_from_metrics sees them after open-gap fix
    result = filters_mod.evaluate_from_metrics(
        "GAPR",
        price=108.0,  # live at HOD
        prior_day_high=102.0,
        prior_close=100.0,
        sma200=80.0,
        gap_pct_value=5.0,  # from OPEN 105
        premarket_high=104.5,
        today_hod=108.0,
        rvol=3.0,
        rules=rules,
    )
    assert result.passed

    # Same tape if D3 wrongly used close-to-close (+1%) would fail:
    faded = filters_mod.evaluate_from_metrics(
        "GAPR",
        price=108.0,
        prior_day_high=102.0,
        prior_close=100.0,
        sma200=80.0,
        gap_pct_value=1.0,  # old (close 101 vs prior 100)
        premarket_high=104.5,
        today_hod=108.0,
        rvol=3.0,
        rules=rules,
    )
    assert not faded.passed
    assert any(r.startswith("D3") for r in faded.reasons)
