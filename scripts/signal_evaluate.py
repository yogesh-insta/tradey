#!/usr/bin/env python3
"""Evaluate D1–D3 / I1–I3 for one or more symbols (yfinance; no IBKR)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from services.signal_generator.filters import evaluate_symbol, evaluation_to_signal
from services.signal_generator.rules import load_rules


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Trend Join Long filters")
    parser.add_argument("symbols", nargs="+", help="IBKR symbols, e.g. AAPL NVDA")
    args = parser.parse_args()

    rules = load_rules()
    out = []
    for symbol in args.symbols:
        evaluation = evaluate_symbol(symbol, rules=rules)
        signal = evaluation_to_signal(evaluation, rules=rules)
        out.append(
            {
                "evaluation": evaluation.model_dump(mode="json"),
                "signal": signal.model_dump(mode="json") if signal else None,
            }
        )
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
