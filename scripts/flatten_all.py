#!/usr/bin/env python3
"""Flatten all non-zero stock positions on paper IB Gateway (:4002).

Hard requirements: PAPER_TRADING + paper_guard + port 4002.
Cancels open orders per symbol, then market flatten; if unfilled (e.g. outside
RTH), falls back to an aggressive limit with outsideRth=True.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from ib_insync import LimitOrder, MarketOrder, Stock

from packages.contracts import TradeEvent
from packages.contracts.execution import ExecutionResult
from packages.ledger import Ledger
from packages.notify import notify
from services.order_executor.brokers.ibkr import IbkrAdapter
from services.order_executor.config import ExecutorConfig
from services.order_executor.paper_guard import PAPER_PORTS, check_paper_guard


def _is_paper_account(account_id: Optional[str]) -> bool:
    if not account_id:
        return False
    return account_id.upper().startswith(("DU", "DF"))


def _connect_with_fallback(cfg: ExecutorConfig) -> IbkrAdapter:
    primary = cfg.client_id
    candidates = [primary] + [i for i in range(primary + 1, primary + 12) if i != primary]
    candidates += [i for i in range(max(1, primary - 5), primary) if i not in candidates]

    last_exc: Exception | None = None
    for cid in candidates:
        adapter = IbkrAdapter(
            ExecutorConfig(
                broker=cfg.broker,
                host=cfg.host,
                port=cfg.port,
                client_id=cid,
                paper_trading=cfg.paper_trading,
                fill_timeout_sec=cfg.fill_timeout_sec,
                limit_slippage_pct=cfg.limit_slippage_pct,
            )
        )
        try:
            print(f"Connecting {cfg.host}:{cfg.port} clientId={cid} ...", flush=True)
            adapter.connect()
            if cid != primary:
                print(f"Note: primary clientId={primary} busy; using {cid}", flush=True)
            return adapter
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc).lower()
            if "already in use" in msg or "client id" in msg or "clientid" in msg:
                print(f"clientId={cid} in use, trying next...", flush=True)
                continue
            raise
    raise RuntimeError(f"Could not connect (last error: {last_exc})")


def _last_price(adapter: IbkrAdapter, contract: Stock) -> Optional[float]:
    ticker = adapter._ib.reqMktData(contract, "", False, False)  # noqa: SLF001
    try:
        for _ in range(40):
            adapter._ib.sleep(0.25)  # noqa: SLF001
            for candidate in (
                ticker.marketPrice(),
                ticker.last,
                ticker.close,
                ticker.bid,
                ticker.ask,
            ):
                if candidate and candidate == candidate and float(candidate) > 0:
                    return float(candidate)
    finally:
        adapter._ib.cancelMktData(contract)  # noqa: SLF001
    return None


def _cancel_opens(adapter: IbkrAdapter, symbol: str) -> int:
    n = 0
    for trade in adapter.open_orders_for(symbol):
        try:
            adapter._ib.cancelOrder(trade.order)  # noqa: SLF001
            n += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  warn cancel {symbol}: {exc}", flush=True)
    if n:
        adapter._ib.sleep(0.5)  # noqa: SLF001
    return n


def _place_and_wait(
    adapter: IbkrAdapter,
    contract: Stock,
    order,
    *,
    timeout_sec: float,
) -> ExecutionResult:
    trade = adapter._ib.placeOrder(contract, order)  # noqa: SLF001
    filled = adapter._wait_fill(trade, timeout_sec)  # noqa: SLF001
    avg = float(trade.orderStatus.avgFillPrice or 0) or None
    return ExecutionResult(
        intent_id=f"flatten:{contract.symbol}:{uuid4()}",
        symbol=contract.symbol,
        ok=filled > 0,
        status=trade.orderStatus.status,
        filled_qty=float(filled),
        avg_fill_price=avg,
        order_id=str(trade.order.orderId),
        message=f"{order.orderType} {order.action}",
        metadata={"action_kind": "flatten", "order_type": order.orderType},
    )


def flatten_position(adapter: IbkrAdapter, symbol: str, qty: float) -> ExecutionResult:
    """Cancel opens → market → aggressive outsideRTH limit if needed."""
    _cancel_opens(adapter, symbol)

    signed = float(qty)
    abs_qty = int(abs(signed))
    if abs_qty < 1:
        return ExecutionResult(
            intent_id=f"flatten:{symbol}:{uuid4()}",
            symbol=symbol,
            ok=True,
            status="flat",
            filled_qty=0.0,
            message="already flat",
            metadata={"action_kind": "flatten"},
        )

    action = "SELL" if signed > 0 else "BUY"
    contract = Stock(symbol, "SMART", "USD")
    adapter._ib.qualifyContracts(contract)  # noqa: SLF001
    timeout = float(adapter.config.fill_timeout_sec)

    # 1) Market (RTH-friendly)
    mkt = MarketOrder(action, abs_qty)
    mkt.tif = "DAY"
    mkt.outsideRth = True
    result = _place_and_wait(adapter, contract, mkt, timeout_sec=min(timeout, 15.0))
    if result.ok and (result.filled_qty or 0) >= abs_qty:
        result.message = f"market {action}"
        return result

    # Cancel residual market if still working
    if result.order_id and result.status not in {"Filled", "Cancelled", "Inactive"}:
        for trade in adapter._ib.openTrades():  # noqa: SLF001
            if str(trade.order.orderId) == str(result.order_id):
                try:
                    adapter._ib.cancelOrder(trade.order)  # noqa: SLF001
                except Exception:  # noqa: BLE001
                    pass
        adapter._ib.sleep(0.5)  # noqa: SLF001

    remaining = abs_qty - int(result.filled_qty or 0)
    if remaining < 1:
        result.ok = True
        return result

    # 2) Aggressive limit + outsideRth (paper_buy_one style)
    px = _last_price(adapter, contract)
    if not px:
        px = 1.0
    slip = float(adapter.config.limit_slippage_pct) / 100.0
    # Sell well below mark / buy well above mark to compel fill.
    if action == "SELL":
        limit_px = round(px * (1.0 - max(slip, 0.05)), 2)
    else:
        limit_px = round(px * (1.0 + max(slip, 0.05)), 2)
    limit_px = max(limit_px, 0.01)

    lmt = LimitOrder(action, remaining, limit_px)
    lmt.tif = "DAY"
    lmt.outsideRth = True
    print(
        f"  market unfilled ({result.status}); aggressive LMT {action} "
        f"{remaining} @ {limit_px} (mark≈{px})",
        flush=True,
    )
    lim_result = _place_and_wait(adapter, contract, lmt, timeout_sec=timeout)
    lim_result.filled_qty = float(lim_result.filled_qty or 0) + float(result.filled_qty or 0)
    lim_result.ok = (lim_result.filled_qty or 0) >= abs_qty * 0.99
    lim_result.message = (
        f"aggressive LMT {action} @ {limit_px} after market={result.status}"
    )
    lim_result.metadata = {
        **(lim_result.metadata or {}),
        "action_kind": "flatten",
        "market_status": result.status,
        "limit_px": limit_px,
        "mark": px,
    }
    return lim_result


def run(*, dry_run: bool = False) -> int:
    port = int(os.getenv("IBKR_PORT", "4002"))
    paper = os.getenv("PAPER_TRADING", "true").strip().lower() in {"1", "true", "yes"}

    guard = check_paper_guard(port=port, paper_trading=paper)
    if not guard.ok:
        print(guard.message, flush=True)
        return 1
    if not paper:
        print("ABORT: PAPER_TRADING must be true for flatten_all", flush=True)
        return 1
    if port not in PAPER_PORTS or port != 4002:
        print(
            f"ABORT: expected Gateway paper port 4002, got {port} "
            f"(allowed paper ports {sorted(PAPER_PORTS)})",
            flush=True,
        )
        return 1

    cfg = ExecutorConfig.from_env()
    if cfg.port != 4002:
        print(f"ABORT: ExecutorConfig.port={cfg.port} != 4002", flush=True)
        return 1

    ledger = Ledger()
    adapter: IbkrAdapter | None = None
    summary: list[dict] = []

    try:
        adapter = _connect_with_fallback(cfg)
        snap = adapter.portfolio()
        account = snap.account_id
        paperish = _is_paper_account(account)
        print(
            f"Connected account={account} port={cfg.port} "
            f"paper_flag={paper} account_looks_paper={paperish} "
            f"positions={snap.open_position_count} NL={snap.net_liquidation_usd}",
            flush=True,
        )
        if account and not paperish:
            print(
                f"ABORT: account {account} does not look like IBKR paper (DU*/DF*). "
                "Refusing flatten.",
                flush=True,
            )
            notify(
                "flatten_all ABORT",
                f"non-paper-looking account={account} port={cfg.port}",
                "high",
            )
            return 1

        ledger.append(
            TradeEvent(
                event_type="info",
                account_id=account,
                message="flatten_all start",
                metadata={
                    "port": cfg.port,
                    "client_id": adapter.config.client_id,
                    "dry_run": dry_run,
                    "position_count": snap.open_position_count,
                },
            )
        )
        notify(
            "flatten_all start",
            f"account={account} port={cfg.port} n={snap.open_position_count}"
            + (" [dry-run]" if dry_run else ""),
            "high",
        )

        stock_positions = [p for p in snap.positions if abs(float(p.qty or 0)) >= 1]
        if not stock_positions:
            print("No non-zero positions — already flat.", flush=True)
            ledger.append(
                TradeEvent(
                    event_type="info",
                    account_id=account,
                    message="flatten_all already flat",
                )
            )
            notify("flatten_all", "already flat", "default")
            return 0

        for pos in stock_positions:
            symbol = pos.symbol
            qty = float(pos.qty)
            side = "SELL" if qty > 0 else "BUY"
            row = {
                "symbol": symbol,
                "qty_before": qty,
                "ok": False,
                "status": "",
                "filled": 0.0,
                "avg": None,
                "message": "",
            }
            print(f"Flatten {symbol} qty={qty:g} ...", flush=True)

            if dry_run:
                row["ok"] = True
                row["status"] = "dry_run"
                row["message"] = "would flatten"
                summary.append(row)
                continue

            try:
                result = flatten_position(adapter, symbol, qty)
                row["ok"] = bool(result.ok)
                row["status"] = result.status
                row["filled"] = float(result.filled_qty or 0)
                row["avg"] = result.avg_fill_price
                row["message"] = result.message or ""

                ledger.append(
                    TradeEvent(
                        event_type="order_submitted",
                        account_id=account,
                        symbol=symbol,
                        side=side,
                        qty=abs(qty),
                        status=result.status,
                        raw_ref=result.order_id,
                        message=f"flatten_all {result.message}",
                        metadata={
                            "action_kind": "flatten",
                            "intent_id": result.intent_id,
                            **(result.metadata or {}),
                        },
                    )
                )
                if result.ok and result.filled_qty and result.filled_qty > 0:
                    ledger.append(
                        TradeEvent(
                            event_type="fill",
                            account_id=account,
                            symbol=symbol,
                            side=side,
                            qty=result.filled_qty,
                            price=result.avg_fill_price,
                            status=result.status,
                            raw_ref=result.order_id,
                            message="flatten_all fill",
                            metadata={"action_kind": "flatten"},
                        )
                    )
                elif not result.ok:
                    ledger.append(
                        TradeEvent(
                            event_type="error",
                            account_id=account,
                            symbol=symbol,
                            side=side,
                            status=result.status,
                            message=result.message,
                            raw_ref=result.order_id,
                            metadata={"action_kind": "flatten"},
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                row["status"] = "error"
                row["message"] = str(exc)
                print(f"  ERROR {symbol}: {exc}", flush=True)
                ledger.append(
                    TradeEvent(
                        event_type="error",
                        account_id=account,
                        symbol=symbol,
                        message=str(exc),
                        metadata={"action_kind": "flatten"},
                    )
                )
            summary.append(row)
            print(
                f"  -> ok={row['ok']} status={row['status']} "
                f"filled={row['filled']} avg={row['avg']} ({row['message']})",
                flush=True,
            )

        remaining = []
        if not dry_run and adapter is not None:
            adapter._ib.sleep(1.0)  # noqa: SLF001
            remaining = [
                p for p in adapter.portfolio().positions if abs(float(p.qty or 0)) >= 1
            ]

        print("\n=== flatten_all summary ===", flush=True)
        for row in summary:
            print(
                f"  {row['symbol']}: qty_before={row['qty_before']:g} "
                f"ok={row['ok']} status={row['status']} "
                f"filled={row['filled']} avg={row['avg']}",
                flush=True,
            )
        if remaining:
            print(
                "REMAINING: "
                + ", ".join(f"{p.symbol}={p.qty:g}" for p in remaining),
                flush=True,
            )
        else:
            print("REMAINING: none (flat)", flush=True)

        ok_all = all(r["ok"] for r in summary) and not remaining
        notify(
            "flatten_all done",
            (
                f"account={account} closed={sum(1 for r in summary if r['ok'])}/{len(summary)} "
                f"remaining={len(remaining)}"
            ),
            "high" if (not ok_all or remaining) else "default",
        )
        ledger.append(
            TradeEvent(
                event_type="info",
                account_id=account,
                message="flatten_all done",
                metadata={
                    "ok_all": ok_all,
                    "remaining": [p.symbol for p in remaining],
                    "summary": summary,
                },
            )
        )
        return 0 if ok_all else 1
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: {exc}", flush=True)
        ledger.append(TradeEvent(event_type="error", message=f"flatten_all: {exc}"))
        notify("flatten_all FATAL", str(exc), "high")
        return 1
    finally:
        if adapter is not None:
            adapter.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Flatten all paper IBKR stock positions")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List positions only; do not send orders",
    )
    args = parser.parse_args()
    raise SystemExit(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
