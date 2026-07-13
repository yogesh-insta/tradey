"""Shared signal contracts (Signal Generator → Risk Manager)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NormalizedSignal(BaseModel):
    """Broker-neutral trade idea. No sizing, no IBKR calls."""

    signal_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp_utc: datetime = Field(default_factory=utc_now)
    source: str = "custom_model"
    strategy_name: str = "Trend Join Long"
    symbol: str
    venue: str = "SMART"
    currency: str = "USD"
    action: str = "BUY"  # BUY | SELL | FLAT
    timeframe: str = "5m"
    price: Optional[float] = None
    reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FilterEvaluation(BaseModel):
    """Result of D1–D3 + I1–I3 against one symbol."""

    symbol: str
    passed: bool
    price: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
