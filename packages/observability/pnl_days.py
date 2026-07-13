"""Daily P/L rollup for dashboard / API (plan 08).

Prefers closed-trades JSONL (realized_pnl_usd + expectancy_R). Falls back to
ledger fill crude (sell − buy notional) when a day has fills but no closed trades.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from packages.observability.expectancy import expectancy_R, per_trade_r_net


SOURCE_CLOSED = "closed_trades"
SOURCE_CRUDE = "ledger_crude"
SOURCE_EMPTY = "empty"

CRUDE_NOTE = (
    "Some days use ledger crude P/L (sell − buy notional) because no closed "
    "trades were recorded for that UTC day. Prefer closed_trades.jsonl once "
    "exits land."
)


def trade_exit_day(trade: dict[str, Any]) -> Optional[str]:
    """UTC calendar date (YYYY-MM-DD) from exit_ts_utc / exit_ts."""
    raw = trade.get("exit_ts_utc") or trade.get("exit_ts") or trade.get("exit_ts_et")
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # ISO: 2026-07-14T12:00:00+00:00 or date-only
    return s[:10] if len(s) >= 10 else None


def _r_net_for(trade: dict[str, Any]) -> Optional[float]:
    r = trade.get("r_net")
    if r is not None:
        try:
            return float(r)
        except (TypeError, ValueError):
            return None
    pnl = trade.get("realized_pnl_usd")
    R = trade.get("R")
    if pnl is None or R is None:
        return None
    try:
        return per_trade_r_net(float(pnl), float(R))
    except (TypeError, ValueError):
        return None


def crude_pnl_from_fills(fills: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Sell − buy notional proxy (not mark-to-market / not true realized)."""
    buys = [f for f in fills if (f.get("side") or "").upper() == "BUY"]
    sells = [f for f in fills if (f.get("side") or "").upper() == "SELL"]
    buy_notional = sum((f.get("qty") or 0) * (f.get("price") or 0) for f in buys)
    sell_notional = sum((f.get("qty") or 0) * (f.get("price") or 0) for f in sells)
    return {
        "crude_pnl_usd": round(float(sell_notional - buy_notional), 2),
        "fill_count": len(fills),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "buy_notional_usd": round(float(buy_notional), 2),
        "sell_notional_usd": round(float(sell_notional), 2),
    }


def rollup_closed_by_day(trades: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group closed trades by UTC exit day. Pure; does not invent empty days."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        day = trade_exit_day(t)
        if not day:
            continue
        buckets.setdefault(day, []).append(t)

    out: dict[str, dict[str, Any]] = {}
    for day, rows in sorted(buckets.items()):
        pnls: list[float] = []
        r_nets: list[float] = []
        for t in rows:
            pnl = t.get("realized_pnl_usd")
            if pnl is not None:
                try:
                    pnls.append(float(pnl))
                except (TypeError, ValueError):
                    pass
            r = _r_net_for(t)
            if r is not None:
                r_nets.append(r)
        day_pnl = round(sum(pnls), 2) if pnls else None
        out[day] = {
            "date": day,
            "day_pnl_usd": day_pnl,
            "trade_count": len(rows),
            "expectancy_R": expectancy_R(r_nets),
            "n_with_r": len(r_nets),
            "source": SOURCE_CLOSED,
        }
    return out


def iter_day_strings(*, days: int, end: Optional[date] = None) -> list[str]:
    """Inclusive window of `days` calendar dates ending at `end` (UTC today default)."""
    if days < 1:
        return []
    end_d = end or datetime.now(timezone.utc).date()
    return [(end_d - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def build_pnl_days(
    trades: Sequence[dict[str, Any]],
    *,
    days: int = 10,
    end: Optional[date] = None,
    ledger_by_day: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Past-N-days strip: closed trades preferred; optional ledger crude fallback.

    `ledger_by_day` maps YYYY-MM-DD → crude_pnl_from_fills result (or any dict with
    crude_pnl_usd / fill_count).
    """
    closed = rollup_closed_by_day(trades)
    ledger_by_day = ledger_by_day or {}
    window = iter_day_strings(days=days, end=end)

    rows: list[dict[str, Any]] = []
    used_crude = False
    for day_s in window:
        if day_s in closed:
            row = dict(closed[day_s])
            rows.append(row)
            continue

        crude = ledger_by_day.get(day_s)
        if crude and (crude.get("fill_count") or 0) > 0:
            used_crude = True
            rows.append(
                {
                    "date": day_s,
                    "day_pnl_usd": crude.get("crude_pnl_usd"),
                    "trade_count": int(crude.get("fill_count") or 0),
                    "expectancy_R": None,
                    "n_with_r": 0,
                    "source": SOURCE_CRUDE,
                }
            )
            continue

        rows.append(
            {
                "date": day_s,
                "day_pnl_usd": None,
                "trade_count": 0,
                "expectancy_R": None,
                "n_with_r": 0,
                "source": SOURCE_EMPTY,
            }
        )

    return {
        "days": days,
        "end": (end or datetime.now(timezone.utc).date()).isoformat(),
        "rows": rows,
        "used_crude_fallback": used_crude,
        "note": CRUDE_NOTE if used_crude else None,
        "n_closed_in_window": sum(
            r["trade_count"] for r in rows if r["source"] == SOURCE_CLOSED
        ),
        "closed_trade_days": sum(1 for r in rows if r["source"] == SOURCE_CLOSED),
    }
