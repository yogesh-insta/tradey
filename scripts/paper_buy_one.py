#!/usr/bin/env python3
"""Paper smoke: buy 1 share, log to ledger, notify Telegram."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from ib_insync import IB, LimitOrder, Stock

from packages.contracts import TradeEvent
from packages.ledger import Ledger
from packages.notify import notify


async def run(symbol: str, client_id: int) -> int:
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))
    paper = os.getenv("PAPER_TRADING", "true").lower() in {"1", "true", "yes"}
    if paper and port in {4001, 7496}:
        print("ABORT: PAPER_TRADING=true but live port configured")
        return 1

    ledger = Ledger()
    ib = IB()
    try:
        await ib.connectAsync(host, port, clientId=client_id)
        accounts = ib.managedAccounts()
        account = accounts[0] if accounts else None
        notify("IBKR connected", f"account={account} port={port}")
        ledger.append(
            TradeEvent(
                event_type="connected",
                account_id=account,
                message=f"connected {host}:{port}",
            )
        )

        ib.reqMarketDataType(3)
        contract = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)

        ticker = ib.reqMktData(contract, "", False, False)
        px = None
        for _ in range(30):
            await asyncio.sleep(0.3)
            for candidate in (ticker.marketPrice(), ticker.last, ticker.close, ticker.bid, ticker.ask):
                if candidate and candidate == candidate:
                    px = float(candidate)
                    break
            if px:
                break
        ib.cancelMktData(contract)
        if not px:
            px = 250.0

        limit_px = round(px * 1.05, 2)
        order = LimitOrder("BUY", 1, limit_px)
        order.tif = "DAY"
        order.outsideRth = True
        trade = ib.placeOrder(contract, order)
        ledger.append(
            TradeEvent(
                event_type="order_submitted",
                account_id=account,
                symbol=symbol,
                side="BUY",
                qty=1,
                price=limit_px,
                status=trade.orderStatus.status,
                raw_ref=str(trade.order.orderId),
                message=f"LMT {limit_px}",
            )
        )
        notify("Order submitted", f"BUY 1 {symbol} LMT {limit_px}")

        for _ in range(90):
            await asyncio.sleep(0.5)
            if trade.isDone():
                break

        status = trade.orderStatus.status
        filled = float(trade.orderStatus.filled or 0)
        avg = float(trade.orderStatus.avgFillPrice or 0)
        print(f"status={status} filled={filled} avg={avg}")

        if status == "Filled" and filled > 0:
            ledger.append(
                TradeEvent(
                    event_type="fill",
                    account_id=account,
                    symbol=symbol,
                    venue="SMART",
                    side="BUY",
                    qty=filled,
                    price=avg,
                    status=status,
                    raw_ref=str(trade.order.orderId),
                )
            )
            notify("Fill", f"BUY {filled:g} {symbol} @ {avg:.2f}")
            return 0

        msg = trade.log[-1].message if trade.log else status
        ledger.append(
            TradeEvent(
                event_type="error",
                account_id=account,
                symbol=symbol,
                side="BUY",
                status=status,
                message=msg,
                raw_ref=str(trade.order.orderId),
            )
        )
        notify("Order failed", f"{symbol}: {msg}", priority="high")
        return 1
    finally:
        if ib.isConnected():
            ib.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--client-id", type=int, default=int(os.getenv("IBKR_CLIENT_ID", "11")))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.symbol, args.client_id)))


if __name__ == "__main__":
    main()
