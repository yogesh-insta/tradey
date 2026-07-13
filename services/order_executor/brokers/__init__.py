"""Broker factory."""

from __future__ import annotations

from services.order_executor.brokers.base import BrokerAdapter, BrokerUnsupportedError
from services.order_executor.brokers.ibkr import IbkrAdapter
from services.order_executor.brokers.pepperstone import PepperstoneAdapter
from services.order_executor.config import ExecutorConfig


def get_adapter(config: ExecutorConfig | None = None) -> BrokerAdapter:
    cfg = config or ExecutorConfig.from_env()
    if cfg.broker == "ibkr":
        return IbkrAdapter(cfg)
    if cfg.broker in {"pepperstone", "pepper"}:
        return PepperstoneAdapter()
    raise BrokerUnsupportedError(f"Unknown BROKER={cfg.broker!r}")


__all__ = [
    "BrokerAdapter",
    "IbkrAdapter",
    "PepperstoneAdapter",
    "get_adapter",
]
