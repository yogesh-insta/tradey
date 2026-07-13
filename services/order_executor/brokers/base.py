"""BrokerAdapter interface — Risk/Signal never import broker SDKs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from packages.contracts.execution import ExecutionResult, PortfolioSnapshot
from packages.contracts.risk import SizedOrderIntent


class BrokerAdapter(ABC):
    """Minimal broker plugin surface for Phase A paper trading."""

    name: str

    @abstractmethod
    def connect(self) -> None:
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def portfolio(self) -> PortfolioSnapshot:
        ...

    @abstractmethod
    def execute(self, intent: SizedOrderIntent) -> ExecutionResult:
        """Place entry (+ protective stop when stop_price set). Blocking."""
        ...

    # --- Position manage (plan 07). Default: unsupported. ---

    def cancel_order(self, order_id: str) -> None:
        raise BrokerUnsupportedError(f"{self.name}: cancel_order not implemented")

    def modify_stop(
        self,
        symbol: str,
        stop_order_id: str,
        new_stop: float,
        qty: int,
    ) -> ExecutionResult:
        raise BrokerUnsupportedError(f"{self.name}: modify_stop not implemented")

    def market_sell(self, symbol: str, qty: int) -> ExecutionResult:
        raise BrokerUnsupportedError(f"{self.name}: market_sell not implemented")

    def flatten_symbol(self, symbol: str) -> ExecutionResult:
        raise BrokerUnsupportedError(f"{self.name}: flatten_symbol not implemented")

    def open_orders_for(self, symbol: str) -> list[Any]:
        return []

    def recent_fill_order_ids(self, *, hours: float = 1.0) -> list[str]:
        """Order ids that filled recently (for stop-out matching)."""
        return []


class BrokerNotConnectedError(RuntimeError):
    """Raised when an operation requires a live broker session."""


class BrokerUnsupportedError(RuntimeError):
    """Raised for stub / unimplemented brokers."""
