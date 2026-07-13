#!/usr/bin/env python3
"""
Local paper process runner (Phase A).

- Notifies Telegram that the session started
- Connects to IB Gateway paper
- Syncs positions + account into the ledger (dashboard)
- Heartbeats every INTERVAL seconds
- Does NOT place new orders unless --enable-orders (safety)

Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from ib_insync import IB

from packages.contracts import TradeEvent
from packages.ledger import Ledger
from packages.notify import notify

ET = ZoneInfo("America/New_York")


def _ts() -> str:
    return datetime.now(ET).strftime("%H:%M:%S ET")


def sync_once(ib: IB, ledger: Ledger, account: str | None) -> None:
    positions = ib.positions()
    for pos in positions:
        if not pos.position:
            continue
        ledger.append(
            TradeEvent(
                event_type="info",
                account_id=account or pos.account,
                symbol=pos.contract.symbol,
                venue=getattr(pos.contract, "exchange", None),
                currency=getattr(pos.contract, "currency", "USD"),
                qty=float(pos.position),
                price=float(pos.avgCost) if pos.avgCost else None,
                message="position_snapshot",
                metadata={"avg_cost": pos.avgCost},
            )
        )
    # accountValues is sync-friendly; avoid nested async helpers
    tags = {v.tag: v.value for v in ib.accountSummary()}
    net = tags.get("NetLiquidation")
    open_n = len([p for p in positions if p.position])
    ledger.append(
        TradeEvent(
            event_type="info",
            account_id=account,
            message=f"heartbeat net_liq={net} positions={open_n}",
            metadata={"net_liquidation": net, "buying_power": tags.get("BuyingPower")},
        )
    )


def run(interval: int, enable_orders: bool) -> int:
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))
    client_id = int(os.getenv("IBKR_CLIENT_ID", "20"))
    paper = os.getenv("PAPER_TRADING", "true").lower() in {"1", "true", "yes"}

    if paper and port in {4001, 7496}:
        print("ABORT: paper flag with live port")
        return 1
    if enable_orders:
        print("WARNING: --enable-orders is set; still no auto entries in this runner yet.")

    ledger = Ledger()
    ib = IB()
    account = None

    notify(
        "tradey process started",
        f"paper={paper} {host}:{port} interval={interval}s\nDashboard: http://127.0.0.1:8080",
    )
    ledger.append(
        TradeEvent(
            event_type="info",
            message="local process started",
            metadata={"interval": interval, "enable_orders": enable_orders},
        )
    )

    try:
        print(f"[{_ts()}] connecting {host}:{port} ...")
        ib.connect(host, port, clientId=client_id)
        accounts = ib.managedAccounts()
        account = accounts[0] if accounts else None
        print(f"[{_ts()}] connected accounts={accounts}")
        notify("IBKR connected", f"account={account} port={port}")
        ledger.append(
            TradeEvent(
                event_type="connected",
                account_id=account,
                message=f"process connect {host}:{port}",
            )
        )

        while True:
            sync_once(ib, ledger, account)
            positions = [p for p in ib.positions() if p.position]
            print(
                f"[{_ts()}] heartbeat positions={len(positions)} "
                f"symbols={[p.contract.symbol for p in positions]}"
            )
            ib.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n[{_ts()}] stopping...")
        notify("tradey process stopped", "Local runner shut down (KeyboardInterrupt).")
        ledger.append(TradeEvent(event_type="info", account_id=account, message="process stopped"))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[{_ts()}] ERROR: {exc}")
        notify("tradey process ERROR", str(exc)[:500], priority="high")
        ledger.append(
            TradeEvent(event_type="error", account_id=account, message=str(exc)[:500])
        )
        return 1
    finally:
        if ib.isConnected():
            ib.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=60, help="Heartbeat seconds")
    parser.add_argument(
        "--enable-orders",
        action="store_true",
        help="Reserved; auto entries not enabled in this runner yet",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.interval, args.enable_orders))


if __name__ == "__main__":
    main()
