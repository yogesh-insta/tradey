"""Signal Generator orchestration: watchlist → filters → NormalizedSignal list."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from packages.contracts.signals import FilterEvaluation, NormalizedSignal
from services.signal_generator.filters import evaluate_symbol, evaluation_to_signal
from services.signal_generator.prefilter import read_watchlist
from services.signal_generator.rules import StrategyRules, load_rules


@dataclass
class ScanResult:
    evaluations: list[FilterEvaluation] = field(default_factory=list)
    signals: list[NormalizedSignal] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return len(self.signals)


def scan_watchlist(
    symbols: Optional[list[str]] = None,
    *,
    rules: Optional[StrategyRules] = None,
    watchlist_path=None,
) -> ScanResult:
    """Evaluate each watchlist symbol; emit NormalizedSignal for passers only."""
    rules = rules or load_rules()
    tickers = symbols if symbols is not None else read_watchlist(watchlist_path)
    result = ScanResult()
    for symbol in tickers:
        evaluation = evaluate_symbol(symbol, rules=rules)
        result.evaluations.append(evaluation)
        signal = evaluation_to_signal(evaluation, rules=rules)
        if signal is not None:
            result.signals.append(signal)
    return result
