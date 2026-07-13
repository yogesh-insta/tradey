"""Load and validate strategy rules.json (Humbled Trader Trend Join Long)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES_PATH = ROOT / "rules.json"


class UniverseFilters(BaseModel):
    index: str = "S&P 500"
    min_price_usd: float = 3.0


class DailyFilters(BaseModel):
    D1_above_prior_day_high: bool = True
    D2_prior_close_above_sma200: bool = True
    D3_min_gap_pct_from_prior_close: float = 3.0


class IntradayFilters(BaseModel):
    I1_above_premarket_high: bool = True
    I2_above_today_hod: bool = True
    I3_rvol_min: float = 2.0
    I3_rvol_lookback_days: int = 14


class TimeFilter(BaseModel):
    earliest_entry_et: str = "10:05"
    latest_entry_et: str = "15:30"
    force_close_et: str = "15:51"


class ExitRules(BaseModel):
    initial_stop_rule: str = "lod_minus_1pct"
    partial_profit_trigger_R: float = 0.75
    partial_profit_fraction: float = 0.3333
    breakeven_trigger_R: float = 1.0
    post_breakeven_trail: str = "swing_low_5m_2_2"


class RiskRules(BaseModel):
    max_risk_per_trade_pct: float = 1.0
    max_position_size_pct_of_portfolio: float = 10
    max_concurrent_positions: int = 5
    # Kill switch: halt new entries when day PnL <= -this % of portfolio.
    max_daily_loss_pct: float = 3.0


class StrategyRules(BaseModel):
    rules_version: str = "unset"
    strategy_name: str = "Trend Join Long"
    direction: str = "long_only"
    trade_timeframe: str = "5m"
    universe_filters: UniverseFilters = Field(default_factory=UniverseFilters)
    daily_filters: DailyFilters = Field(default_factory=DailyFilters)
    intraday_filters: IntradayFilters = Field(default_factory=IntradayFilters)
    time_filter: TimeFilter = Field(default_factory=TimeFilter)
    exit: ExitRules = Field(default_factory=ExitRules)
    risk: RiskRules = Field(default_factory=RiskRules)


def load_rules(path: Optional[Path] = None) -> StrategyRules:
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    if not rules_path.is_file():
        raise FileNotFoundError(f"rules.json not found: {rules_path}")
    return StrategyRules.model_validate_json(rules_path.read_text(encoding="utf-8"))


def rules_version_stamp(rules: Optional[StrategyRules] = None) -> str:
    """Stable rules_version for ledger attribution (plan 08)."""
    r = rules or load_rules()
    if r.rules_version and r.rules_version != "unset":
        return r.rules_version
    return "unset"
