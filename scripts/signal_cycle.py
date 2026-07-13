#!/usr/bin/env python3
"""
Signal cycle (paper-safe): prefilter watchlist → filters → risk gate → optional exec.

Default is --dry-run (no IBKR orders). Requires watchlist.txt unless --symbols given.
IB Gateway is NOT required for dry-run / filter evaluation (Yahoo via httpx).

With --no-dry-run:
  paper guard → ExecutionService portfolio snapshot → Risk (AUD 10k cap) → execute.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from packages.contracts import TradeEvent
from packages.ledger import Ledger
from packages.notify import notify
from services.order_executor.execution_service import ExecutionService
from services.order_executor.paper_guard import check_paper_guard
from services.position_manager.state import PositionStore
from services.risk_manager.capital import CapitalConfig, usd_to_aud
from services.risk_manager.policy import classify_session
from services.session.entry_tick import run_entry_tick
from services.signal_generator.rules import load_rules, rules_version_stamp


def main() -> int:
    capital = CapitalConfig.from_env()
    parser = argparse.ArgumentParser(description="Trend Join Long signal cycle")
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Default true: evaluate only; never place IBKR orders",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Override watchlist with explicit IBKR symbols",
    )
    parser.add_argument(
        "--portfolio-value",
        type=float,
        default=None,
        help="Optional USD sizing override (still capped by PORTFOLIO_VALUE_AUD)",
    )
    parser.add_argument(
        "--open-positions",
        type=int,
        default=0,
        help="Open count for concurrency gate when no broker snapshot",
    )
    parser.add_argument(
        "--day-pnl",
        type=float,
        default=0.0,
        help="Day PnL USD stub when no broker snapshot (dry-run)",
    )
    args = parser.parse_args()

    rules = load_rules()
    session = classify_session(time_filter=rules.time_filter)
    print(
        json.dumps(
            {
                "session": session.code,
                "allow_new_entries": session.allow_new_entries,
                "dry_run": args.dry_run,
                "strategy": rules.strategy_name,
                "rules_version": rules_version_stamp(rules),
                "portfolio_value_aud": capital.portfolio_value_aud,
                "usd_aud": capital.usd_aud,
                "portfolio_value_usd_cap": round(capital.portfolio_value_usd_cap, 2),
            },
            indent=2,
        )
    )

    snap = None
    exec_svc: Optional[ExecutionService] = None

    if not args.dry_run:
        guard = check_paper_guard()
        if not guard.ok:
            print(guard.message, file=sys.stderr)
            notify("Order blocked", guard.message, "high")
            return 1
        try:
            exec_svc = ExecutionService()
            exec_svc.open()
            snap = exec_svc.safe_portfolio()
            if snap is None:
                print("ABORT: no portfolio snapshot (broker disconnect?)", file=sys.stderr)
                notify("Order blocked", "no portfolio snapshot", "high")
                return 1
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            notify("Order blocked", str(exc), "high")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"ABORT: executor open failed: {exc}", file=sys.stderr)
            notify("Order blocked", str(exc)[:400], "high")
            return 1

    try:
        ledger = Ledger()
        entry = run_entry_tick(
            rules=rules,
            capital=capital,
            dry_run=args.dry_run,
            symbols=args.symbols,
            snap=snap,
            exec_svc=exec_svc,
            store=PositionStore(),
            session_code=session.code,
            open_positions_arg=args.open_positions,
            day_pnl_arg=args.day_pnl,
            portfolio_usd_cli=args.portfolio_value,
            ledger=ledger,
        )
        print(
            json.dumps(
                {
                    "portfolio_context": {
                        **entry.capital_meta,
                        "sizing_portfolio_usd": round(entry.portfolio_usd, 2),
                        "sizing_portfolio_aud_equiv": round(
                            usd_to_aud(entry.portfolio_usd, usd_aud=capital.usd_aud), 2
                        ),
                        "open_positions": entry.open_count,
                        "day_pnl_usd": entry.day_pnl_usd,
                    }
                },
                indent=2,
            )
        )

        summary = {
            "evaluated": entry.evaluated,
            "filter_passed": entry.filter_passed,
            "risk_approved": entry.risk_approved,
            "risk_rejected": entry.risk_rejected,
            "executed": entry.executed,
            "intents": [i.model_dump(mode="json") for i in entry.intents],
            "rejections": entry.rejections,
            "executions": entry.executions,
            "managed_opened": entry.managed_opened,
            "dry_run": args.dry_run,
            "rules_version": rules_version_stamp(rules),
        }
        print(json.dumps(summary, indent=2, default=str))

        ledger.append(
            TradeEvent(
                event_type="info",
                message="signal_cycle",
                metadata={
                    "dry_run": args.dry_run,
                    "evaluated": summary["evaluated"],
                    "filter_passed": summary["filter_passed"],
                    "risk_approved": summary["risk_approved"],
                    "executed": summary["executed"],
                    "sizing_portfolio_usd": entry.portfolio_usd,
                    "portfolio_value_aud": capital.portfolio_value_aud,
                    "rules_version": rules_version_stamp(rules),
                    "session_code": session.code,
                },
            )
        )

        notify(
            "Signal cycle",
            (
                f"session={session.code} dry_run={args.dry_run}\n"
                f"eval={summary['evaluated']} pass={summary['filter_passed']} "
                f"approved={summary['risk_approved']} executed={summary['executed']}\n"
                f"cap=A${capital.portfolio_value_aud:g} sizing_usd={entry.portfolio_usd:.0f}"
            ),
            "default",
        )
        return 0
    finally:
        if exec_svc is not None:
            exec_svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
