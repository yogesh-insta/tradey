"""Position-manager contracts (exit ladder decisions ↔ Executor)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PositionLifecycle(str, Enum):
    pre_breakeven = "pre_breakeven"
    post_breakeven_partial_done = "post_breakeven_partial_done"
    post_breakeven_no_partial = "post_breakeven_no_partial"


class ExitActionKind(str, Enum):
    noop = "noop"
    partial = "partial"
    modify_stop = "modify_stop"
    flatten = "flatten"
    clear = "clear"


class ExitReason(str, Enum):
    """Closed-trade exit attribution (plan 08)."""

    stop = "stop"
    trail = "trail"
    force_close = "force_close"
    external = "external"
    partial = "partial"  # leg-level; closed trade prefers nested legs
    breakeven_then_stop = "breakeven_then_stop"


class ManagedPosition(BaseModel):
    """Strategy metadata for one open long; broker qty is source of truth."""

    trade_id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    qty_remaining: int
    entry_price: float
    initial_stop: float
    R: float
    current_stop: float
    stop_order_id: Optional[str] = None
    entry_order_id: Optional[str] = None
    signal_id: Optional[str] = None
    intent_id: Optional[str] = None
    state: PositionLifecycle = PositionLifecycle.pre_breakeven
    partial_done: bool = False
    breakeven_done: bool = False
    trail_updates_count: int = 0
    opened_at_et: Optional[str] = None
    account_id: Optional[str] = None
    rules_version: Optional[str] = None
    session_code_at_entry: Optional[str] = None
    filters_fired: dict[str, Any] = Field(default_factory=dict)
    signal_reasons: list[str] = Field(default_factory=list)
    signal_metrics: dict[str, Any] = Field(default_factory=dict)
    risk_usd: Optional[float] = None
    sizing_portfolio_usd: Optional[float] = None
    qty_initial: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExitAction(BaseModel):
    """Pure decision from Position Manager — Executor executes or dry-runs."""

    action_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: ExitActionKind
    symbol: str
    trade_id: str
    qty: Optional[int] = None
    new_stop: Optional[float] = None
    stop_order_id: Optional[str] = None
    exit_reason: Optional[ExitReason] = None
    event_channel: str = "pm_noop"  # pm_partial | pm_breakeven | pm_trail | …
    state_before: Optional[PositionLifecycle] = None
    state_after: Optional[PositionLifecycle] = None
    mark: Optional[float] = None
    R_progress: Optional[float] = None
    message: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp_utc: datetime = Field(default_factory=utc_now)
