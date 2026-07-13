#!/usr/bin/env python3
"""
Today's P&L summary from ledger (+ optional IBKR portfolio snapshot).

Run tomorrow morning (or any time):

  source .venv/bin/activate
  python scripts/pnl_report.py
  python scripts/pnl_report.py --notify          # also Telegram
  python scripts/pnl_report.py --ibkr            # include Gateway NetLiq / day PnL
  python scripts/pnl_report.py --date 2026-07-13

Ask the agent tomorrow for a P&L update, or run the commands above yourself.
No launchd / cron required.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from packages.ledger import Ledger
from packages.notify import notify
from services.risk_manager.capital import CapitalConfig, usd_to_aud


def _event_counts_on(ledger: Ledger, day: date) -> dict[str, int]:
    day_s = day.isoformat()
    with ledger._connect() as conn:  # noqa: SLF001 — lightweight group-by read
        rows = conn.execute(
            """
            SELECT event_type, COUNT(*) AS n
            FROM events
            WHERE substr(timestamp_utc, 1, 10) = ?
            GROUP BY event_type
            """,
            (day_s,),
        ).fetchall()
    return {r["event_type"]: int(r["n"]) for r in rows}


def _ledger_day_summary(ledger: Ledger, day: date) -> dict[str, Any]:
    """Fills + event counts for a UTC calendar day (ledger timestamps are UTC ISO)."""
    fill_rows = ledger.fills_on(day)
    buys = [f for f in fill_rows if (f.get("side") or "").upper() == "BUY"]
    sells = [f for f in fill_rows if (f.get("side") or "").upper() == "SELL"]
    buy_notional = sum((f.get("qty") or 0) * (f.get("price") or 0) for f in buys)
    sell_notional = sum((f.get("qty") or 0) * (f.get("price") or 0) for f in sells)
    # Crude realized proxy: sells − buys on the day (not mark-to-market).
    crude_pnl = sell_notional - buy_notional
    return {
        "date": day.isoformat(),
        "fill_count": len(fill_rows),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "buy_notional_usd": round(buy_notional, 2),
        "sell_notional_usd": round(sell_notional, 2),
        "crude_pnl_usd": round(crude_pnl, 2),
        "event_counts": _event_counts_on(ledger, day),
        "fills": [
            {
                "ts": f.get("timestamp_utc"),
                "side": f.get("side"),
                "symbol": f.get("symbol"),
                "qty": f.get("qty"),
                "price": f.get("price"),
            }
            for f in fill_rows
        ],
    }


def _ibkr_snapshot() -> Optional[dict[str, Any]]:
    """Best-effort paper portfolio snapshot; never raises to caller."""
    try:
        from services.order_executor.execution_service import ExecutionService
        from services.order_executor.paper_guard import check_paper_guard

        guard = check_paper_guard()
        if not guard.ok:
            return {"ok": False, "error": guard.message}
        with ExecutionService() as svc:
            snap = svc.safe_portfolio()
            if snap is None:
                return {"ok": False, "error": "no snapshot"}
            return {
                "ok": True,
                "account_id": snap.account_id,
                "net_liquidation_usd": snap.net_liquidation_usd,
                "day_pnl_usd": snap.day_pnl_usd,
                "open_position_count": snap.open_position_count,
                "positions": [
                    {"symbol": p.symbol, "qty": p.qty, "avg_cost": p.avg_cost}
                    for p in snap.positions
                ],
            }
    except SystemExit as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def build_report(*, day: date, include_ibkr: bool) -> dict[str, Any]:
    capital = CapitalConfig.from_env()
    ledger = Ledger()
    ledger_part = _ledger_day_summary(ledger, day)
    ibkr_part = _ibkr_snapshot() if include_ibkr else None

    report: dict[str, Any] = {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "day": day.isoformat(),
        "capital": {
            "portfolio_value_aud": capital.portfolio_value_aud,
            "usd_aud": capital.usd_aud,
            "portfolio_value_usd_cap": round(capital.portfolio_value_usd_cap, 2),
        },
        "ledger": ledger_part,
        "ibkr": ibkr_part,
    }

    # Prefer broker day PnL when available; else crude ledger proxy.
    if ibkr_part and ibkr_part.get("ok"):
        day_pnl = float(ibkr_part.get("day_pnl_usd") or 0.0)
        report["day_pnl_usd"] = round(day_pnl, 2)
        report["day_pnl_source"] = "ibkr"
        report["day_pnl_aud_equiv"] = round(
            usd_to_aud(day_pnl, usd_aud=capital.usd_aud), 2
        )
    else:
        day_pnl = float(ledger_part["crude_pnl_usd"])
        report["day_pnl_usd"] = day_pnl
        report["day_pnl_source"] = "ledger_crude"
        report["day_pnl_aud_equiv"] = round(
            usd_to_aud(day_pnl, usd_aud=capital.usd_aud), 2
        )
    return report


def format_text(report: dict[str, Any]) -> str:
    led = report["ledger"]
    lines = [
        f"P&L report {report['day']} (source={report['day_pnl_source']})",
        f"day_pnl_usd={report['day_pnl_usd']}  aud_equiv≈{report['day_pnl_aud_equiv']}",
        f"cap=A${report['capital']['portfolio_value_aud']:g}  "
        f"usd_cap≈{report['capital']['portfolio_value_usd_cap']}",
        f"fills={led['fill_count']} buys={led['buy_count']} sells={led['sell_count']}",
        f"buy_notional={led['buy_notional_usd']} sell_notional={led['sell_notional_usd']}",
    ]
    ibkr = report.get("ibkr")
    if ibkr and ibkr.get("ok"):
        lines.append(
            f"IBKR net_liq={ibkr.get('net_liquidation_usd')} "
            f"open={ibkr.get('open_position_count')} acct={ibkr.get('account_id')}"
        )
    elif ibkr and not ibkr.get("ok"):
        lines.append(f"IBKR snapshot skipped: {ibkr.get('error')}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Today's P&L summary (ledger ± IBKR)")
    parser.add_argument(
        "--date",
        default=None,
        help="UTC calendar day YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--ibkr",
        action="store_true",
        help="Also fetch paper Gateway portfolio / day PnL",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send summary to Telegram (never fails the script)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON report",
    )
    parser.add_argument(
        "--expectancy",
        action="store_true",
        help="Include closed-trade expectancy / exit attribution rollup",
    )
    args = parser.parse_args()

    day = date.fromisoformat(args.date) if args.date else date.today()
    report = build_report(day=day, include_ibkr=args.ibkr)

    if args.expectancy:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from expectancy_report import (  # type: ignore
            build_expectancy_report,
            format_text as format_exp,
        )

        report["expectancy"] = build_expectancy_report()

    text = format_text(report)
    if args.expectancy and report.get("expectancy"):
        text = text + "\n\n" + format_exp(report["expectancy"])

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(text)

    if args.notify:
        notify("P&L report", text, "default")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
