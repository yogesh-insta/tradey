#!/usr/bin/env python3
"""
Expectancy / R-multiple report from closed trades + optional day PnL.

  python scripts/expectancy_report.py
  python scripts/expectancy_report.py --json
  python scripts/pnl_report.py --expectancy   # day PnL + expectancy rollup

Closed trades: data/closed_trades.jsonl (written on stop-out / force-close / external).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from packages.observability.closed_trades import load_closed_trades
from packages.observability.expectancy import summarize_closed_trades


def build_expectancy_report(*, limit: int | None = None) -> dict[str, Any]:
    trades = load_closed_trades()
    if limit is not None and limit > 0:
        trades = trades[-limit:]
    summary = summarize_closed_trades(trades)
    return {
        "source": "closed_trades.jsonl",
        "n_loaded": len(trades),
        **summary,
        "trades_sample": trades[-5:],
    }


def format_text(report: dict[str, Any]) -> str:
    lines = [
        f"Expectancy report (n={report.get('n_with_r')}/{report.get('n_closed')} with R)",
        f"win_rate={report.get('win_rate')}",
        f"avg_win_R={report.get('avg_win_R')}  avg_loss_R={report.get('avg_loss_R')}",
        f"expectancy_R={report.get('expectancy_R')}  profit_factor_R={report.get('profit_factor_R')}",
    ]
    kill = report.get("kill_check") or {}
    if kill.get("triggered"):
        lines.append(
            f"KILL FLAG: rolling expectancy_R={kill.get('expectancy_R')} "
            f"< {kill.get('kill_below')} (window={kill.get('window')})"
        )
    attr = report.get("exit_attribution") or {}
    if attr:
        lines.append("exit_attribution:")
        for reason, stats in attr.items():
            lines.append(
                f"  {reason}: n={stats.get('count')} "
                f"E[R]={stats.get('expectancy_R')} wr={stats.get('win_rate')}"
            )
    if report.get("n_closed", 0) == 0:
        lines.append(
            "(no closed trades yet — run session_runner paper soak; "
            "records append to data/closed_trades.jsonl)"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Expectancy / R rollup")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Use only the last N closed trades",
    )
    args = parser.parse_args()
    report = build_expectancy_report(limit=args.limit)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
