"""Position Manager — exit ladder policy + managed state (no IBKR SDK)."""

from packages.contracts.position import (
    ExitAction,
    ExitActionKind,
    ExitReason,
    ManagedPosition,
    PositionLifecycle,
)
from services.position_manager.policy import (
    apply_action_to_position,
    decide_manage,
    detect_stop_out,
    force_close_action,
    stop_out_action,
)
from services.position_manager.r_math import compute_R, partial_qty, r_progress, trigger_price
from services.position_manager.state import PositionStore
from services.position_manager.swing import trail_stop_from_swing

__all__ = [
    "ExitAction",
    "ExitActionKind",
    "ExitReason",
    "ManagedPosition",
    "PositionLifecycle",
    "PositionStore",
    "apply_action_to_position",
    "compute_R",
    "decide_manage",
    "detect_stop_out",
    "force_close_action",
    "partial_qty",
    "r_progress",
    "stop_out_action",
    "trail_stop_from_swing",
    "trigger_price",
]
