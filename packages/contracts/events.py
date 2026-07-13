from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TradeEvent(BaseModel):
    """Broker-neutral ledger event (ATO / dashboard)."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp_utc: datetime = Field(default_factory=utc_now)
    event_type: str  # connected | order_submitted | fill | reject | error | info
    broker: str = "ibkr"
    account_id: Optional[str] = None
    symbol: Optional[str] = None
    venue: Optional[str] = None
    currency: Optional[str] = "USD"
    side: Optional[str] = None
    qty: Optional[float] = None
    price: Optional[float] = None
    fees: Optional[float] = None
    status: Optional[str] = None
    message: Optional[str] = None
    raw_ref: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
