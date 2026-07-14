"""Morning gap prefilter: universe → top survivors watchlist (Yahoo only; no IBKR)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from services.market_profile import get_market_profile
from services.signal_generator.bars import (
    download_daily_bars,
    extract_ticker_frame,
    get_last_yahoo_error,
)
from services.signal_generator.rules import load_rules, min_price_from_rules

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIN_GAP_PCT = 3.0
DEFAULT_MIN_PRICE = 3.0
MAX_SURVIVORS = 20


def default_watchlist_path() -> Path:
    return get_market_profile().watchlist_path


# Back-compat alias for imports that expect a Path constant (resolved at call time via default_watchlist_path).
DEFAULT_WATCHLIST_PATH = ROOT / "data" / "us" / "watchlist.txt"


@dataclass
class GapCandidate:
    symbol: str  # IBKR format
    gap_pct: float
    today_open: float
    today_close: float
    yesterday_close: float


@dataclass
class PrefilterResult:
    success: bool
    total_screened: int
    survivors_count: int
    below_gap: int = 0
    below_price: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0
    survivors: list[GapCandidate] = field(default_factory=list)
    watchlist_path: Optional[str] = None
    error: Optional[str] = None

    def to_summary(self) -> dict:
        return {
            "success": self.success,
            "total_screened": self.total_screened,
            "survivors_count": self.survivors_count,
            "below_gap": self.below_gap,
            "below_price": self.below_price,
            "failed": self.failed,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "top_20_survivors": [
                f"{c.symbol} (+{c.gap_pct:.2f}%)" for c in self.survivors
            ],
            "watchlist_path": self.watchlist_path,
            "error": self.error,
        }


def compute_gap_pct(yesterday_close: float, today_close: float) -> float:
    if yesterday_close <= 0:
        raise ValueError("yesterday_close must be positive")
    return (today_close - yesterday_close) / yesterday_close * 100.0


def run_prefilter(
    *,
    tickers: Optional[list[str]] = None,
    min_gap_pct: float = DEFAULT_MIN_GAP_PCT,
    min_price: float = DEFAULT_MIN_PRICE,
    max_survivors: int = MAX_SURVIVORS,
    watchlist_path: Optional[Path] = None,
    dry_run: bool = False,
    threads: int = 5,
    download_fn=None,
) -> PrefilterResult:
    """Screen universe for gap survivors. Never touches IBKR.

    `download_fn` is injectable for tests (signature matches download_daily_bars).
    """
    started = time.monotonic()
    profile = get_market_profile()
    universe = list(tickers) if tickers is not None else profile.universe()
    out_path = Path(watchlist_path) if watchlist_path else profile.watchlist_path
    fetch = download_fn or download_daily_bars

    if min_gap_pct == DEFAULT_MIN_GAP_PCT or min_price == DEFAULT_MIN_PRICE:
        try:
            rules = load_rules(profile.rules_path)
            if min_gap_pct == DEFAULT_MIN_GAP_PCT:
                min_gap_pct = float(rules.daily_filters.D3_min_gap_pct_from_prior_close)
            if min_price == DEFAULT_MIN_PRICE:
                min_price = min_price_from_rules(rules)
        except Exception:  # noqa: BLE001
            min_gap_pct = profile.default_min_gap_pct if min_gap_pct == DEFAULT_MIN_GAP_PCT else min_gap_pct
            min_price = profile.default_min_price if min_price == DEFAULT_MIN_PRICE else min_price

    yahoo_map = {profile.to_yahoo(t): t for t in universe}
    yahoo_tickers = list(yahoo_map.keys())

    try:
        raw = fetch(yahoo_tickers, period="5d", threads=threads)
    except Exception as exc:  # noqa: BLE001
        return PrefilterResult(
            success=False,
            total_screened=len(universe),
            survivors_count=0,
            failed=len(universe),
            elapsed_seconds=time.monotonic() - started,
            error=f"yahoo download failed: {exc}",
        )

    if raw is None or raw.empty:
        err = get_last_yahoo_error() or "yahoo returned empty dataframe"
        elapsed = time.monotonic() - started
        if not dry_run:
            # Persist a stub so the session runner does not re-hit Yahoo every tick.
            write_failed_watchlist(out_path, err, total=len(universe))
        return PrefilterResult(
            success=False,
            total_screened=len(universe),
            survivors_count=0,
            failed=len(universe),
            elapsed_seconds=elapsed,
            watchlist_path=str(out_path) if not dry_run else None,
            error=err,
        )

    survivors: list[GapCandidate] = []
    below_gap = below_price = failed = 0

    for yahoo_sym, ibkr_sym in yahoo_map.items():
        try:
            frame = extract_ticker_frame(raw, yahoo_sym)
            if frame is None or len(frame) < 2:
                failed += 1
                continue
            yesterday = frame.iloc[-2]
            today = frame.iloc[-1]
            yesterday_close = float(yesterday["Close"])
            today_open = float(today["Open"])
            today_close = float(today["Close"])
            gap_pct = compute_gap_pct(yesterday_close, today_close)
            if today_close < min_price:
                below_price += 1
                continue
            if gap_pct < min_gap_pct:
                below_gap += 1
                continue
            survivors.append(
                GapCandidate(
                    symbol=ibkr_sym,
                    gap_pct=gap_pct,
                    today_open=today_open,
                    today_close=today_close,
                    yesterday_close=yesterday_close,
                )
            )
        except (KeyError, IndexError, ValueError, TypeError):
            failed += 1

    survivors.sort(key=lambda c: c.gap_pct, reverse=True)
    survivors = survivors[:max_survivors]

    result = PrefilterResult(
        success=True,
        total_screened=len(universe),
        survivors_count=len(survivors),
        below_gap=below_gap,
        below_price=below_price,
        failed=failed,
        elapsed_seconds=time.monotonic() - started,
        survivors=survivors,
        watchlist_path=str(out_path) if not dry_run else None,
    )

    if not dry_run:
        write_watchlist(out_path, survivors, min_gap_pct=min_gap_pct, min_price=min_price, total=len(universe))

    _print_degradation_alerts(result)
    return result


def write_watchlist(
    path: Path,
    survivors: list[GapCandidate],
    *,
    min_gap_pct: float,
    min_price: float,
    total: int,
) -> None:
    profile = get_market_profile()
    now = datetime.now(profile.tz).strftime("%Y-%m-%d %H:%M %Z")
    ccy = "$" if profile.currency == "USD" else "A$"
    lines = [
        f"# Auto-generated by morning_prefilter at {now}",
        f"# Market: {profile.market}  Filters: gap >= {min_gap_pct}%, price >= {ccy}{min_price}",
        "# Source: Yahoo chart API (screening only); IBKR handles execution",
        f"# Survivors: {len(survivors)} (capped at {MAX_SURVIVORS}) of {total}",
        "#",
        f"# ticker  # gap +X.XX%  open {ccy}X.XX  prev {ccy}X.XX",
    ]
    for c in survivors:
        lines.append(
            f"{c.symbol}  # gap +{c.gap_pct:.2f}%  open {ccy}{c.today_open:.2f}  "
            f"prev {ccy}{c.yesterday_close:.2f}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_failed_watchlist(path: Path, error: str, *, total: int) -> None:
    """Write an empty watchlist stub after a Yahoo failure (stops tick hammering)."""
    profile = get_market_profile()
    now = datetime.now(profile.tz).strftime("%Y-%m-%d %H:%M %Z")
    safe = error.replace("\n", " ").strip()[:240]
    lines = [
        f"# Auto-generated by morning_prefilter at {now}",
        f"# Market: {profile.market}",
        f"# ERROR: {safe}",
        f"# Survivors: 0 of {total} (download failed; retry after cooldown)",
        "#",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_watchlist(path: Optional[Path] = None) -> list[str]:
    watch_path = Path(path) if path else default_watchlist_path()
    if not watch_path.is_file():
        return []
    symbols: list[str] = []
    for line in watch_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        symbols.append(stripped.split()[0])
    return symbols


def _print_degradation_alerts(result: PrefilterResult) -> None:
    total = max(result.total_screened, 1)
    ratio = result.failed / total
    if ratio >= 0.95:
        print(
            "ALERT: Yahoo-wide failure suspected; check Yahoo chart API / network",
            flush=True,
        )
    elif ratio >= 0.30:
        print(
            f"ALERT: yahoo degradation (failed {result.failed}/{result.total_screened})",
            flush=True,
        )


def result_json(result: PrefilterResult) -> str:
    return json.dumps(result.to_summary(), indent=2)
