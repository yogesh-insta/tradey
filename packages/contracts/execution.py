"""Shared execution / portfolio contracts (Executor ↔ Risk / ledger)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PositionSnapshot(BaseModel):
    symbol: str
    qty: float
    avg_cost: Optional[float] = None
    account_id: Optional[str] = None
    currency: str = "USD"
    venue: str = "SMART"


class PortfolioSnapshot(BaseModel):
    """Broker-neutral portfolio view for Risk Manager."""

    account_id: Optional[str] = None
    net_liquidation_usd: float = 0.0
    buying_power_usd: Optional[float] = None
    day_pnl_usd: float = 0.0
    open_position_count: int = 0
    positions: list[PositionSnapshot] = Field(default_factory=list)
    connected: bool = False
    as_of_utc: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    """Outcome of attempting to execute a SizedOrderIntent."""

    result_id: str = Field(default_factory=lambda: str(uuid4()))
    intent_id: str
    symbol: str
    ok: bool
    status: str
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    order_id: Optional[str] = None
    stop_order_id: Optional[str] = None
    message: Optional[str] = None
    timestamp_utc: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
