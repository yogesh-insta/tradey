"""Risk Manager — sizing, concurrency, session/time gates, daily-loss kill. No IBKR."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional, Union
from uuid import uuid4
from zoneinfo import ZoneInfo

from packages.contracts.risk import RiskRejection, SizedOrderIntent
from packages.contracts.signals import NormalizedSignal
from services.signal_generator.rules import (
    RiskRules,
    StrategyRules,
    TimeFilter,
    load_rules,
    rules_version_stamp,
)

ET = ZoneInfo("America/New_York")
TzLike = Union[ZoneInfo, str]


@dataclass(frozen=True)
class SessionStatus:
    """Entry window classification in the active market timezone."""

    code: str  # weekend | too_early | closed | manage_only | force_close | ok
    allow_new_entries: bool


def parse_hhmm(value: str) -> time:
    hour, minute = value.strip().split(":")
    return time(int(hour), int(minute))


def _resolve_tz(tz: Optional[TzLike] = None) -> ZoneInfo:
    if tz is None:
        try:
            from services.market_profile import get_market_profile

            return get_market_profile().tz
        except Exception:  # noqa: BLE001
            return ET
    if isinstance(tz, ZoneInfo):
        return tz
    return ZoneInfo(str(tz))


def classify_session(
    now: Optional[datetime] = None,
    *,
    time_filter: Optional[TimeFilter] = None,
    tz: Optional[TzLike] = None,
    market_open_hhmm: Optional[str] = None,
    market_close_hhmm: Optional[str] = None,
) -> SessionStatus:
    """Map local market clock to session phase codes (US ET or Sydney).

    `time_filter` HH:MM fields are local to `tz` (names keep `_et` suffix for
    schema continuity — ASX rules store Sydney times in the same keys).
    """
    tf = time_filter or load_rules().time_filter
    zone = _resolve_tz(tz)
    if market_open_hhmm is None or market_close_hhmm is None:
        try:
            from services.market_profile import get_market_profile

            profile = get_market_profile()
            market_open_hhmm = market_open_hhmm or profile.market_open_hhmm
            market_close_hhmm = market_close_hhmm or profile.market_close_hhmm
        except Exception:  # noqa: BLE001
            market_open_hhmm = market_open_hhmm or "10:00"
            market_close_hhmm = market_close_hhmm or "16:00"

    now_local = now.astimezone(zone) if now else datetime.now(zone)
    if now_local.weekday() >= 5:
        return SessionStatus("weekend", False)

    t = now_local.time()
    earliest = parse_hhmm(tf.earliest_entry_et)
    latest = parse_hhmm(tf.latest_entry_et)
    force_close = parse_hhmm(tf.force_close_et)
    market_open = parse_hhmm(market_open_hhmm)
    market_close = parse_hhmm(market_close_hhmm)

    if t < market_open:
        return SessionStatus("too_early", False)
    if t >= market_close:
        return SessionStatus("closed", False)
    if force_close <= t < market_close:
        return SessionStatus("force_close", False)
    if market_open <= t < earliest or latest <= t < force_close:
        return SessionStatus("manage_only", False)
    if earliest <= t < latest:
        return SessionStatus("ok", True)
    return SessionStatus("closed", False)


def size_long(
    price: float,
    stop_price: float,
    *,
    portfolio_value_usd: float,
    risk: RiskRules,
) -> int:
    """Risk-% and max-position-pct sizing. Returns 0 if too small."""
    if price <= 0 or stop_price <= 0 or stop_price >= price:
        return 0
    risk_per_share = price - stop_price
    risk_dollars = portfolio_value_usd * (risk.max_risk_per_trade_pct / 100.0)
    by_risk = int(risk_dollars // risk_per_share)
    max_notional = portfolio_value_usd * (risk.max_position_size_pct_of_portfolio / 100.0)
    by_cap = int(max_notional // price)
    return max(0, min(by_risk, by_cap))


def initial_stop_from_lod(low_of_day: float) -> float:
    """Blog exit rule lod_minus_1pct."""
    return low_of_day * 0.99


def daily_loss_breached(
    *,
    portfolio_value_usd: float,
    day_pnl_usd: float,
    max_daily_loss_pct: float,
) -> bool:
    """True when day PnL is at or below -max_daily_loss_pct of portfolio."""
    if portfolio_value_usd <= 0 or max_daily_loss_pct <= 0:
        return False
    threshold = -abs(portfolio_value_usd * (max_daily_loss_pct / 100.0))
    return day_pnl_usd <= threshold


def evaluate_signal(
    signal: NormalizedSignal,
    *,
    open_position_count: int,
    portfolio_value_usd: float,
    low_of_day: Optional[float] = None,
    day_pnl_usd: float = 0.0,
    rules: Optional[StrategyRules] = None,
    now: Optional[datetime] = None,
    long_only: bool = True,
) -> SizedOrderIntent | RiskRejection:
    """Apply time gate, daily-loss kill, concurrency, long-only, and size."""
    rules = rules or load_rules()
    session = classify_session(now, time_filter=rules.time_filter)
    # Session TZ / open-close come from active MarketProfile inside classify_session.
    if not session.allow_new_entries:
        return RiskRejection(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            reason=f"session gate: {session.code}",
            details={"session": session.code},
        )

    if daily_loss_breached(
        portfolio_value_usd=portfolio_value_usd,
        day_pnl_usd=day_pnl_usd,
        max_daily_loss_pct=rules.risk.max_daily_loss_pct,
    ):
        return RiskRejection(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            reason="daily_loss_kill_switch",
            details={
                "day_pnl_usd": day_pnl_usd,
                "portfolio_value_usd": portfolio_value_usd,
                "max_daily_loss_pct": rules.risk.max_daily_loss_pct,
            },
        )

    if long_only and signal.action.upper() != "BUY":
        return RiskRejection(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            reason="long_only: non-BUY rejected",
        )

    if open_position_count >= rules.risk.max_concurrent_positions:
        return RiskRejection(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            reason="max_concurrent_positions reached",
            details={
                "open": open_position_count,
                "max": rules.risk.max_concurrent_positions,
            },
        )

    price = float(signal.price or 0.0)
    if price <= 0:
        return RiskRejection(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            reason="missing or invalid signal price",
        )

    lod = float(low_of_day) if low_of_day is not None else price * 0.98
    stop = initial_stop_from_lod(lod)
    qty = size_long(
        price,
        stop,
        portfolio_value_usd=portfolio_value_usd,
        risk=rules.risk,
    )
    if qty < 1:
        return RiskRejection(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            reason="position size < 1 after risk caps",
            details={"price": price, "stop": stop},
        )

    trade_id = str(uuid4())
    R = round(price - stop, 4)
    risk_usd = round(portfolio_value_usd * (rules.risk.max_risk_per_trade_pct / 100.0), 4)
    filters_fired = (signal.metadata or {}).get("filters_fired") or {}
    metrics = (signal.metadata or {}).get("metrics") or {}
    return SizedOrderIntent(
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        venue=signal.venue,
        currency=signal.currency,
        side="BUY",
        qty=qty,
        stop_price=round(stop, 4),
        risk_tags=["trend_join_long", f"session:{session.code}"],
        metadata={
            "trade_id": trade_id,
            "rules_version": rules_version_stamp(rules),
            "session_code": session.code,
            "strategy": rules.strategy_name,
            "R": R,
            "risk_usd": risk_usd,
            "sizing_portfolio_usd": portfolio_value_usd,
            "max_risk_pct_used": rules.risk.max_risk_per_trade_pct,
            "day_pnl_usd": day_pnl_usd,
            "signal_reasons": list(signal.reasons or []),
            "filters_fired": filters_fired,
            "signal_metrics": metrics,
            "action_kind": "entry",
        },
    )
