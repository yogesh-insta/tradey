"""Thin market mode switch: MARKET=us|asx → paths, TZ, Yahoo/IBKR defaults.

One shared Signal / Risk / Executor / PM / session_runner stack.
Default is **us** so existing overnight soak stays intact.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]

VALID_MARKETS = frozenset({"us", "asx"})

# IBKR API client ids — must differ if both modes ever touch the same Gateway.
DEFAULT_EXEC_CLIENT_ID_US = 18
DEFAULT_EXEC_CLIENT_ID_ASX = 19


@dataclass(frozen=True)
class MarketProfile:
    """Resolved settings for one MARKET mode (never dual-market in one process)."""

    market: str
    timezone: str
    rules_path: Path
    data_dir: Path
    watchlist_path: Path
    open_positions_path: Path
    closed_trades_path: Path
    run_dir: Path
    status_path: Path
    runner_pid_path: Path
    yahoo_suffix: str
    currency: str
    ibkr_exchange: str
    ibkr_primary_exchange: Optional[str]
    exec_client_id: int
    market_open_hhmm: str = "10:00"
    market_close_hhmm: str = "16:00"
    # Prefilter defaults (overridden by rules when present)
    default_min_gap_pct: float = 3.0
    default_min_price: float = 3.0

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def to_yahoo(self, ibkr_symbol: str) -> str:
        """IBKR symbol → Yahoo chart symbol (e.g. BHP → BHP.AX)."""
        base = ibkr_symbol.strip().replace(" ", "-")
        if not self.yahoo_suffix:
            return base
        if base.upper().endswith(self.yahoo_suffix.upper()):
            return base
        return f"{base}{self.yahoo_suffix}"

    def universe(self) -> list[str]:
        if self.market == "asx":
            from services.signal_generator.universe.asx200_tickers import ASX200_TICKERS

            return list(ASX200_TICKERS)
        from services.signal_generator.universe.sp500_tickers import SP500_TICKERS

        return list(SP500_TICKERS)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)


_active: Optional[MarketProfile] = None


def normalize_market(name: Optional[str]) -> str:
    raw = (name or os.getenv("MARKET") or "us").strip().lower()
    if raw not in VALID_MARKETS:
        raise ValueError(f"MARKET must be us|asx, got {name!r}")
    return raw


def _exec_client_id(market: str) -> int:
    if market == "asx":
        raw = os.getenv("IBKR_EXEC_CLIENT_ID_ASX", "").strip()
        if raw:
            return int(raw)
        # Do not reuse bare IBKR_EXEC_CLIENT_ID (US default) — collision risk.
        return DEFAULT_EXEC_CLIENT_ID_ASX
    raw = os.getenv("IBKR_EXEC_CLIENT_ID_US", "").strip()
    if raw:
        return int(raw)
    # Bare IBKR_EXEC_CLIENT_ID is the US operator default — ignore when MARKET=asx
    # was exported by a prior activate_market("asx") in the same process.
    if os.getenv("MARKET", "us").strip().lower() == "us":
        raw = os.getenv("IBKR_EXEC_CLIENT_ID", "").strip()
        if raw:
            return int(raw)
    return DEFAULT_EXEC_CLIENT_ID_US


def resolve_market(name: Optional[str] = None) -> MarketProfile:
    """Build profile for MARKET (default us). Does not mutate process env."""
    market = normalize_market(name)
    data_dir = ROOT / "data" / market
    run_dir = data_dir / "run"

    if market == "asx":
        profile = MarketProfile(
            market="asx",
            timezone="Australia/Sydney",
            rules_path=ROOT / "rules.asx.json",
            data_dir=data_dir,
            watchlist_path=data_dir / "watchlist.txt",
            open_positions_path=data_dir / "open_positions.json",
            closed_trades_path=data_dir / "closed_trades.jsonl",
            run_dir=run_dir,
            status_path=run_dir / "status.json",
            runner_pid_path=run_dir / "runner.pid",
            yahoo_suffix=".AX",
            currency="AUD",
            ibkr_exchange="SMART",
            ibkr_primary_exchange="ASX",
            exec_client_id=_exec_client_id("asx"),
            market_open_hhmm="10:00",
            market_close_hhmm="16:00",
            default_min_gap_pct=2.5,
            default_min_price=1.0,
        )
    else:
        profile = MarketProfile(
            market="us",
            timezone="America/New_York",
            rules_path=ROOT / "rules.json",
            data_dir=data_dir,
            watchlist_path=data_dir / "watchlist.txt",
            open_positions_path=data_dir / "open_positions.json",
            closed_trades_path=data_dir / "closed_trades.jsonl",
            run_dir=run_dir,
            status_path=run_dir / "status.json",
            runner_pid_path=run_dir / "runner.pid",
            yahoo_suffix="",
            currency="USD",
            ibkr_exchange="SMART",
            ibkr_primary_exchange=None,
            exec_client_id=_exec_client_id("us"),
            market_open_hhmm="10:00",
            market_close_hhmm="16:00",
            default_min_gap_pct=3.0,
            default_min_price=3.0,
        )
    return profile


def migrate_legacy_us_paths(profile: MarketProfile) -> None:
    """One-time copy of flat US paths into data/us/ when missing (no overwrite)."""
    if profile.market != "us":
        return
    profile.ensure_dirs()
    legacy_pairs = [
        (ROOT / "watchlist.txt", profile.watchlist_path),
        (ROOT / "data" / "open_positions.json", profile.open_positions_path),
        (ROOT / "data" / "closed_trades.jsonl", profile.closed_trades_path),
        (ROOT / "data" / "run" / "status.json", profile.status_path),
    ]
    for src, dst in legacy_pairs:
        if src.is_file() and not dst.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())


def apply_profile_env(profile: MarketProfile) -> None:
    """Export path / MARKET env so PositionStore / bot_status / closed_trades resolve correctly."""
    os.environ["MARKET"] = profile.market
    os.environ["MANAGED_POSITIONS_PATH"] = str(profile.open_positions_path)
    os.environ["BOT_STATUS_PATH"] = str(profile.status_path)
    os.environ["BOT_RUNNER_PID_PATH"] = str(profile.runner_pid_path)
    os.environ["CLOSED_TRADES_PATH"] = str(profile.closed_trades_path)
    os.environ["IBKR_EXEC_CLIENT_ID"] = str(profile.exec_client_id)


def clear_profile_env() -> None:
    """Remove profile-exported keys (tests). Leaves explicit operator .env alone if never applied."""
    for key in (
        "MARKET",
        "MANAGED_POSITIONS_PATH",
        "BOT_STATUS_PATH",
        "BOT_RUNNER_PID_PATH",
        "CLOSED_TRADES_PATH",
    ):
        os.environ.pop(key, None)


def get_market_profile() -> MarketProfile:
    global _active
    if _active is None:
        _active = resolve_market()
        _active.ensure_dirs()
        migrate_legacy_us_paths(_active)
    return _active


def set_market_profile(profile: Optional[MarketProfile]) -> None:
    """Override active profile (tests / CLI). Pass None to clear."""
    global _active
    _active = profile
    if profile is None:
        return
    profile.ensure_dirs()
    migrate_legacy_us_paths(profile)


def activate_market(name: Optional[str] = None) -> MarketProfile:
    """Resolve, migrate, export env, and set as active profile."""
    profile = resolve_market(name)
    profile.ensure_dirs()
    migrate_legacy_us_paths(profile)
    apply_profile_env(profile)
    set_market_profile(profile)
    return profile
