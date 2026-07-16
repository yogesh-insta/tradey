"""Shared entry-tick logic for signal_cycle CLI and session_runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from packages.contracts.execution import ExecutionResult, PortfolioSnapshot
from packages.contracts.position import ManagedPosition, PositionLifecycle
from packages.contracts.risk import RiskRejection, SizedOrderIntent
from packages.contracts.events import TradeEvent
from packages.ledger import Ledger
from services.order_executor.execution_service import ExecutionService
from services.position_manager.r_math import compute_R
from services.position_manager.state import PositionStore
from services.market_profile import get_market_profile
from services.risk_manager.capital import CapitalConfig, sizing_portfolio
from services.risk_manager.policy import evaluate_signal
from services.signal_generator.evaluate import scan_watchlist
from services.signal_generator.rules import StrategyRules, rules_version_stamp


@dataclass
class EntryTickResult:
    evaluated: int = 0
    filter_passed: int = 0
    risk_approved: int = 0
    risk_rejected: int = 0
    executed: int = 0
    intents: list[SizedOrderIntent] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    executions: list[dict[str, Any]] = field(default_factory=list)
    managed_opened: list[str] = field(default_factory=list)
    portfolio_usd: float = 0.0
    open_count: int = 0
    day_pnl_usd: float = 0.0
    capital_meta: dict[str, Any] = field(default_factory=dict)


def resolve_portfolio_context(
    *,
    capital: CapitalConfig,
    open_positions_arg: int = 0,
    day_pnl_arg: float = 0.0,
    portfolio_usd_cli: Optional[float] = None,
    snap: Optional[PortfolioSnapshot] = None,
) -> tuple[float, int, float, dict[str, Any]]:
    """Return (portfolio_usd, open_count, day_pnl_usd, meta) for Risk.

    For MARKET=asx, sizing units are AUD (A$10k ceiling, no FX).
    """
    currency = get_market_profile().currency
    if snap is not None:
        portfolio_usd = sizing_portfolio(
            broker_net_liquidation=snap.net_liquidation_usd,
            config=capital,
            currency=currency,
        )
        return (
            portfolio_usd,
            snap.open_position_count,
            float(snap.day_pnl_usd or 0.0),
            {
                "source": "broker_snapshot",
                "net_liquidation_usd": snap.net_liquidation_usd,
                "account_id": snap.account_id,
                "cap_usd": capital.portfolio_value_usd_cap,
                "cap_aud": capital.portfolio_value_aud,
                "sizing_currency": currency,
            },
        )

    if portfolio_usd_cli is not None and portfolio_usd_cli > 0:
        cap = (
            capital.portfolio_value_aud
            if currency.upper() == "AUD"
            else capital.portfolio_value_usd_cap
        )
        portfolio_usd = min(portfolio_usd_cli, cap)
    else:
        portfolio_usd = sizing_portfolio(
            broker_net_liquidation=None,
            config=capital,
            currency=currency,
        )
    return (
        portfolio_usd,
        open_positions_arg,
        day_pnl_arg,
        {
            "source": "env_cli",
            "cap_usd": capital.portfolio_value_usd_cap,
            "cap_aud": capital.portfolio_value_aud,
            "sizing_currency": currency,
        },
    )


def managed_from_fill(
    intent: SizedOrderIntent,
    result: ExecutionResult,
    *,
    session_code: str,
    rules: StrategyRules,
    account_id: Optional[str] = None,
) -> ManagedPosition:
    """Build ManagedPosition after a successful entry fill."""
    meta = intent.metadata or {}
    entry = float(result.avg_fill_price or intent.limit_price or 0.0)
    initial_stop = float(intent.stop_price or 0.0)
    R = float(meta.get("R") or compute_R(entry, initial_stop))
    qty = int(result.filled_qty or intent.qty)
    return ManagedPosition(
        trade_id=str(meta.get("trade_id") or intent.intent_id),
        symbol=intent.symbol,
        qty_remaining=qty,
        qty_initial=qty,
        entry_price=entry,
        initial_stop=initial_stop,
        R=R,
        current_stop=initial_stop,
        stop_order_id=result.stop_order_id,
        entry_order_id=result.order_id,
        signal_id=intent.signal_id,
        intent_id=intent.intent_id,
        state=PositionLifecycle.pre_breakeven,
        account_id=account_id,
        rules_version=str(meta.get("rules_version") or rules_version_stamp(rules)),
        session_code_at_entry=str(meta.get("session_code") or session_code),
        filters_fired=dict(meta.get("filters_fired") or {}),
        signal_reasons=list(meta.get("signal_reasons") or []),
        signal_metrics=dict(meta.get("signal_metrics") or {}),
        risk_usd=float(meta["risk_usd"]) if meta.get("risk_usd") is not None else None,
        sizing_portfolio_usd=(
            float(meta["sizing_portfolio_usd"])
            if meta.get("sizing_portfolio_usd") is not None
            else None
        ),
    )


def run_entry_tick(
    *,
    rules: StrategyRules,
    capital: CapitalConfig,
    dry_run: bool,
    symbols: Optional[list[str]] = None,
    snap: Optional[PortfolioSnapshot] = None,
    exec_svc: Optional[ExecutionService] = None,
    store: Optional[PositionStore] = None,
    session_code: str = "ok",
    open_positions_arg: int = 0,
    day_pnl_arg: float = 0.0,
    portfolio_usd_cli: Optional[float] = None,
    ledger: Optional[Ledger] = None,
    watchlist_path=None,
    now=None,
) -> EntryTickResult:
    """Scan watchlist → Risk → optional execute; register managed positions on fill.

    `now` is optional (injected for tests); Risk session gate uses market local clock.
    """
    out = EntryTickResult()
    portfolio_usd, open_count, day_pnl_usd, capital_meta = resolve_portfolio_context(
        capital=capital,
        open_positions_arg=open_positions_arg,
        day_pnl_arg=day_pnl_arg,
        portfolio_usd_cli=portfolio_usd_cli,
        snap=snap,
    )
    out.portfolio_usd = portfolio_usd
    out.open_count = open_count
    out.day_pnl_usd = day_pnl_usd
    out.capital_meta = capital_meta

    # Count already-managed symbols toward concurrency
    store = store or PositionStore()
    managed = store.load()
    managed_symbols = {p.symbol for p in managed}
    effective_open = max(open_count, len(managed))

    scan = scan_watchlist(symbols, rules=rules, watchlist_path=watchlist_path)
    out.evaluated = len(scan.evaluations)
    out.filter_passed = scan.passed_count

    intents: list[SizedOrderIntent] = []
    for signal in scan.signals:
        if signal.symbol in managed_symbols:
            out.rejections.append(
                {
                    "symbol": signal.symbol,
                    "reason": "already_managed",
                    "signal_id": signal.signal_id,
                }
            )
            continue
        metrics = signal.metadata.get("metrics") or {}
        lod = metrics.get("today_low")
        decision = evaluate_signal(
            signal,
            open_position_count=effective_open + len(intents),
            portfolio_value_usd=portfolio_usd,
            low_of_day=float(lod) if lod is not None else None,
            day_pnl_usd=day_pnl_usd,
            rules=rules,
            now=now,
        )
        if isinstance(decision, RiskRejection):
            rej = decision.model_dump(mode="json")
            rej["rules_version"] = rules_version_stamp(rules)
            out.rejections.append(rej)
            if ledger is not None:
                ledger.append(
                    TradeEvent(
                        event_type="info",
                        symbol=decision.symbol,
                        message="risk_reject",
                        metadata={
                            "channel": "risk_reject",
                            "signal_id": decision.signal_id,
                            "reason": decision.reason,
                            "details": decision.details,
                            "rules_version": rules_version_stamp(rules),
                            "session_code": session_code,
                        },
                    )
                )
            continue
        assert isinstance(decision, SizedOrderIntent)
        intents.append(decision)
        if ledger is not None:
            ledger.append(
                TradeEvent(
                    event_type="info",
                    symbol=decision.symbol,
                    message="risk_approve",
                    metadata={
                        "channel": "risk_approve",
                        "signal_id": decision.signal_id,
                        "intent_id": decision.intent_id,
                        "trade_id": (decision.metadata or {}).get("trade_id"),
                        "rules_version": (decision.metadata or {}).get("rules_version"),
                        "session_code": session_code,
                        "qty": decision.qty,
                        "stop_price": decision.stop_price,
                        **{
                            k: (decision.metadata or {}).get(k)
                            for k in (
                                "R",
                                "risk_usd",
                                "sizing_portfolio_usd",
                                "signal_reasons",
                                "filters_fired",
                            )
                        },
                    },
                )
            )

    out.intents = intents
    out.risk_approved = len(intents)
    out.risk_rejected = len(out.rejections)

    if dry_run or not intents:
        return out

    assert exec_svc is not None
    account_id = snap.account_id if snap else None
    for intent in intents:
        result = exec_svc.execute_intent(intent)
        out.executions.append(result.model_dump(mode="json"))
        if result.ok and result.filled_qty > 0:
            out.executed += 1
            effective_open += 1
            pos = managed_from_fill(
                intent,
                result,
                session_code=session_code,
                rules=rules,
                account_id=account_id,
            )
            store.upsert(pos)
            out.managed_opened.append(pos.symbol)
            managed_symbols.add(pos.symbol)

    return out
