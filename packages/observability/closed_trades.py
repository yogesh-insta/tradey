"""Append closed-trade attribution records (plan 08)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from packages.contracts.position import ExitAction, ManagedPosition
from packages.observability.expectancy import per_trade_r_net

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "data" / "us" / "closed_trades.jsonl"


def closed_trades_path() -> Path:
    raw = os.getenv("CLOSED_TRADES_PATH", "")
    if raw.strip():
        path = Path(raw.strip())
        return path if path.is_absolute() else ROOT / path
    try:
        from services.market_profile import get_market_profile

        return get_market_profile().closed_trades_path
    except Exception:  # noqa: BLE001
        return DEFAULT_PATH


def record_closed_trade(
    position: ManagedPosition,
    action: ExitAction,
    *,
    realized_pnl_usd: Optional[float] = None,
    fees_usd: float = 0.0,
    path: Optional[Path] = None,
) -> dict[str, Any]:
    """Append one closed-trade JSONL row for expectancy reports."""
    exit_reason = action.exit_reason.value if action.exit_reason else "unknown"
    # Crude PnL stub when broker net unknown: (exit_mark - entry) * qty
    # Prefer caller-supplied realized_pnl_usd when available.
    pnl = realized_pnl_usd
    if pnl is None and action.mark is not None:
        # For stop/flatten without fill price, use mark as proxy
        sold_qty = position.qty_remaining
        pnl = (float(action.mark) - float(position.entry_price)) * sold_qty
    r_net = None
    if pnl is not None and position.R > 0:
        r_net = per_trade_r_net(float(pnl), float(position.R))

    row: dict[str, Any] = {
        "trade_id": position.trade_id,
        "rules_version": position.rules_version,
        "experiment_id": (position.metadata or {}).get("experiment_id"),
        "symbol": position.symbol,
        "signal_id": position.signal_id,
        "session_code_at_entry": position.session_code_at_entry,
        "entry_ts_utc": position.opened_at_et,
        "exit_ts_utc": datetime.now(timezone.utc).isoformat(),
        "filters_fired": position.filters_fired,
        "signal_reasons": position.signal_reasons,
        "signal_metrics": position.signal_metrics,
        "qty_initial": position.qty_initial or position.qty_remaining,
        "entry_price": position.entry_price,
        "initial_stop": position.initial_stop,
        "R": position.R,
        "risk_usd": position.risk_usd,
        "sizing_portfolio_usd": position.sizing_portfolio_usd,
        "partial_done": position.partial_done,
        "breakeven_done": position.breakeven_done,
        "trail_updates_count": position.trail_updates_count,
        "exit_reason": exit_reason,
        "exit_stage_final": position.state.value if position.state else None,
        "qty_final": 0,
        "realized_pnl_usd": pnl,
        "r_net": r_net,
        "fees_usd": fees_usd,
    }

    out = path or closed_trades_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    return row


def load_closed_trades(path: Optional[Path] = None) -> list[dict[str, Any]]:
    p = path or closed_trades_path()
    if not p.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
