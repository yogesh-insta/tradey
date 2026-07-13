"""Trend Join Long filter evaluation (D1–D3 daily, I1–I3 intraday).

Pure metric checks are unit-tested; bar assembly uses Yahoo chart API (no IBKR).
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from packages.contracts.signals import FilterEvaluation, NormalizedSignal
from services.signal_generator.bars import download_symbol_history, ibkr_to_yahoo
from services.signal_generator.rules import StrategyRules, load_rules


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
        "D1": True,
        "D2": True,
        "D3": True,
        "I1": True,
        "I2": True,
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
    """Fetch Yahoo bars and evaluate filters for one IBKR-format symbol."""
    rules = rules or load_rules()
    yahoo = ibkr_to_yahoo(symbol)

    try:
        daily = download_symbol_history(yahoo, period="1y", interval="1d")
        intraday = download_symbol_history(yahoo, period="5d", interval="5m")
    except Exception as exc:  # noqa: BLE001
        return FilterEvaluation(
            symbol=symbol,
            passed=False,
            reasons=[f"bar download failed: {exc}"],
        )

    if daily is None or len(daily) < 201:
        return FilterEvaluation(
            symbol=symbol,
            passed=False,
            reasons=["insufficient daily history for SMA200"],
        )

    prior = daily.iloc[-2]
    today = daily.iloc[-1]
    prior_close = float(prior["Close"])
    prior_day_high = float(prior["High"])
    price = float(today["Close"])
    today_hod = float(today["High"])
    today_low = float(today["Low"])
    today_vol = float(today["Volume"]) if "Volume" in today.index else 0.0

    close_series = daily["Close"].astype(float)
    sma200_val = sma(close_series.iloc[:-1], 200)
    if sma200_val is None:
        return FilterEvaluation(
            symbol=symbol,
            passed=False,
            price=price,
            reasons=["could not compute SMA200"],
        )

    try:
        gap = gap_pct(prior_close, price)
    except ValueError as exc:
        return FilterEvaluation(symbol=symbol, passed=False, reasons=[str(exc)])

    # Premarket high proxy: max high on today's 5m bars before 09:30 ET when available.
    premarket_high = _premarket_high(intraday, fallback=float(today["Open"]))

    vol_lookback = rules.intraday_filters.I3_rvol_lookback_days
    avg_vol = float(daily["Volume"].astype(float).iloc[-(vol_lookback + 1) : -1].mean())
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
    return result


def evaluation_to_signal(
    evaluation: FilterEvaluation,
    *,
    rules: Optional[StrategyRules] = None,
) -> Optional[NormalizedSignal]:
    if not evaluation.passed:
        return None
    rules = rules or load_rules()
    return NormalizedSignal(
        source="custom_model",
        strategy_name=rules.strategy_name,
        symbol=evaluation.symbol,
        venue="SMART",
        currency="USD",
        action="BUY",
        timeframe=rules.trade_timeframe,
        price=evaluation.price,
        reasons=list(evaluation.reasons),
        metadata={
            "filters": "D1-D3,I1-I3",
            "filters_fired": (evaluation.metrics or {}).get("filters_fired")
            or {"D1": True, "D2": True, "D3": True, "I1": True, "I2": True, "I3": True},
            "metrics": evaluation.metrics,
            "direction": rules.direction,
        },
    )


def _premarket_high(intraday: Optional[pd.DataFrame], *, fallback: float) -> float:
    if intraday is None or intraday.empty or "High" not in intraday.columns:
        return fallback
    try:
        idx = intraday.index
        if getattr(idx, "tz", None) is None:
            # Assume US/Eastern naive timestamps from yfinance
            from zoneinfo import ZoneInfo

            et = ZoneInfo("America/New_York")
            times = idx.tz_localize(et) if hasattr(idx, "tz_localize") else idx
        else:
            from zoneinfo import ZoneInfo

            times = idx.tz_convert(ZoneInfo("America/New_York"))
        mask = (times.hour < 9) | ((times.hour == 9) & (times.minute < 30))
        # Only today's session
        today = times[-1].date()
        mask = mask & (times.date == today) if hasattr(times, "date") else mask
        pm = intraday.loc[mask]
        if pm.empty:
            return fallback
        return float(pm["High"].max())
    except Exception:  # noqa: BLE001
        return fallback
