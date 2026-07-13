"""Swing-low trail helpers (swing_low_5m_2_2). Pure, no IBKR."""

from __future__ import annotations

from typing import Sequence


def swing_low_indices(
    lows: Sequence[float],
    *,
    left: int = 2,
    right: int = 2,
) -> list[int]:
    """Indices of swing lows: low[i] strictly < lows of `left` bars before and `right` after."""
    n = len(lows)
    out: list[int] = []
    if n < left + right + 1:
        return out
    for i in range(left, n - right):
        center = float(lows[i])
        before = [float(lows[j]) for j in range(i - left, i)]
        after = [float(lows[j]) for j in range(i + 1, i + 1 + right)]
        if all(center < b for b in before) and all(center < a for a in after):
            out.append(i)
    return out


def newest_completed_swing_low(
    lows: Sequence[float],
    *,
    left: int = 2,
    right: int = 2,
) -> float | None:
    """Most recent completed swing low value, or None."""
    idxs = swing_low_indices(lows, left=left, right=right)
    if not idxs:
        return None
    return float(lows[idxs[-1]])


def trail_stop_from_swing(
    lows: Sequence[float],
    *,
    left: int = 2,
    right: int = 2,
    offset: float = 0.01,
) -> float | None:
    """Candidate stop = newest swing low − offset (blog swing_low_5m_2_2)."""
    swing = newest_completed_swing_low(lows, left=left, right=right)
    if swing is None:
        return None
    return round(swing - float(offset), 4)
