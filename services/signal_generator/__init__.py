"""Signal Generator package — universe, prefilter, filters → NormalizedSignal."""

from services.signal_generator.evaluate import ScanResult, scan_watchlist
from services.signal_generator.filters import evaluate_symbol, evaluation_to_signal
from services.signal_generator.prefilter import PrefilterResult, run_prefilter
from services.signal_generator.rules import StrategyRules, load_rules

__all__ = [
    "StrategyRules",
    "load_rules",
    "run_prefilter",
    "PrefilterResult",
    "evaluate_symbol",
    "evaluation_to_signal",
    "scan_watchlist",
    "ScanResult",
]
