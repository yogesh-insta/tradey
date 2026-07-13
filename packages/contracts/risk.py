"""Shared risk contracts (Risk Manager → Order Executor)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SizedOrderIntent(BaseModel):
    """Approved, sized order intent. Executor maps to broker syntax."""

    intent_id: str = Field(default_factory=lambda: str(uuid4()))
    signal_id: str
    timestamp_utc: datetime = Field(default_factory=utc_now)
    symbol: str
    venue: str = "SMART"
    currency: str = "USD"
    side: str = "BUY"
    qty: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    risk_tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskRejection(BaseModel):
    """Why Risk Manager refused a NormalizedSignal."""

    rejection_id: str = Field(default_factory=lambda: str(uuid4()))
    signal_id: str
    timestamp_utc: datetime = Field(default_factory=utc_now)
    symbol: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
