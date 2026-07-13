"""Exit ladder policy — pure decisions, no IBKR / IO."""

from __future__ import annotations

from typing import Iterable, Optional

from packages.contracts.position import (
    ExitAction,
    ExitActionKind,
    ExitReason,
    ManagedPosition,
    PositionLifecycle,
)
from services.position_manager.r_math import partial_qty, r_progress, trigger_price
from services.signal_generator.rules import ExitRules


def detect_stop_out(
    position: ManagedPosition,
    recent_fill_order_ids: Iterable[str | int],
) -> bool:
    """True when a fill's orderId matches stored stop_order_id (never by qty)."""
    if not position.stop_order_id:
        return False
    target = str(position.stop_order_id).strip()
    for oid in recent_fill_order_ids:
        if str(oid).strip() == target:
            return True
    return False


def stop_out_action(position: ManagedPosition, *, trail_after_be: bool = False) -> ExitAction:
    reason = ExitReason.trail if (
        trail_after_be or position.trail_updates_count > 0
    ) else (
        ExitReason.breakeven_then_stop
        if position.breakeven_done and position.trail_updates_count == 0
        else ExitReason.stop
    )
    return ExitAction(
        kind=ExitActionKind.clear,
        symbol=position.symbol,
        trade_id=position.trade_id,
        exit_reason=reason,
        event_channel="pm_stop_out",
        state_before=position.state,
        state_after=None,
        stop_order_id=position.stop_order_id,
        message=f"stop-out via stop_order_id={position.stop_order_id}",
        metadata={"matched_stop_order_id": position.stop_order_id},
    )


def external_close_action(position: ManagedPosition) -> ExitAction:
    return ExitAction(
        kind=ExitActionKind.clear,
        symbol=position.symbol,
        trade_id=position.trade_id,
        exit_reason=ExitReason.external,
        event_channel="pm_stop_out",
        state_before=position.state,
        message="broker flat; clearing managed state (external close)",
        metadata={"exit_reason": "external"},
    )


def force_close_action(position: ManagedPosition) -> ExitAction:
    return ExitAction(
        kind=ExitActionKind.flatten,
        symbol=position.symbol,
        trade_id=position.trade_id,
        qty=position.qty_remaining,
        stop_order_id=position.stop_order_id,
        exit_reason=ExitReason.force_close,
        event_channel="pm_force_close",
        state_before=position.state,
        state_after=None,
        message="force_close flatten",
    )


def decide_manage(
    position: ManagedPosition,
    mark: float,
    *,
    exit_rules: ExitRules,
    swing_stop_candidate: Optional[float] = None,
) -> ExitAction:
    """One tick of the exit ladder for a single open long (not force_close).

    Ordering: partial @ 0.75R (pre-BE only) → BE @ 1R → trail ratchet after BE.
    Idempotent via partial_done / breakeven_done flags.
    """
    R = position.R
    progress = r_progress(mark, position.entry_price, R) if R > 0 else 0.0
    partial_px = trigger_price(
        position.entry_price, R, exit_rules.partial_profit_trigger_R
    )
    be_px = trigger_price(position.entry_price, R, exit_rules.breakeven_trigger_R)

    # 1) Partial while still pre-breakeven
    if (
        position.state == PositionLifecycle.pre_breakeven
        and not position.partial_done
        and mark >= partial_px
    ):
        qty = partial_qty(position.qty_remaining, exit_rules.partial_profit_fraction)
        if qty >= 1:
            # After partial: stop on remaining at entry * 0.99 (blog ladder)
            new_stop = round(position.entry_price * 0.99, 4)
            return ExitAction(
                kind=ExitActionKind.partial,
                symbol=position.symbol,
                trade_id=position.trade_id,
                qty=qty,
                new_stop=new_stop,
                stop_order_id=position.stop_order_id,
                exit_reason=ExitReason.partial,
                event_channel="pm_partial",
                state_before=position.state,
                state_after=PositionLifecycle.post_breakeven_partial_done,
                mark=mark,
                R_progress=progress,
                message=f"partial sell {qty} @ ≥{partial_px:.4f} (0.75R)",
                metadata={
                    "partial_trigger": partial_px,
                    "new_stop": new_stop,
                },
            )

    # 2) Breakeven without (or after skipping) partial: mark ≥ 1.0R
    if (
        position.state == PositionLifecycle.pre_breakeven
        and not position.breakeven_done
        and mark >= be_px
    ):
        return ExitAction(
            kind=ExitActionKind.modify_stop,
            symbol=position.symbol,
            trade_id=position.trade_id,
            qty=position.qty_remaining,
            new_stop=round(position.entry_price, 4),
            stop_order_id=position.stop_order_id,
            event_channel="pm_breakeven",
            state_before=position.state,
            state_after=PositionLifecycle.post_breakeven_no_partial,
            mark=mark,
            R_progress=progress,
            message=f"move stop to breakeven entry={position.entry_price}",
            metadata={"breakeven_trigger": be_px},
        )

    # 3) Trail after any post-breakeven state — ratchet up only
    if position.state in (
        PositionLifecycle.post_breakeven_partial_done,
        PositionLifecycle.post_breakeven_no_partial,
    ):
        if (
            swing_stop_candidate is not None
            and swing_stop_candidate > position.current_stop
        ):
            return ExitAction(
                kind=ExitActionKind.modify_stop,
                symbol=position.symbol,
                trade_id=position.trade_id,
                qty=position.qty_remaining,
                new_stop=round(float(swing_stop_candidate), 4),
                stop_order_id=position.stop_order_id,
                event_channel="pm_trail",
                state_before=position.state,
                state_after=position.state,
                mark=mark,
                R_progress=progress,
                message=(
                    f"trail stop {position.current_stop} → {swing_stop_candidate}"
                ),
                metadata={
                    "stop_old": position.current_stop,
                    "stop_new": swing_stop_candidate,
                },
            )

    return ExitAction(
        kind=ExitActionKind.noop,
        symbol=position.symbol,
        trade_id=position.trade_id,
        event_channel="pm_noop",
        state_before=position.state,
        state_after=position.state,
        mark=mark,
        R_progress=progress,
        message="noop",
    )


def apply_action_to_position(
    position: ManagedPosition,
    action: ExitAction,
) -> ManagedPosition | None:
    """Return updated position, or None when cleared (stop-out / flatten / external)."""
    if action.kind == ExitActionKind.clear or action.kind == ExitActionKind.flatten:
        return None

    updated = position.model_copy(deep=True)

    if action.kind == ExitActionKind.partial and action.qty:
        updated.qty_remaining = max(0, updated.qty_remaining - int(action.qty))
        updated.partial_done = True
        updated.breakeven_done = True
        if action.state_after:
            updated.state = action.state_after
        if action.new_stop is not None:
            updated.current_stop = float(action.new_stop)
        return updated

    if action.kind == ExitActionKind.modify_stop and action.new_stop is not None:
        updated.current_stop = float(action.new_stop)
        if action.event_channel == "pm_breakeven":
            updated.breakeven_done = True
            if action.state_after:
                updated.state = action.state_after
        elif action.event_channel == "pm_trail":
            updated.trail_updates_count += 1
        return updated

    return updated


def reconcile_broker_qty(
    position: ManagedPosition,
    broker_qty: float,
) -> ExitAction | None:
    """If broker is flat but state open → external clear. Else sync qty."""
    if broker_qty <= 0:
        return external_close_action(position)
    return None
