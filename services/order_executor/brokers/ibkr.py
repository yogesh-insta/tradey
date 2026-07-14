"""IBKR adapter (ib_insync) — paper Gateway/TWS only via paper_guard.

Uses ib_insync's *sync* API (connect / sleep / qualifyContracts) so a single
IB session stays alive across portfolio() + execute() + manage calls. Do not
wrap each call in asyncio.run — that tears down the client event loop between ops.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from ib_insync import IB, LimitOrder, MarketOrder, Stock, StopOrder

from packages.contracts.execution import (
    ExecutionResult,
    PortfolioSnapshot,
    PositionSnapshot,
)
from packages.contracts.risk import SizedOrderIntent
from services.order_executor.brokers.base import BrokerAdapter, BrokerNotConnectedError
from services.order_executor.config import ExecutorConfig
from services.order_executor.paper_guard import assert_paper_safe

logger = logging.getLogger(__name__)


def _stock_contract(
    symbol: str,
    *,
    currency: Optional[str] = None,
    exchange: Optional[str] = None,
) -> Stock:
    """Build Stock from active market profile (US SMART/USD or ASX SMART/AUD)."""
    try:
        from services.market_profile import get_market_profile

        profile = get_market_profile()
        ccy = currency or profile.currency
        exch = exchange or profile.ibkr_exchange
        primary = profile.ibkr_primary_exchange
    except Exception:  # noqa: BLE001
        ccy = currency or "USD"
        exch = exchange or "SMART"
        primary = None
    if primary:
        return Stock(symbol, exch, ccy, primaryExchange=primary)
    return Stock(symbol, exch, ccy)


class IbkrAdapter(BrokerAdapter):
    name = "ibkr"

    def __init__(self, config: Optional[ExecutorConfig] = None) -> None:
        self.config = config or ExecutorConfig.from_env()
        self._ib = IB()
        self._account: Optional[str] = None

    def connect(self) -> None:
        assert_paper_safe()
        if self._ib.isConnected():
            return
        self._ib.connect(
            self.config.host,
            self.config.port,
            clientId=self.config.client_id,
        )
        accounts = self._ib.managedAccounts()
        self._account = accounts[0] if accounts else None
        # Delayed data is fine for paper smoke / protective stop placement.
        self._ib.reqMarketDataType(3)

    def disconnect(self) -> None:
        if self._ib.isConnected():
            self._ib.disconnect()

    def is_connected(self) -> bool:
        return bool(self._ib.isConnected())

    def portfolio(self) -> PortfolioSnapshot:
        if not self.is_connected():
            raise BrokerNotConnectedError("IBKR not connected")

        positions_raw = self._ib.positions()
        positions: list[PositionSnapshot] = []
        for pos in positions_raw:
            qty = float(pos.position or 0)
            if qty == 0:
                continue
            positions.append(
                PositionSnapshot(
                    symbol=pos.contract.symbol,
                    qty=qty,
                    avg_cost=float(pos.avgCost) if pos.avgCost else None,
                    account_id=pos.account,
                    currency=getattr(pos.contract, "currency", "USD") or "USD",
                    venue=getattr(pos.contract, "exchange", "SMART") or "SMART",
                )
            )

        # Sync path (same as run_local_process): accountSummary after connect.
        # Prefer accountValues which is already populated; fall back to summary tags.
        tags = {v.tag: v.value for v in self._ib.accountSummary()}
        if not tags:
            tags = {v.tag: v.value for v in self._ib.accountValues()}
        net = _float_or(tags.get("NetLiquidation"), 0.0) or 0.0
        day_pnl = (_float_or(tags.get("RealizedPnL"), 0.0) or 0.0) + (
            _float_or(tags.get("UnrealizedPnL"), 0.0) or 0.0
        )
        if "DailyPnL" in tags:
            day_pnl = _float_or(tags.get("DailyPnL"), day_pnl) or day_pnl

        return PortfolioSnapshot(
            account_id=self._account,
            net_liquidation_usd=net,
            buying_power_usd=_float_or(tags.get("BuyingPower"), None),
            day_pnl_usd=day_pnl,
            open_position_count=len(positions),
            positions=positions,
            connected=True,
            metadata={"tags_sample": {k: tags[k] for k in list(tags)[:12]}},
        )

    def execute(self, intent: SizedOrderIntent) -> ExecutionResult:
        if not self.is_connected():
            raise BrokerNotConnectedError("IBKR not connected")

        if intent.side.upper() != "BUY":
            return ExecutionResult(
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                ok=False,
                status="rejected",
                message=f"IbkrAdapter v1 long-only; got side={intent.side}",
            )
        if intent.qty < 1:
            return ExecutionResult(
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                ok=False,
                status="rejected",
                message="qty < 1",
            )

        contract = _stock_contract(
            intent.symbol,
            currency=intent.currency,
            exchange=intent.venue,
        )
        self._ib.qualifyContracts(contract)

        px = intent.limit_price
        if px is None:
            px = self._last_price(contract)
        if px is None or px <= 0:
            return ExecutionResult(
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                ok=False,
                status="error",
                message="could not resolve entry price",
            )

        limit_px = round(float(px) * (1.0 + self.config.limit_slippage_pct / 100.0), 2)
        order = LimitOrder("BUY", int(intent.qty), limit_px)
        order.tif = "DAY"
        order.outsideRth = False
        trade = self._ib.placeOrder(contract, order)

        filled = self._wait_fill(trade, self.config.fill_timeout_sec)
        status = trade.orderStatus.status
        avg = float(trade.orderStatus.avgFillPrice or 0) or None
        order_id = str(trade.order.orderId)

        if filled <= 0:
            msg = trade.log[-1].message if trade.log else status
            return ExecutionResult(
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                ok=False,
                status=status,
                filled_qty=0.0,
                avg_fill_price=avg,
                order_id=order_id,
                message=msg,
            )

        stop_order_id: Optional[str] = None
        if intent.stop_price and intent.stop_price > 0:
            stop = StopOrder("SELL", int(filled), float(intent.stop_price))
            stop.tif = "GTC"
            stop_trade = self._ib.placeOrder(contract, stop)
            stop_order_id = str(stop_trade.order.orderId)

        return ExecutionResult(
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            ok=True,
            status=status,
            filled_qty=float(filled),
            avg_fill_price=avg,
            order_id=order_id,
            stop_order_id=stop_order_id,
            message="entry filled" + (" + stop placed" if stop_order_id else ""),
            metadata={"limit_px": limit_px, "account": self._account},
        )

    def cancel_order(self, order_id: str) -> None:
        if not self.is_connected():
            raise BrokerNotConnectedError("IBKR not connected")
        assert_paper_safe()
        trade = self._find_trade(order_id)
        if trade is None:
            logger.warning("cancel_order: order_id=%s not found", order_id)
            return
        self._ib.cancelOrder(trade.order)
        self._ib.sleep(0.5)

    def modify_stop(
        self,
        symbol: str,
        stop_order_id: str,
        new_stop: float,
        qty: int,
    ) -> ExecutionResult:
        """Raise/replace protective stop. Prefer cancel+new StopOrder on failure."""
        if not self.is_connected():
            raise BrokerNotConnectedError("IBKR not connected")
        assert_paper_safe()
        intent_id = f"modify_stop:{symbol}:{uuid4()}"
        contract = _stock_contract(symbol)
        self._ib.qualifyContracts(contract)

        trade = self._find_trade(stop_order_id)
        if trade is not None and trade.order.orderType.upper() in {"STP", "STOP"}:
            try:
                trade.order.auxPrice = float(new_stop)
                trade.order.totalQuantity = int(qty)
                # ib_insync #647: clear parentId when modify fails to find parent
                if getattr(trade.order, "parentId", None):
                    trade.order.parentId = 0
                self._ib.placeOrder(contract, trade.order)
                self._ib.sleep(0.5)
                return ExecutionResult(
                    intent_id=intent_id,
                    symbol=symbol,
                    ok=True,
                    status="Submitted",
                    order_id=str(trade.order.orderId),
                    stop_order_id=str(trade.order.orderId),
                    message=f"stop modified auxPrice={new_stop} qty={qty}",
                    metadata={"action_kind": "stop_modify", "method": "modify"},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("modify_stop in-place failed (%s); replacing", exc)

        # Safest Phase A: cancel old → new StopOrder for remaining qty
        try:
            self.cancel_order(stop_order_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel old stop failed: %s", exc)

        stop = StopOrder("SELL", int(qty), float(new_stop))
        stop.tif = "GTC"
        stop_trade = self._ib.placeOrder(contract, stop)
        new_id = str(stop_trade.order.orderId)
        return ExecutionResult(
            intent_id=intent_id,
            symbol=symbol,
            ok=True,
            status="Submitted",
            order_id=new_id,
            stop_order_id=new_id,
            message=f"stop replaced auxPrice={new_stop} qty={qty}",
            metadata={"action_kind": "stop_modify", "method": "replace"},
        )

    def market_sell(self, symbol: str, qty: int) -> ExecutionResult:
        if not self.is_connected():
            raise BrokerNotConnectedError("IBKR not connected")
        assert_paper_safe()
        intent_id = f"market_sell:{symbol}:{uuid4()}"
        if qty < 1:
            return ExecutionResult(
                intent_id=intent_id,
                symbol=symbol,
                ok=False,
                status="rejected",
                message="qty < 1",
            )
        contract = _stock_contract(symbol)
        self._ib.qualifyContracts(contract)
        order = MarketOrder("SELL", int(qty))
        order.tif = "DAY"
        trade = self._ib.placeOrder(contract, order)
        filled = self._wait_fill(trade, self.config.fill_timeout_sec)
        avg = float(trade.orderStatus.avgFillPrice or 0) or None
        return ExecutionResult(
            intent_id=intent_id,
            symbol=symbol,
            ok=filled > 0,
            status=trade.orderStatus.status,
            filled_qty=float(filled),
            avg_fill_price=avg,
            order_id=str(trade.order.orderId),
            message="market sell",
            metadata={"action_kind": "market_sell"},
        )

    def flatten_symbol(self, symbol: str) -> ExecutionResult:
        """Cancel open stops for symbol, then market-sell remaining position."""
        if not self.is_connected():
            raise BrokerNotConnectedError("IBKR not connected")
        assert_paper_safe()
        intent_id = f"flatten:{symbol}:{uuid4()}"

        for trade in self.open_orders_for(symbol):
            try:
                self._ib.cancelOrder(trade.order)
            except Exception as exc:  # noqa: BLE001
                logger.warning("flatten cancel failed: %s", exc)
        self._ib.sleep(0.5)

        qty = 0.0
        for pos in self._ib.positions():
            if pos.contract.symbol == symbol:
                qty = abs(float(pos.position or 0))
                break
        if qty < 1:
            return ExecutionResult(
                intent_id=intent_id,
                symbol=symbol,
                ok=True,
                status="flat",
                filled_qty=0.0,
                message="already flat",
                metadata={"action_kind": "flatten"},
            )

        result = self.market_sell(symbol, int(qty))
        result.intent_id = intent_id
        result.metadata = {**(result.metadata or {}), "action_kind": "flatten"}
        return result

    def open_orders_for(self, symbol: str) -> list[Any]:
        if not self.is_connected():
            return []
        out = []
        for trade in self._ib.openTrades():
            c = trade.contract
            if getattr(c, "symbol", None) == symbol:
                out.append(trade)
        return out

    def recent_fill_order_ids(self, *, hours: float = 1.0) -> list[str]:
        if not self.is_connected():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        ids: list[str] = []
        for fill in self._ib.fills():
            ts = getattr(fill.execution, "time", None)
            if ts is not None:
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            oid = getattr(fill.execution, "orderId", None)
            if oid is not None:
                ids.append(str(oid))
        return ids

    def _find_trade(self, order_id: str) -> Any:
        target = str(order_id)
        for trade in self._ib.trades():
            if str(trade.order.orderId) == target:
                return trade
        for trade in self._ib.openTrades():
            if str(trade.order.orderId) == target:
                return trade
        return None

    def _last_price(self, contract: Stock) -> Optional[float]:
        ticker = self._ib.reqMktData(contract, "", False, False)
        try:
            for _ in range(40):
                self._ib.sleep(0.25)
                for candidate in (
                    ticker.marketPrice(),
                    ticker.last,
                    ticker.close,
                    ticker.bid,
                    ticker.ask,
                ):
                    if candidate and candidate == candidate and float(candidate) > 0:
                        return float(candidate)
        finally:
            self._ib.cancelMktData(contract)
        return None

    def _wait_fill(self, trade, timeout_sec: float) -> float:
        steps = max(1, int(timeout_sec / 0.5))
        for _ in range(steps):
            self._ib.sleep(0.5)
            if trade.isDone():
                break
        return float(trade.orderStatus.filled or 0)


def _float_or(value: Optional[str], default: Optional[float]) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
