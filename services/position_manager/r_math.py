"""R-multiple helpers for the exit ladder (longs). Pure math, no IO."""

from __future__ import annotations

import math


def compute_R(entry_price: float, initial_stop: float) -> float:
    """1R distance for a long: entry − initial stop (must be > 0)."""
    return float(entry_price) - float(initial_stop)


def trigger_price(entry_price: float, R: float, multiple: float) -> float:
    """Mark level at which `multiple` R of profit is reached."""
    return float(entry_price) + float(multiple) * float(R)


def r_progress(mark: float, entry_price: float, R: float) -> float:
    """How many R the mark is above entry. Negative when underwater."""
    if R <= 0:
        return 0.0
    return (float(mark) - float(entry_price)) / float(R)


def partial_qty(qty_remaining: int, fraction: float) -> int:
    """Ceil(qty * fraction), keep ≥1 share when qty ≥ 2; skip when qty == 1.

    Blog/Mac: ~ceil(qty / 3) for fraction 0.3333. Never flatten via partial
    when shares remain for the trail path.
    """
    qty = int(qty_remaining)
    if qty <= 1:
        return 0
    raw = math.ceil(qty * float(fraction))
    # Keep at least 1 share for trail / BE path.
    return max(1, min(raw, qty - 1))
