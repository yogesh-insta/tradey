#!/usr/bin/env python3
"""
Day-cycle runner (plan 07 + 09 dual-mode).

MARKET=us|asx (default us). Same Signal / Risk / Executor / PM stack;
profile selects TZ, rules, universe, Yahoo map, IBKR contract defaults,
and isolated data/{us,asx}/ paths.

Default is --dry-run (no IBKR orders). Gateway not required for off-hours
early-exit or dry-run decision ticks (Yahoo marks for manage when open).

  python scripts/session_runner.py --once --dry-run
  python scripts/session_runner.py --market asx --once --dry-run
  MARKET=asx python scripts/session_runner.py --dry-run --interval 60
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from packages.contracts.events import TradeEvent
from packages.ledger import Ledger
from packages.notify import notify
from packages.observability.bot_status import (
    watchlist_symbol_count,
    write_status,
)
from services.market_profile import MarketProfile, activate_market
from services.order_executor.execution_service import ExecutionService
from services.order_executor.paper_guard import check_paper_guard
from services.position_manager.state import PositionStore
from services.risk_manager.capital import CapitalConfig
from services.risk_manager.policy import classify_session
from services.session.entry_tick import run_entry_tick
from services.session.manage_tick import run_manage_tick
from services.session.prefilter_tick import run_prefilter_tick, stale_hours_from_env
from services.signal_generator.rules import load_rules, rules_version_stamp


def _heartbeat(
    summary: dict[str, Any],
    *,
    dry_run: bool,
    profile: MarketProfile,
    watchlist_path: Optional[Path] = None,
) -> None:
    """Write per-market status.json so the dashboard can show Running/Stale."""
    open_managed = summary.get("open_managed")
    if open_managed is None:
        try:
            open_count = len(PositionStore(profile.open_positions_path).load())
        except Exception:  # noqa: BLE001
            open_count = 0
    else:
        open_count = len(open_managed)
    phase = str(summary.get("session") or "unknown")
    err = summary.get("error")
    last_error = str(err)[:400] if err else None
    if last_error is None:
        pf = summary.get("prefilter") or {}
        if pf.get("ran") and pf.get("success") is False and pf.get("error"):
            last_error = f"prefilter: {pf['error']}"[:400]
    wl = watchlist_path or profile.watchlist_path
    try:
        write_status(
            phase=phase,
            dry_run=dry_run,
            open_count=open_count,
            watchlist_count=watchlist_symbol_count(wl),
            last_error=last_error,
            path=profile.status_path,
            market=profile.market,
        )
    except OSError as exc:
        print(f"status heartbeat write failed: {exc}", file=sys.stderr)


def run_once(
    *,
    dry_run: bool,
    profile: MarketProfile,
    symbols: Optional[list[str]] = None,
    open_positions: int = 0,
    day_pnl: float = 0.0,
    portfolio_value: Optional[float] = None,
    skip_prefilter: bool = False,
    watchlist_path: Optional[Path] = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    rules = load_rules(profile.rules_path)
    capital = CapitalConfig.from_env()
    session = classify_session(
        time_filter=rules.time_filter,
        tz=profile.tz,
        market_open_hhmm=profile.market_open_hhmm,
        market_close_hhmm=profile.market_close_hhmm,
    )
    ledger = Ledger()
    store = PositionStore(profile.open_positions_path)
    wl_path = watchlist_path or profile.watchlist_path

    summary: dict[str, Any] = {
        "market": profile.market,
        "session": session.code,
        "allow_new_entries": session.allow_new_entries,
        "dry_run": dry_run,
        "rules_version": rules_version_stamp(rules),
        "portfolio_value_aud": capital.portfolio_value_aud,
        "currency": profile.currency,
        "prefilter": None,
        "manage_actions": [],
        "entries": None,
        "early_exit": False,
    }

    if session.code in {"weekend", "closed"}:
        summary["early_exit"] = True
        summary["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        ledger.append(
            TradeEvent(
                event_type="info",
                message="session_tick",
                metadata={
                    "channel": "session_tick",
                    "market": profile.market,
                    "session_code": session.code,
                    "duration_ms": summary["duration_ms"],
                    "early_exit": True,
                    "dry_run": dry_run,
                    "rules_version": summary["rules_version"],
                },
            )
        )
        return summary

    pf = run_prefilter_tick(
        session_code=session.code,
        skip=skip_prefilter,
        watchlist_path=wl_path,
        max_age_hours=stale_hours_from_env(),
        ledger=ledger,
    )
    summary["prefilter"] = pf.to_summary()
    if pf.ran and pf.prefilter is not None and pf.prefilter.success:
        print(
            f"prefilter ran: {pf.prefilter.survivors_count} survivors",
            flush=True,
        )

    if session.code == "too_early":
        summary["early_exit"] = True
        summary["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        ledger.append(
            TradeEvent(
                event_type="info",
                message="session_tick",
                metadata={
                    "channel": "session_tick",
                    "market": profile.market,
                    "session_code": session.code,
                    "duration_ms": summary["duration_ms"],
                    "early_exit": True,
                    "dry_run": dry_run,
                    "prefilter_ran": pf.ran,
                    "rules_version": summary["rules_version"],
                },
            )
        )
        return summary

    exec_svc: Optional[ExecutionService] = None
    snap = None
    fill_ids: list[str] = []

    if not dry_run:
        guard = check_paper_guard()
        if not guard.ok:
            summary["error"] = guard.message
            notify("Session blocked", guard.message, "high")
            return summary
        try:
            exec_svc = ExecutionService(ledger=ledger)
            exec_svc.open()
            snap = exec_svc.safe_portfolio()
            fill_ids = list(exec_svc.adapter.recent_fill_order_ids(hours=1.0))
        except SystemExit as exc:
            summary["error"] = str(exc)
            notify("Session blocked", str(exc), "high")
            return summary
        except Exception as exc:  # noqa: BLE001
            summary["error"] = str(exc)
            notify("Session error", str(exc)[:400], "high")
            return summary

    try:
        force = session.code == "force_close"
        has_open = bool(store.load()) or (snap is not None and snap.open_position_count > 0)
        if force or has_open or session.code in {"ok", "manage_only"}:
            if force or has_open:
                summary["manage_actions"] = run_manage_tick(
                    rules=rules,
                    store=store,
                    dry_run=dry_run,
                    force_close=force,
                    snap=snap,
                    exec_svc=exec_svc,
                    ledger=ledger,
                    recent_fill_order_ids=fill_ids,
                )

        if session.code == "ok" and not force:
            entry = run_entry_tick(
                rules=rules,
                capital=capital,
                dry_run=dry_run,
                symbols=symbols,
                snap=snap,
                exec_svc=exec_svc if not dry_run else None,
                store=store,
                session_code=session.code,
                open_positions_arg=open_positions,
                day_pnl_arg=day_pnl,
                portfolio_usd_cli=portfolio_value,
                ledger=ledger,
                watchlist_path=wl_path,
            )
            summary["entries"] = {
                "evaluated": entry.evaluated,
                "filter_passed": entry.filter_passed,
                "risk_approved": entry.risk_approved,
                "risk_rejected": entry.risk_rejected,
                "executed": entry.executed,
                "intents": [i.model_dump(mode="json") for i in entry.intents],
                "rejections": entry.rejections,
                "managed_opened": entry.managed_opened,
                "sizing_portfolio_usd": entry.portfolio_usd,
            }
    finally:
        if exec_svc is not None:
            exec_svc.close()

    summary["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    summary["open_managed"] = [p.symbol for p in store.load()]
    ledger.append(
        TradeEvent(
            event_type="info",
            message="session_tick",
            metadata={
                "channel": "session_tick",
                "market": profile.market,
                "session_code": session.code,
                "duration_ms": summary["duration_ms"],
                "entries_attempted": (
                    (summary.get("entries") or {}).get("risk_approved")
                    if summary.get("entries")
                    else 0
                ),
                "manage_actions": len(summary.get("manage_actions") or []),
                "prefilter_ran": bool((summary.get("prefilter") or {}).get("ran")),
                "dry_run": dry_run,
                "rules_version": summary["rules_version"],
                "as_of_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Session day-cycle runner (MARKET=us|asx). Auto-refreshes watchlist "
            "via gap prefilter when missing/stale. Default --dry-run skips IBKR "
            "orders but still writes the market watchlist."
        )
    )
    parser.add_argument(
        "--market",
        choices=["us", "asx"],
        default=None,
        help="Market mode (default: MARKET env or us)",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Default true: decisions only; no IBKR orders (prefilter still writes watchlist)",
    )
    parser.add_argument("--once", action="store_true", help="Single tick then exit")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--skip-prefilter", action="store_true")
    parser.add_argument("--watchlist", type=Path, default=None)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--open-positions", type=int, default=0)
    parser.add_argument("--day-pnl", type=float, default=0.0)
    parser.add_argument("--portfolio-value", type=float, default=None)
    args = parser.parse_args()

    profile = activate_market(args.market)

    def _tick() -> int:
        summary = run_once(
            dry_run=args.dry_run,
            profile=profile,
            symbols=args.symbols,
            open_positions=args.open_positions,
            day_pnl=args.day_pnl,
            portfolio_value=args.portfolio_value,
            skip_prefilter=args.skip_prefilter,
            watchlist_path=args.watchlist,
        )
        _heartbeat(
            summary,
            dry_run=args.dry_run,
            profile=profile,
            watchlist_path=args.watchlist,
        )
        print(json.dumps(summary, indent=2, default=str))
        if summary.get("error"):
            return 1
        return 0

    if args.once:
        return _tick()

    print(
        json.dumps(
            {
                "mode": "loop",
                "market": profile.market,
                "interval": args.interval,
                "dry_run": args.dry_run,
                "skip_prefilter": args.skip_prefilter,
                "watchlist": str(args.watchlist or profile.watchlist_path),
                "hint": "Ctrl+C to stop; use caffeinate for soak",
            },
            indent=2,
        )
    )
    while True:
        try:
            code = _tick()
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            print(f"tick crashed (continuing): {err}", file=sys.stderr, flush=True)
            try:
                write_status(
                    phase="tick_error",
                    dry_run=args.dry_run,
                    watchlist_count=watchlist_symbol_count(
                        args.watchlist or profile.watchlist_path
                    ),
                    last_error=err[:400],
                    path=profile.status_path,
                    market=profile.market,
                )
            except OSError as write_exc:
                print(f"status heartbeat write failed: {write_exc}", file=sys.stderr)
            try:
                notify("Session tick crashed", err[:400], "high")
            except Exception:  # noqa: BLE001
                pass
            code = 1
        if code != 0 and not args.dry_run:
            pass
        time.sleep(max(5, int(args.interval)))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
        raise SystemExit(0)
