"""Trend Join Long filter evaluation (D1–D3 daily, I1–I3 intraday).

Pure metric checks are unit-tested; bar assembly uses Yahoo chart API (no IBKR).
"""

from __future__ import annotations

import datetime as dt_mod
from typing import Any, Optional

import pandas as pd

from packages.contracts.signals import FilterEvaluation, NormalizedSignal
from services.market_profile import get_market_profile
from services.signal_generator.bars import download_symbol_history, get_last_yahoo_error
from services.signal_generator.rules import StrategyRules, load_rules

# Keep stdlib anchors for isinstance / construction; tests may freeze `.datetime.now`.
datetime = dt_mod.datetime
date = dt_mod.date


def _daily_bars_failure_reason(daily: Optional[pd.DataFrame], yahoo_err: Optional[str]) -> str:
    """Explain missing / short daily history; prefer real Yahoo errors over SMA200."""
    n = 0 if daily is None else len(daily)
    if n == 0:
        if yahoo_err:
            return f"daily bars unavailable: {yahoo_err}"
        return "daily bars unavailable: empty response"
    if yahoo_err:
        return f"insufficient daily history for SMA200 ({n} bars); last Yahoo error: {yahoo_err}"
    return f"insufficient daily history for SMA200 ({n} bars)"


def check_d1_above_prior_day_high(price: float, prior_day_high: float) -> bool:
    return price > prior_day_high


def check_d2_prior_close_above_sma200(prior_close: float, sma200: float) -> bool:
    return prior_close > sma200


def check_d3_min_gap(gap_pct: float, min_gap_pct: float) -> bool:
    return gap_pct >= min_gap_pct


def check_i1_above_premarket_high(price: float, premarket_high: float) -> bool:
    return price > premarket_high


def check_i2_above_today_hod(price: float, today_hod: float, *, epsilon: float = 1e-9) -> bool:
    """Join strength: price at/near HOD (within epsilon of session high)."""
    return price + epsilon >= today_hod


def check_i3_rvol(rvol: float, min_rvol: float) -> bool:
    return rvol >= min_rvol


def gap_pct(prior_close: float, current: float) -> float:
    if prior_close <= 0:
        raise ValueError("prior_close must be positive")
    return (current - prior_close) / prior_close * 100.0


def relative_volume(today_volume: float, avg_volume: float) -> float:
    if avg_volume <= 0:
        return 0.0
    return today_volume / avg_volume


def sma(series: pd.Series, window: int) -> Optional[float]:
    if series is None or len(series) < window:
        return None
    return float(series.tail(window).mean())


def evaluate_from_metrics(
    symbol: str,
    *,
    price: float,
    prior_day_high: float,
    prior_close: float,
    sma200: float,
    gap_pct_value: float,
    premarket_high: float,
    today_hod: float,
    rvol: float,
    rules: StrategyRules,
) -> FilterEvaluation:
    """Apply enabled D1–D3 / I1–I3 checks; fail-fast with reasons."""
    reasons: list[str] = []
    metrics: dict[str, Any] = {
        "price": price,
        "prior_day_high": prior_day_high,
        "prior_close": prior_close,
        "sma200": sma200,
        "gap_pct": gap_pct_value,
        "premarket_high": premarket_high,
        "today_hod": today_hod,
        "rvol": rvol,
    }
    # today_low attached by evaluate_symbol when available
    daily = rules.daily_filters
    intra = rules.intraday_filters

    if daily.D1_above_prior_day_high:
        if not check_d1_above_prior_day_high(price, prior_day_high):
            return FilterEvaluation(
                symbol=symbol,
                passed=False,
                price=price,
                reasons=[f"D1 fail: price {price:.2f} <= prior high {prior_day_high:.2f}"],
                metrics=metrics,
            )
        reasons.append("D1 ok")

    if daily.D2_prior_close_above_sma200:
        if not check_d2_prior_close_above_sma200(prior_close, sma200):
            return FilterEvaluation(
                symbol=symbol,
                passed=False,
                price=price,
                reasons=[f"D2 fail: prior close {prior_close:.2f} <= SMA200 {sma200:.2f}"],
                metrics=metrics,
            )
        reasons.append("D2 ok")

    min_gap = daily.D3_min_gap_pct_from_prior_close
    if not check_d3_min_gap(gap_pct_value, min_gap):
        return FilterEvaluation(
            symbol=symbol,
            passed=False,
            price=price,
            reasons=[f"D3 fail: gap {gap_pct_value:.2f}% < {min_gap}%"],
            metrics=metrics,
        )
    reasons.append("D3 ok")

    if intra.I1_above_premarket_high:
        if not check_i1_above_premarket_high(price, premarket_high):
            return FilterEvaluation(
                symbol=symbol,
                passed=False,
                price=price,
                reasons=[f"I1 fail: price {price:.2f} <= PM high {premarket_high:.2f}"],
                metrics=metrics,
            )
        reasons.append("I1 ok")

    if intra.I2_above_today_hod:
        if not check_i2_above_today_hod(price, today_hod):
            return FilterEvaluation(
                symbol=symbol,
                passed=False,
                price=price,
                reasons=[f"I2 fail: price {price:.2f} < today HOD {today_hod:.2f}"],
                metrics=metrics,
            )
        reasons.append("I2 ok")

    if not check_i3_rvol(rvol, intra.I3_rvol_min):
        return FilterEvaluation(
            symbol=symbol,
            passed=False,
            price=price,
            reasons=[f"I3 fail: rvol {rvol:.2f}x < {intra.I3_rvol_min}x"],
            metrics=metrics,
        )
    reasons.append("I3 ok")

    filters_fired = {
        "D1": bool(daily.D1_above_prior_day_high),
        "D2": bool(daily.D2_prior_close_above_sma200),
        "D3": True,
        "I1": bool(intra.I1_above_premarket_high),
        "I2": bool(intra.I2_above_today_hod),
        "I3": True,
    }
    metrics["filters_fired"] = filters_fired

    return FilterEvaluation(
        symbol=symbol,
        passed=True,
        price=price,
        reasons=reasons,
        metrics=metrics,
    )


def evaluate_symbol(
    symbol: str,
    *,
    rules: Optional[StrategyRules] = None,
) -> FilterEvaluation:
    """Fetch Yahoo bars and evaluate filters for one IBKR-format symbol.

    D3 uses **opening gap** (today open vs prior close) — same definition as prefilter.
    Price / HOD / LOD prefer today's 5m session bars when available (I2 needs live HOD join).
    When the daily chart still lacks today's row, prior metrics come from the last daily
    bar and today's open/HOD/price come from session 5m (ASX daily lag path).
    """
    rules = rules or load_rules()
    profile = get_market_profile()
    yahoo = profile.to_yahoo(symbol)
    session_date = datetime.now(profile.tz).date()

    try:
        daily = download_symbol_history(yahoo, period="1y", interval="1d")
        # Capture before intraday fetch — fetch_chart clears _last_yahoo_error on entry.
        daily_err = get_last_yahoo_error()
        intraday = download_symbol_history(yahoo, period="5d", interval="5m")
    except Exception as exc:  # noqa: BLE001
        return FilterEvaluation(
            symbol=symbol,
            passed=False,
            reasons=[f"bar download failed: {exc}"],
        )

    if daily is None or daily.empty:
        return FilterEvaluation(
            symbol=symbol,
            passed=False,
            reasons=[_daily_bars_failure_reason(daily, daily_err)],
        )

    bar_date = _bar_date(daily.index[-1], profile.tz)
    session_5m = _session_intraday(intraday, profile.tz, session_date)
    has_session_5m = session_5m is not None and not session_5m.empty

    if bar_date == session_date:
        if len(daily) < 201:
            return FilterEvaluation(
                symbol=symbol,
                passed=False,
                reasons=[_daily_bars_failure_reason(daily, daily_err)],
            )
        prior = daily.iloc[-2]
        today = daily.iloc[-1]
        prior_close = float(prior["Close"])
        prior_day_high = float(prior["High"])
        today_open = float(today["Open"])
        price = float(today["Close"])
        today_hod = float(today["High"])
        today_low = float(today["Low"])
        today_vol = float(today["Volume"]) if "Volume" in today.index else 0.0
        hist_closes = daily["Close"].astype(float).iloc[:-1]
        hist_volumes = daily["Volume"].astype(float).iloc[:-1]
        gap_basis = "open_vs_prior_close"
    else:
        # Daily lag: last row is prior session. Need 5m for today's open/price/HOD.
        if len(daily) < 200:
            return FilterEvaluation(
                symbol=symbol,
                passed=False,
                reasons=[_daily_bars_failure_reason(daily, daily_err)],
            )
        if not has_session_5m:
            return FilterEvaluation(
                symbol=symbol,
                passed=False,
                reasons=[
                    f"awaiting today's session open (last daily={bar_date.isoformat()}, "
                    f"session={session_date.isoformat()})"
                ],
            )
        prior = daily.iloc[-1]
        prior_close = float(prior["Close"])
        prior_day_high = float(prior["High"])
        today_open = float(session_5m["Open"].iloc[0])
        price = float(session_5m["Close"].iloc[-1])
        today_hod = float(session_5m["High"].max())
        today_low = float(session_5m["Low"].min())
        today_vol = (
            float(session_5m["Volume"].sum()) if "Volume" in session_5m.columns else 0.0
        )
        hist_closes = daily["Close"].astype(float)
        hist_volumes = daily["Volume"].astype(float)
        gap_basis = "open_vs_prior_close_5m"

    if has_session_5m and bar_date == session_date:
        price = float(session_5m["Close"].iloc[-1])
        today_hod = float(session_5m["High"].max())
        today_low = float(session_5m["Low"].min())
        if "Volume" in session_5m.columns:
            today_vol = float(session_5m["Volume"].sum())

    sma200_val = sma(hist_closes, 200)
    if sma200_val is None:
        return FilterEvaluation(
            symbol=symbol,
            passed=False,
            price=price,
            reasons=["could not compute SMA200"],
        )

    try:
        # Opening gap — aligned with morning prefilter (not last-price vs prior close).
        gap = gap_pct(prior_close, today_open)
    except ValueError as exc:
        return FilterEvaluation(symbol=symbol, passed=False, reasons=[str(exc)])

    rth_open = _rth_open_hhmm(profile)
    premarket_high = _premarket_high(
        intraday,
        fallback=today_open,
        tz=profile.tz,
        session_date=session_date,
        open_hhmm=rth_open,
    )

    vol_lookback = rules.intraday_filters.I3_rvol_lookback_days
    avg_vol = float(hist_volumes.tail(vol_lookback).mean()) if len(hist_volumes) else 0.0
    rvol = relative_volume(today_vol, avg_vol)

    result = evaluate_from_metrics(
        symbol,
        price=price,
        prior_day_high=prior_day_high,
        prior_close=prior_close,
        sma200=sma200_val,
        gap_pct_value=gap,
        premarket_high=premarket_high,
        today_hod=today_hod,
        rvol=rvol,
        rules=rules,
    )
    result.metrics["today_low"] = today_low
    result.metrics["today_open"] = today_open
    result.metrics["gap_basis"] = gap_basis
    return result


def evaluation_to_signal(
    evaluation: FilterEvaluation,
    *,
    rules: Optional[StrategyRules] = None,
) -> Optional[NormalizedSignal]:
    if not evaluation.passed:
        return None
    rules = rules or load_rules()
    profile = get_market_profile()
    return NormalizedSignal(
        source="custom_model",
        strategy_name=rules.strategy_name,
        symbol=evaluation.symbol,
        venue=profile.ibkr_exchange,
        currency=profile.currency,
        action="BUY",
        timeframe=rules.trade_timeframe,
        price=evaluation.price,
        reasons=list(evaluation.reasons),
        metadata={
            "filters": "D1-D3,I1-I3",
            "market": profile.market,
            "filters_fired": (evaluation.metrics or {}).get("filters_fired")
            or {"D1": True, "D2": True, "D3": True, "I1": True, "I2": True, "I3": True},
            "metrics": evaluation.metrics,
            "direction": rules.direction,
        },
    )


def _rth_open_hhmm(profile) -> str:
    """Wall-clock RTH open for premarket cutoff (US 09:30; ASX uses profile open)."""
    if getattr(profile, "market", "") == "us":
        return "09:30"
    return getattr(profile, "market_open_hhmm", "10:00") or "10:00"


def _bar_date(ts, zone) -> date:
    """Calendar date of a bar timestamp in `zone` (robust to frozen datetime.now)."""
    if hasattr(ts, "to_pydatetime"):
        dt = ts.to_pydatetime()
    else:
        dt = ts
    # datetime is a subclass of date — always normalize clock objects.
    if isinstance(dt, dt_mod.datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zone)
        else:
            dt = dt.astimezone(zone)
        return dt.date()
    if isinstance(dt, dt_mod.date):
        return dt
    raise TypeError(f"unsupported bar timestamp type: {type(ts)!r}")


def _session_intraday(
    intraday: Optional[pd.DataFrame],
    zone,
    session_date,
) -> Optional[pd.DataFrame]:
    if intraday is None or intraday.empty:
        return None
    try:
        times = _index_times(intraday.index, zone)
        mask = times.date == session_date if hasattr(times, "date") else [
            t.date() == session_date for t in times
        ]
        out = intraday.loc[mask]
        return out if not out.empty else None
    except Exception:  # noqa: BLE001
        return None


def _index_times(idx, zone):
    if getattr(idx, "tz", None) is None:
        return idx.tz_localize(zone) if hasattr(idx, "tz_localize") else idx
    return idx.tz_convert(zone)


def _premarket_high(
    intraday: Optional[pd.DataFrame],
    *,
    fallback: float,
    tz=None,
    session_date=None,
    open_hhmm: str = "09:30",
) -> float:
    """Max high on today's bars before RTH open (profile TZ)."""
    if intraday is None or intraday.empty or "High" not in intraday.columns:
        return fallback
    try:
        from zoneinfo import ZoneInfo

        zone = tz or ZoneInfo("America/New_York")
        open_h, open_m = (int(x) for x in open_hhmm.split(":")[:2])
        times = _index_times(intraday.index, zone)
        if session_date is None:
            session_date = times[-1].date()
        before_open = (times.hour < open_h) | (
            (times.hour == open_h) & (times.minute < open_m)
        )
        on_day = times.date == session_date if hasattr(times, "date") else [
            t.date() == session_date for t in times
        ]
        pm = intraday.loc[before_open & on_day]
        if pm.empty:
            return fallback
        return float(pm["High"].max())
    except Exception:  # noqa: BLE001
        return fallback
