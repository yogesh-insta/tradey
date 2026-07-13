"""Manage-tick helpers: stop-out reconcile, exit ladder, force-close."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from packages.contracts.events import TradeEvent
from packages.contracts.execution import PortfolioSnapshot
from packages.contracts.position import ExitAction, ExitActionKind, ManagedPosition
from packages.ledger import Ledger
from packages.observability.closed_trades import record_closed_trade
from services.order_executor.execution_service import ExecutionService
from services.position_manager.policy import (
    apply_action_to_position,
    decide_manage,
    detect_stop_out,
    force_close_action,
    reconcile_broker_qty,
    stop_out_action,
)
from services.position_manager.state import PositionStore
from services.position_manager.swing import trail_stop_from_swing
from services.signal_generator.bars import fetch_chart, ibkr_to_yahoo
from services.signal_generator.rules import ExitRules, StrategyRules

logger = logging.getLogger(__name__)

MarkFn = Callable[[str], Optional[float]]
SwingFn = Callable[[str], Optional[float]]


def yahoo_mark(symbol: str) -> Optional[float]:
    """Best-effort last close from Yahoo 5m bars (works without Gateway)."""
    try:
        frame = fetch_chart(ibkr_to_yahoo(symbol), period="1d", interval="5m")
        if frame is None or frame.empty or "Close" not in frame.columns:
            return None
        val = float(frame["Close"].iloc[-1])
        return val if val > 0 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("yahoo_mark(%s) failed: %s", symbol, exc)
        return None


def yahoo_swing_stop(symbol: str) -> Optional[float]:
    try:
        frame = fetch_chart(ibkr_to_yahoo(symbol), period="5d", interval="5m")
        if frame is None or frame.empty or "Low" not in frame.columns:
            return None
        lows = [float(x) for x in frame["Low"].dropna().tolist()]
        return trail_stop_from_swing(lows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("yahoo_swing_stop(%s) failed: %s", symbol, exc)
        return None


def _broker_qty(snap: Optional[PortfolioSnapshot], symbol: str) -> Optional[float]:
    if snap is None:
        return None
    for pos in snap.positions:
        if pos.symbol == symbol:
            return float(pos.qty)
    return 0.0


def run_manage_tick(
    *,
    rules: StrategyRules,
    store: PositionStore,
    dry_run: bool,
    force_close: bool = False,
    snap: Optional[PortfolioSnapshot] = None,
    exec_svc: Optional[ExecutionService] = None,
    ledger: Optional[Ledger] = None,
    recent_fill_order_ids: Optional[list[str]] = None,
    mark_fn: Optional[MarkFn] = None,
    swing_fn: Optional[SwingFn] = None,
) -> list[dict[str, Any]]:
    """Reconcile stop-outs → force flatten or manage ladder. Persist state."""
    mark_fn = mark_fn or yahoo_mark
    swing_fn = swing_fn or yahoo_swing_stop
    exit_rules: ExitRules = rules.exit
    actions_out: list[dict[str, Any]] = []
    positions = store.load()
    fill_ids = list(recent_fill_order_ids or [])

    remaining: list[ManagedPosition] = []

    for pos in positions:
        # 1) Stop-out by stop_order_id
        if detect_stop_out(pos, fill_ids):
            action = stop_out_action(
                pos, trail_after_be=pos.trail_updates_count > 0
            )
            _dispatch(action, dry_run=dry_run, exec_svc=exec_svc, ledger=ledger)
            try:
                record_closed_trade(pos, action)
            except Exception as exc:  # noqa: BLE001
                logger.warning("record_closed_trade failed: %s", exc)
            actions_out.append(action.model_dump(mode="json"))
            continue

        # Broker flat while state open → external
        bq = _broker_qty(snap, pos.symbol)
        if bq is not None and bq <= 0:
            action = reconcile_broker_qty(pos, 0.0)
            assert action is not None
            _dispatch(action, dry_run=dry_run, exec_svc=exec_svc, ledger=ledger)
            try:
                record_closed_trade(pos, action)
            except Exception as exc:  # noqa: BLE001
                logger.warning("record_closed_trade failed: %s", exc)
            actions_out.append(action.model_dump(mode="json"))
            continue
        if bq is not None and bq > 0:
            pos = pos.model_copy(update={"qty_remaining": int(bq)})

        # 2) Force-close window: flatten only
        if force_close:
            action = force_close_action(pos)
            result = _dispatch(
                action, dry_run=dry_run, exec_svc=exec_svc, ledger=ledger
            )
            try:
                record_closed_trade(pos, action)
            except Exception as exc:  # noqa: BLE001
                logger.warning("record_closed_trade failed: %s", exc)
            actions_out.append(
                {**action.model_dump(mode="json"), "result_ok": result.ok if result else True}
            )
            continue

        # 3) Manage ladder
        mark = mark_fn(pos.symbol)
        if mark is None:
            actions_out.append(
                {
                    "kind": "noop",
                    "symbol": pos.symbol,
                    "trade_id": pos.trade_id,
                    "message": "no mark; skip manage",
                }
            )
            remaining.append(pos)
            continue

        swing_candidate = None
        if pos.state.value.startswith("post_breakeven"):
            swing_candidate = swing_fn(pos.symbol)

        action = decide_manage(
            pos,
            mark,
            exit_rules=exit_rules,
            swing_stop_candidate=swing_candidate,
        )

        # Annotate remaining qty after partial for stop resize
        if action.kind == ExitActionKind.partial and action.qty:
            qty_after = max(1, pos.qty_remaining - int(action.qty))
            action = action.model_copy(
                update={"metadata": {**(action.metadata or {}), "qty_after": qty_after}}
            )

        result = _dispatch(action, dry_run=dry_run, exec_svc=exec_svc, ledger=ledger)
        actions_out.append(action.model_dump(mode="json"))

        updated = apply_action_to_position(pos, action)
        if updated is None:
            continue
        # Refresh stop_order_id after live modify/replace
        if (
            not dry_run
            and result is not None
            and result.stop_order_id
            and action.kind
            in (ExitActionKind.partial, ExitActionKind.modify_stop)
        ):
            updated = updated.model_copy(
                update={"stop_order_id": result.stop_order_id}
            )
        remaining.append(updated)

    store.save(remaining)
    return actions_out


def _dispatch(
    action: ExitAction,
    *,
    dry_run: bool,
    exec_svc: Optional[ExecutionService],
    ledger: Optional[Ledger],
):
    if exec_svc is not None:
        return exec_svc.apply_exit_action(action, dry_run=dry_run)
    # Dry-run without executor: still ledger if provided
    if ledger is not None and action.kind != ExitActionKind.noop:
        ledger.append(
            TradeEvent(
                event_type="info",
                symbol=action.symbol,
                message=action.event_channel,
                metadata={
                    "channel": action.event_channel,
                    "trade_id": action.trade_id,
                    "kind": action.kind.value,
                    "dry_run": True,
                    "exit_reason": action.exit_reason.value if action.exit_reason else None,
                    "mark": action.mark,
                    "R_progress": action.R_progress,
                    "new_stop": action.new_stop,
                    "qty": action.qty,
                },
            )
        )
    return None
