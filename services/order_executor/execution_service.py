"""Execution service — SizedOrderIntent → broker → ledger + notify.

Also applies Position Manager ExitActions (partial / stop modify / flatten).
"""

from __future__ import annotations

import logging
from typing import Optional

from packages.contracts.events import TradeEvent
from packages.contracts.execution import ExecutionResult, PortfolioSnapshot
from packages.contracts.position import ExitAction, ExitActionKind
from packages.contracts.risk import SizedOrderIntent
from packages.ledger import Ledger
from packages.notify import notify
from services.order_executor.brokers import get_adapter
from services.order_executor.brokers.base import BrokerAdapter, BrokerNotConnectedError
from services.order_executor.config import ExecutorConfig
from services.order_executor.paper_guard import check_paper_guard

logger = logging.getLogger(__name__)


class ExecutionService:
    """Owns adapter lifecycle for a single execute/portfolio session."""

    def __init__(
        self,
        *,
        config: Optional[ExecutorConfig] = None,
        adapter: Optional[BrokerAdapter] = None,
        ledger: Optional[Ledger] = None,
    ) -> None:
        self.config = config or ExecutorConfig.from_env()
        self.adapter = adapter or get_adapter(self.config)
        self.ledger = ledger or Ledger()
        self._owns_connection = False

    def __enter__(self) -> "ExecutionService":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        guard = check_paper_guard(
            port=self.config.port,
            paper_trading=self.config.paper_trading,
        )
        if not guard.ok:
            raise SystemExit(guard.message)
        if not self.adapter.is_connected():
            self.adapter.connect()
            self._owns_connection = True
            snap = self.safe_portfolio()
            self.ledger.append(
                TradeEvent(
                    event_type="connected",
                    account_id=snap.account_id if snap else None,
                    message=f"executor connected broker={self.adapter.name}",
                    metadata={"port": self.config.port, "client_id": self.config.client_id},
                )
            )

    def close(self) -> None:
        if self._owns_connection:
            try:
                self.adapter.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("disconnect error: %s", exc)
            self._owns_connection = False

    def safe_portfolio(self) -> Optional[PortfolioSnapshot]:
        try:
            return self.adapter.portfolio()
        except BrokerNotConnectedError:
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("portfolio snapshot failed: %s", exc)
            return None

    def execute_intent(self, intent: SizedOrderIntent) -> ExecutionResult:
        """Execute one approved intent; always ledger + notify; never raises fill misses."""
        meta = dict(intent.metadata or {})
        self.ledger.append(
            TradeEvent(
                event_type="order_submitted",
                symbol=intent.symbol,
                venue=intent.venue,
                currency=intent.currency,
                side=intent.side,
                qty=float(intent.qty),
                price=intent.limit_price,
                message=f"intent {intent.intent_id}",
                metadata={
                    "intent_id": intent.intent_id,
                    "signal_id": intent.signal_id,
                    "trade_id": meta.get("trade_id"),
                    "rules_version": meta.get("rules_version"),
                    "session_code": meta.get("session_code"),
                    "stop_price": intent.stop_price,
                    "action_kind": "entry",
                    **{k: meta[k] for k in ("R", "risk_usd", "sizing_portfolio_usd") if k in meta},
                },
            )
        )
        notify(
            "Order submitted",
            f"{intent.side} {intent.qty} {intent.symbol} stop={intent.stop_price}",
        )

        try:
            result = self.adapter.execute(intent)
        except Exception as exc:  # noqa: BLE001
            logger.exception("execute failed")
            result = ExecutionResult(
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                ok=False,
                status="error",
                message=str(exc),
            )

        if result.ok and result.filled_qty > 0:
            self.ledger.append(
                TradeEvent(
                    event_type="fill",
                    symbol=result.symbol,
                    side=intent.side,
                    qty=result.filled_qty,
                    price=result.avg_fill_price,
                    status=result.status,
                    raw_ref=result.order_id,
                    message=result.message,
                    metadata={
                        "intent_id": intent.intent_id,
                        "signal_id": intent.signal_id,
                        "trade_id": meta.get("trade_id"),
                        "rules_version": meta.get("rules_version"),
                        "stop_order_id": result.stop_order_id,
                        "action_kind": "entry",
                    },
                )
            )
            notify(
                "Fill",
                f"{intent.side} {result.filled_qty:g} {result.symbol} "
                f"@ {result.avg_fill_price} stop_id={result.stop_order_id}",
            )
        else:
            self.ledger.append(
                TradeEvent(
                    event_type="error",
                    symbol=result.symbol,
                    side=intent.side,
                    qty=float(intent.qty),
                    status=result.status,
                    raw_ref=result.order_id,
                    message=result.message or "execution failed",
                    metadata={
                        "intent_id": intent.intent_id,
                        "trade_id": meta.get("trade_id"),
                        "rules_version": meta.get("rules_version"),
                    },
                )
            )
            notify(
                "Order failed",
                f"{result.symbol}: {result.status} {result.message}",
                "high",
            )
        return result

    def apply_exit_action(
        self,
        action: ExitAction,
        *,
        dry_run: bool = True,
    ) -> ExecutionResult:
        """Execute or dry-run a Position Manager decision; ledger + notify never raise."""
        result = ExecutionResult(
            intent_id=action.action_id,
            symbol=action.symbol,
            ok=True,
            status="dry_run" if dry_run else "pending",
            message=action.message,
            metadata={
                "trade_id": action.trade_id,
                "kind": action.kind.value,
                "event_channel": action.event_channel,
                "dry_run": dry_run,
                "exit_reason": action.exit_reason.value if action.exit_reason else None,
            },
        )

        self.ledger.append(
            TradeEvent(
                event_type="info",
                symbol=action.symbol,
                message=action.event_channel,
                qty=float(action.qty) if action.qty else None,
                price=action.new_stop,
                metadata={
                    "channel": action.event_channel,
                    "trade_id": action.trade_id,
                    "kind": action.kind.value,
                    "state_before": action.state_before.value if action.state_before else None,
                    "state_after": action.state_after.value if action.state_after else None,
                    "mark": action.mark,
                    "R_progress": action.R_progress,
                    "exit_reason": action.exit_reason.value if action.exit_reason else None,
                    "stop_old": (action.metadata or {}).get("stop_old"),
                    "stop_new": action.new_stop,
                    "qty": action.qty,
                    "dry_run": dry_run,
                    **(action.metadata or {}),
                },
            )
        )

        if action.kind == ExitActionKind.noop or action.kind == ExitActionKind.clear:
            # clear is state-only; broker already flat / stop filled
            if dry_run or action.kind == ExitActionKind.clear:
                notify(
                    action.event_channel,
                    action.message or action.symbol,
                    "default",
                )
                result.status = "ok" if not dry_run else "dry_run"
                return result

        if dry_run:
            notify(
                f"[dry-run] {action.event_channel}",
                action.message or f"{action.kind.value} {action.symbol}",
            )
            return result

        try:
            if action.kind == ExitActionKind.partial and action.qty:
                sell = self.adapter.market_sell(action.symbol, int(action.qty))
                result = sell
                qty_after = int(
                    (action.metadata or {}).get("qty_after")
                    or max(1, int(action.qty))  # fallback
                )
                # Prefer remaining = broker-reported when sell ok; else metadata
                if action.new_stop is not None and action.stop_order_id:
                    mod = self.adapter.modify_stop(
                        action.symbol,
                        action.stop_order_id,
                        float(action.new_stop),
                        max(1, qty_after),
                    )
                    result.stop_order_id = mod.stop_order_id or mod.order_id
                    result.metadata = {
                        **(result.metadata or {}),
                        "action_kind": "partial",
                        "trade_id": action.trade_id,
                        "new_stop": action.new_stop,
                    }
            elif action.kind == ExitActionKind.modify_stop and action.new_stop is not None:
                if not action.stop_order_id:
                    result.ok = False
                    result.status = "error"
                    result.message = "modify_stop missing stop_order_id"
                else:
                    result = self.adapter.modify_stop(
                        action.symbol,
                        action.stop_order_id,
                        float(action.new_stop),
                        int(action.qty or 1),
                    )
                    result.metadata = {
                        **(result.metadata or {}),
                        "action_kind": "stop_modify",
                        "trade_id": action.trade_id,
                        "event_channel": action.event_channel,
                    }
            elif action.kind == ExitActionKind.flatten:
                result = self.adapter.flatten_symbol(action.symbol)
                result.metadata = {
                    **(result.metadata or {}),
                    "action_kind": "flatten",
                    "trade_id": action.trade_id,
                    "exit_reason": "force_close",
                }
            else:
                result.ok = False
                result.status = "rejected"
                result.message = f"unsupported manage kind={action.kind}"
        except Exception as exc:  # noqa: BLE001
            logger.exception("apply_exit_action failed")
            result = ExecutionResult(
                intent_id=action.action_id,
                symbol=action.symbol,
                ok=False,
                status="error",
                message=str(exc),
                metadata={"trade_id": action.trade_id, "kind": action.kind.value},
            )

        if result.ok and result.filled_qty and result.filled_qty > 0:
            self.ledger.append(
                TradeEvent(
                    event_type="fill",
                    symbol=result.symbol,
                    side="SELL",
                    qty=result.filled_qty,
                    price=result.avg_fill_price,
                    status=result.status,
                    raw_ref=result.order_id,
                    message=result.message,
                    metadata={
                        "trade_id": action.trade_id,
                        "stop_order_id": result.stop_order_id,
                        "action_kind": (result.metadata or {}).get("action_kind"),
                        "exit_reason": action.exit_reason.value if action.exit_reason else None,
                    },
                )
            )

        priority = "high" if action.kind == ExitActionKind.flatten else "default"
        notify(
            action.event_channel,
            result.message or action.message or action.symbol,
            priority,
        )
        return result
