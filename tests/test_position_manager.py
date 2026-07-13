"""Unit tests for Position Manager pure policy / swing / R math."""

from __future__ import annotations

from packages.contracts.position import (
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
from services.position_manager.swing import (
    newest_completed_swing_low,
    swing_low_indices,
    trail_stop_from_swing,
)
from services.signal_generator.rules import ExitRules


def _pos(**kwargs) -> ManagedPosition:
    base = dict(
        trade_id="t1",
        symbol="AAPL",
        qty_remaining=9,
        entry_price=100.0,
        initial_stop=98.0,
        R=2.0,
        current_stop=98.0,
        stop_order_id="55",
        state=PositionLifecycle.pre_breakeven,
    )
    base.update(kwargs)
    return ManagedPosition(**base)


def test_compute_R_and_triggers():
    R = compute_R(100.0, 98.0)
    assert R == 2.0
    assert trigger_price(100.0, R, 0.75) == 101.5
    assert trigger_price(100.0, R, 1.0) == 102.0
    assert abs(r_progress(101.5, 100.0, R) - 0.75) < 1e-9


def test_partial_qty_keeps_one_share():
    assert partial_qty(1, 0.3333) == 0
    assert partial_qty(3, 0.3333) == 1
    assert partial_qty(9, 0.3333) == 3
    assert partial_qty(2, 0.3333) == 1


def test_swing_low_5m_2_2():
    # indices: 0..8 ; swing at i=4 (value 10) strictly lower than neighbors
    lows = [15, 14, 13, 12, 10, 12, 13, 14, 15]
    assert swing_low_indices(lows, left=2, right=2) == [4]
    assert newest_completed_swing_low(lows) == 10.0
    assert trail_stop_from_swing(lows) == 9.99


def test_partial_at_0_75R():
    rules = ExitRules()
    action = decide_manage(_pos(), mark=101.5, exit_rules=rules)
    assert action.kind == ExitActionKind.partial
    assert action.qty == 3
    assert action.new_stop == 99.0  # entry * 0.99
    assert action.state_after == PositionLifecycle.post_breakeven_partial_done
    updated = apply_action_to_position(_pos(), action)
    assert updated is not None
    assert updated.partial_done and updated.breakeven_done
    assert updated.qty_remaining == 6
    assert updated.state == PositionLifecycle.post_breakeven_partial_done


def test_breakeven_at_1R_skips_when_partial_not_taken_first_if_below_partial():
    # Mark at 1.0R without hitting partial first — BE path
    rules = ExitRules()
    # If mark >= 1R and still pre_breakeven without partial_done, BE wins over
    # waiting — but plan order is: check partial first (mark >= 0.75), so at 1R
    # we take partial if not done.
    action = decide_manage(_pos(), mark=102.0, exit_rules=rules)
    assert action.kind == ExitActionKind.partial  # 1R also satisfies 0.75R


def test_breakeven_when_partial_skipped_qty1():
    rules = ExitRules()
    pos = _pos(qty_remaining=1)
    # 0.75R would want partial but qty==1 → skip; at 1R → BE
    action = decide_manage(pos, mark=101.5, exit_rules=rules)
    assert action.kind == ExitActionKind.noop  # partial skipped, BE not yet at 0.75 only
    action = decide_manage(pos, mark=102.0, exit_rules=rules)
    assert action.kind == ExitActionKind.modify_stop
    assert action.event_channel == "pm_breakeven"
    assert action.new_stop == 100.0


def test_trail_ratchets_up_only():
    rules = ExitRules()
    pos = _pos(
        state=PositionLifecycle.post_breakeven_no_partial,
        breakeven_done=True,
        current_stop=100.0,
    )
    # Candidate below current → noop
    action = decide_manage(pos, mark=105.0, exit_rules=rules, swing_stop_candidate=99.5)
    assert action.kind == ExitActionKind.noop
    # Candidate above → trail
    action = decide_manage(pos, mark=105.0, exit_rules=rules, swing_stop_candidate=100.5)
    assert action.kind == ExitActionKind.modify_stop
    assert action.event_channel == "pm_trail"
    assert action.new_stop == 100.5
    updated = apply_action_to_position(pos, action)
    assert updated is not None
    assert updated.trail_updates_count == 1
    assert updated.current_stop == 100.5


def test_stop_out_by_order_id_not_qty():
    pos = _pos(stop_order_id="99", qty_remaining=6)
    # After partial, qty dropped — must NOT treat unrelated fills as stop-out
    assert not detect_stop_out(pos, ["12", "34"])
    assert detect_stop_out(pos, ["12", "99", "34"])
    action = stop_out_action(pos)
    assert action.kind == ExitActionKind.clear
    assert action.exit_reason == ExitReason.stop
    assert apply_action_to_position(pos, action) is None


def test_stop_out_after_partial_still_matches_stop_id():
    """Known Mac bug: matching by filled qty after partial causes false stop-outs."""
    pos = _pos(stop_order_id="77", qty_remaining=6, partial_done=True)
    # Sell of 3 shares (partial) must not clear if order id != stop
    assert not detect_stop_out(pos, ["partial_sell_id"])
    assert detect_stop_out(pos, ["77"])


def test_force_close_flatten():
    pos = _pos()
    action = force_close_action(pos)
    assert action.kind == ExitActionKind.flatten
    assert action.exit_reason == ExitReason.force_close
    assert action.event_channel == "pm_force_close"
    assert apply_action_to_position(pos, action) is None


def test_idempotent_partial_flag():
    rules = ExitRules()
    pos = _pos(partial_done=True, breakeven_done=True,
               state=PositionLifecycle.post_breakeven_partial_done,
               current_stop=99.0)
    action = decide_manage(pos, mark=110.0, exit_rules=rules, swing_stop_candidate=None)
    assert action.kind == ExitActionKind.noop
