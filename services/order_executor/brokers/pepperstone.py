"""Pepperstone adapter stub — interface only until a later phase."""

from __future__ import annotations

from packages.contracts.execution import ExecutionResult, PortfolioSnapshot
from packages.contracts.risk import SizedOrderIntent
from services.order_executor.brokers.base import BrokerAdapter, BrokerUnsupportedError


class PepperstoneAdapter(BrokerAdapter):
    name = "pepperstone"

    def connect(self) -> None:
        raise BrokerUnsupportedError("PepperstoneAdapter is a stub")

    def disconnect(self) -> None:
        return

    def is_connected(self) -> bool:
        return False

    def portfolio(self) -> PortfolioSnapshot:
        raise BrokerUnsupportedError("PepperstoneAdapter is a stub")

    def execute(self, intent: SizedOrderIntent) -> ExecutionResult:
        raise BrokerUnsupportedError("PepperstoneAdapter is a stub")
