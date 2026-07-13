"""Expectancy / R-multiple rollups from closed-trade records (pure helpers)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional, Sequence


def per_trade_r_net(realized_pnl_usd: float, R: float) -> Optional[float]:
    """r_net = realized_pnl / R. None when R is invalid."""
    if R is None or R <= 0:
        return None
    return float(realized_pnl_usd) / float(R)


def expectancy_R(r_nets: Sequence[float]) -> Optional[float]:
    """Mean of per-trade r_net (primary north star). Empty → None."""
    vals = [float(x) for x in r_nets]
    if not vals:
        return None
    return sum(vals) / len(vals)


def win_rate(r_nets: Sequence[float]) -> Optional[float]:
    vals = [float(x) for x in r_nets]
    if not vals:
        return None
    wins = sum(1 for x in vals if x > 0)
    return wins / len(vals)


def avg_win_loss_R(r_nets: Sequence[float]) -> tuple[Optional[float], Optional[float]]:
    wins = [float(x) for x in r_nets if x > 0]
    losses = [float(x) for x in r_nets if x < 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    return avg_win, avg_loss


def profit_factor_R(r_nets: Sequence[float]) -> Optional[float]:
    pos = sum(x for x in r_nets if x > 0)
    neg = abs(sum(x for x in r_nets if x < 0))
    if neg <= 0:
        return None if pos <= 0 else float("inf")
    return pos / neg


def expectancy_from_wr(win_rate_v: float, avg_win_R: float, avg_loss_R: float) -> float:
    """(WR × avg_win_R) − ((1−WR) × |avg_loss_R|)."""
    return (win_rate_v * avg_win_R) - ((1.0 - win_rate_v) * abs(avg_loss_R))


def exit_attribution(trades: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Counts + mean r_net by exit_reason."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        reason = str(t.get("exit_reason") or "unknown")
        r = t.get("r_net")
        if r is None and t.get("R") and t.get("realized_pnl_usd") is not None:
            r = per_trade_r_net(float(t["realized_pnl_usd"]), float(t["R"]))
        if r is None:
            buckets[reason]  # ensure key
            continue
        buckets[reason].append(float(r))
    out: dict[str, dict[str, Any]] = {}
    for reason, vals in sorted(buckets.items()):
        out[reason] = {
            "count": len(vals),
            "expectancy_R": expectancy_R(vals),
            "win_rate": win_rate(vals),
        }
    return out


def rolling_expectancy_flag(
    r_nets: Sequence[float],
    *,
    window: int = 10,
    kill_below: float = -0.25,
) -> dict[str, Any]:
    """Report-only kill helper: flag when rolling expectancy collapses."""
    vals = [float(x) for x in r_nets]
    recent = vals[-window:] if vals else []
    exp = expectancy_R(recent)
    return {
        "window": window,
        "n": len(recent),
        "expectancy_R": exp,
        "kill_below": kill_below,
        "triggered": exp is not None and exp < kill_below and len(recent) >= min(window, 5),
    }


def summarize_closed_trades(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    r_nets = []
    for t in trades:
        r = t.get("r_net")
        if r is None and t.get("R") and t.get("realized_pnl_usd") is not None:
            r = per_trade_r_net(float(t["realized_pnl_usd"]), float(t["R"]))
        if r is not None:
            r_nets.append(float(r))
    avg_win, avg_loss = avg_win_loss_R(r_nets)
    wr = win_rate(r_nets)
    return {
        "n_closed": len(trades),
        "n_with_r": len(r_nets),
        "win_rate": wr,
        "avg_win_R": avg_win,
        "avg_loss_R": avg_loss,
        "expectancy_R": expectancy_R(r_nets),
        "profit_factor_R": profit_factor_R(r_nets),
        "exit_attribution": exit_attribution(trades),
        "kill_check": rolling_expectancy_flag(r_nets),
    }
