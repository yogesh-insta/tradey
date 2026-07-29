"""Morning gap prefilter: universe → top survivors watchlist (Yahoo only; no IBKR).

Gap definition (opening gap):
  gap% = (today_open - prior_close) / prior_close * 100

Prefer today's daily Open when the last daily bar date matches the market session
date (`as_of` in profile TZ). When the daily chart still lags (common for ASX),
fall back to the first session 5m open vs prior daily close. True pre-open
(no daily session bar and no 5m yet) returns a short-cooldown error stub.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from services.market_profile import get_market_profile
from services.signal_generator.bars import (
    download_daily_bars,
    download_session_opens,
    extract_ticker_frame,
    get_last_yahoo_error,
)
from services.signal_generator.rules import load_rules, min_price_from_rules

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIN_GAP_PCT = 3.0
DEFAULT_MIN_PRICE = 3.0
MAX_SURVIVORS = 20

# session_open_fn(yahoo_tickers, *, session_date, tz) -> {yahoo: (open, last_close)}
SessionOpenFn = Callable[..., dict[str, tuple[float, float]]]


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
    pending_session: int = 0
    open_fallback: int = 0
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
            "pending_session": self.pending_session,
            "open_fallback": self.open_fallback,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "top_20_survivors": [
                f"{c.symbol} (+{c.gap_pct:.2f}%)" for c in self.survivors
            ],
            "watchlist_path": self.watchlist_path,
            "error": self.error,
        }


def compute_gap_pct(prior_close: float, reference_price: float) -> float:
    """Percent change from prior close to reference (open for prefilter)."""
    if prior_close <= 0:
        raise ValueError("prior_close must be positive")
    return (reference_price - prior_close) / prior_close * 100.0


def _index_date(ts, zone) -> date:
    """Calendar date of a pandas/numpy timestamp in `zone`."""
    if hasattr(ts, "to_pydatetime"):
        dt = ts.to_pydatetime()
    else:
        dt = ts
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=zone)
    else:
        dt = dt.astimezone(zone)
    return dt.date()


def _consider_candidate(
    *,
    survivors: list[GapCandidate],
    ibkr_sym: str,
    yesterday_close: float,
    today_open: float,
    today_close: float,
    min_gap_pct: float,
    min_price: float,
) -> str:
    """Apply price/gap filters. Returns 'survivor' | 'below_price' | 'below_gap'."""
    if today_open < min_price:
        return "below_price"
    gap_pct = compute_gap_pct(yesterday_close, today_open)
    if gap_pct < min_gap_pct:
        return "below_gap"
    survivors.append(
        GapCandidate(
            symbol=ibkr_sym,
            gap_pct=gap_pct,
            today_open=today_open,
            today_close=today_close,
            yesterday_close=yesterday_close,
        )
    )
    return "survivor"


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
    session_open_fn: Optional[SessionOpenFn] = None,
    as_of: Optional[datetime] = None,
) -> PrefilterResult:
    """Screen universe for opening-gap survivors. Never touches IBKR.

    `download_fn` is injectable for tests (signature matches download_daily_bars).
    `session_open_fn` is injectable for 5m open fallback (defaults to live Yahoo
    when using real daily download; empty when `download_fn` is injected unless
    `session_open_fn` is passed explicitly).
    `as_of` selects the session date (defaults to now in the market TZ).
    """
    started = time.monotonic()
    profile = get_market_profile()
    universe = list(tickers) if tickers is not None else profile.universe()
    out_path = Path(watchlist_path) if watchlist_path else profile.watchlist_path
    fetch = download_fn or download_daily_bars
    session_date = (as_of or datetime.now(profile.tz)).astimezone(profile.tz).date()

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
    below_gap = below_price = failed = pending_session = open_fallback = 0
    # Symbols whose daily bar is still prior-session — need 5m open.
    pending: list[tuple[str, str, float]] = []  # yahoo, ibkr, prior_close

    for yahoo_sym, ibkr_sym in yahoo_map.items():
        try:
            frame = extract_ticker_frame(raw, yahoo_sym)
            if frame is None or len(frame) < 1:
                failed += 1
                continue
            bar_date = _index_date(frame.index[-1], profile.tz)
            if bar_date == session_date:
                if len(frame) < 2:
                    failed += 1
                    continue
                yesterday = frame.iloc[-2]
                today = frame.iloc[-1]
                yesterday_close = float(yesterday["Close"])
                today_open = float(today["Open"])
                today_close = float(today["Close"])
                outcome = _consider_candidate(
                    survivors=survivors,
                    ibkr_sym=ibkr_sym,
                    yesterday_close=yesterday_close,
                    today_open=today_open,
                    today_close=today_close,
                    min_gap_pct=min_gap_pct,
                    min_price=min_price,
                )
                if outcome == "below_price":
                    below_price += 1
                elif outcome == "below_gap":
                    below_gap += 1
                continue

            # Daily chart lagging: last close is prior session close.
            prior_close = float(frame.iloc[-1]["Close"])
            if prior_close <= 0:
                failed += 1
                continue
            pending.append((yahoo_sym, ibkr_sym, prior_close))
        except (KeyError, IndexError, ValueError, TypeError, AttributeError):
            failed += 1

    if pending:
        opens = _resolve_session_opens(
            [y for y, _, _ in pending],
            session_date=session_date,
            tz=profile.tz,
            download_fn=download_fn,
            session_open_fn=session_open_fn,
        )
        for yahoo_sym, ibkr_sym, prior_close in pending:
            pair = opens.get(yahoo_sym)
            if pair is None:
                pending_session += 1
                continue
            today_open, today_close = pair
            open_fallback += 1
            try:
                outcome = _consider_candidate(
                    survivors=survivors,
                    ibkr_sym=ibkr_sym,
                    yesterday_close=prior_close,
                    today_open=float(today_open),
                    today_close=float(today_close),
                    min_gap_pct=min_gap_pct,
                    min_price=min_price,
                )
            except (ValueError, TypeError):
                failed += 1
                continue
            if outcome == "below_price":
                below_price += 1
            elif outcome == "below_gap":
                below_gap += 1

    # No opens from daily or 5m yet → short cooldown stub, retry after open.
    if pending_session and not survivors and below_gap == 0 and below_price == 0 and failed < len(universe):
        err = (
            f"awaiting today's session open (session={session_date.isoformat()}, "
            f"pending={pending_session}/{len(universe)})"
        )
        elapsed = time.monotonic() - started
        if not dry_run:
            write_failed_watchlist(out_path, err, total=len(universe))
        return PrefilterResult(
            success=False,
            total_screened=len(universe),
            survivors_count=0,
            failed=failed,
            pending_session=pending_session,
            open_fallback=open_fallback,
            elapsed_seconds=elapsed,
            watchlist_path=str(out_path) if not dry_run else None,
            error=err,
        )

    survivors.sort(key=lambda c: c.gap_pct, reverse=True)
    survivors = survivors[:max_survivors]

    result = PrefilterResult(
        success=True,
        total_screened=len(universe),
        survivors_count=len(survivors),
        below_gap=below_gap,
        below_price=below_price,
        failed=failed,
        pending_session=pending_session,
        open_fallback=open_fallback,
        elapsed_seconds=time.monotonic() - started,
        survivors=survivors,
        watchlist_path=str(out_path) if not dry_run else None,
    )

    if not dry_run:
        write_watchlist(out_path, survivors, min_gap_pct=min_gap_pct, min_price=min_price, total=len(universe))

    _print_degradation_alerts(result)
    return result


def _resolve_session_opens(
    yahoo_tickers: list[str],
    *,
    session_date: date,
    tz: ZoneInfo,
    download_fn,
    session_open_fn: Optional[SessionOpenFn],
) -> dict[str, tuple[float, float]]:
    if not yahoo_tickers:
        return {}
    if session_open_fn is not None:
        return session_open_fn(yahoo_tickers, session_date=session_date, tz=tz)
    # Injected daily download in tests: do not hit the network unless opens injected.
    if download_fn is not None:
        return {}
    return download_session_opens(yahoo_tickers, session_date=session_date, tz=tz)


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
        f"# Market: {profile.market}  Filters: open gap >= {min_gap_pct}%, open >= {ccy}{min_price}",
        "# Gap = (open - prior_close) / prior_close; Yahoo screening only; IBKR handles execution",
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
