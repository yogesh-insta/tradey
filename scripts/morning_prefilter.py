#!/usr/bin/env python3
"""CLI: morning gap prefilter (Yahoo only; no IBKR). MARKET=us|asx."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from packages.notify import notify
from services.market_profile import activate_market
from services.signal_generator.prefilter import (
    DEFAULT_MIN_GAP_PCT,
    DEFAULT_MIN_PRICE,
    result_json,
    run_prefilter,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Morning gap prefilter (us|asx)")
    parser.add_argument("--market", choices=["us", "asx"], default=None)
    parser.add_argument("--min-gap", type=float, default=DEFAULT_MIN_GAP_PCT)
    parser.add_argument("--min-price", type=float, default=DEFAULT_MIN_PRICE)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write watchlist",
    )
    parser.add_argument(
        "--watchlist",
        type=Path,
        default=None,
        help="Output path (default: data/{market}/watchlist.txt)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on universe size (debug / smoke)",
    )
    args = parser.parse_args()

    profile = activate_market(args.market)

    tickers = None
    if args.limit is not None:
        tickers = profile.universe()[: max(args.limit, 0)]

    result = run_prefilter(
        tickers=tickers,
        min_gap_pct=args.min_gap,
        min_price=args.min_price,
        dry_run=args.dry_run,
        watchlist_path=args.watchlist or profile.watchlist_path,
    )
    print(result_json(result))

    hhmm = datetime.now(profile.tz).strftime("%H:%M")
    tz_label = "Sydney" if profile.market == "asx" else "ET"
    if args.dry_run:
        return 0 if result.success else 1

    if result.success:
        bullets = "\n".join(f"• {s}" for s in result.to_summary()["top_20_survivors"])
        notify(
            f"Prefilter {hhmm} {tz_label} [{profile.market}]",
            f"{result.survivors_count} survivors in {result.elapsed_seconds:.1f}s\n{bullets}",
            "default",
        )
    else:
        notify(
            f"Prefilter FAILED {hhmm} {tz_label} [{profile.market}]",
            result.error or "unknown error",
            "high",
        )

    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
