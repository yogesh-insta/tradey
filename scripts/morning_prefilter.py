#!/usr/bin/env python3
"""CLI: morning S&P 500 gap prefilter (yfinance only; no IBKR)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from packages.notify import notify
from services.signal_generator.prefilter import (
    DEFAULT_MIN_GAP_PCT,
    DEFAULT_MIN_PRICE,
    result_json,
    run_prefilter,
)

ET = ZoneInfo("America/New_York")


def main() -> int:
    parser = argparse.ArgumentParser(description="S&P 500 morning gap prefilter")
    parser.add_argument("--min-gap", type=float, default=DEFAULT_MIN_GAP_PCT)
    parser.add_argument("--min-price", type=float, default=DEFAULT_MIN_PRICE)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write watchlist.txt",
    )
    parser.add_argument(
        "--watchlist",
        type=Path,
        default=None,
        help="Output path (default: ./watchlist.txt)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on universe size (debug / smoke)",
    )
    args = parser.parse_args()

    tickers = None
    if args.limit is not None:
        from services.signal_generator.universe.sp500_tickers import SP500_TICKERS

        tickers = SP500_TICKERS[: max(args.limit, 0)]

    result = run_prefilter(
        tickers=tickers,
        min_gap_pct=args.min_gap,
        min_price=args.min_price,
        dry_run=args.dry_run,
        watchlist_path=args.watchlist,
    )
    print(result_json(result))

    hhmm = datetime.now(ET).strftime("%H:%M")
    if args.dry_run:
        return 0 if result.success else 1

    if result.success:
        bullets = "\n".join(f"• {s}" for s in result.to_summary()["top_20_survivors"])
        notify(
            f"Prefilter {hhmm} ET",
            f"{result.survivors_count}/503 survivors in {result.elapsed_seconds:.1f}s\n{bullets}",
            "default",
        )
    else:
        notify(f"Prefilter FAILED {hhmm} ET", result.error or "unknown error", "high")

    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
