#!/usr/bin/env python3
"""Local smoke test: connect to IB Gateway paper (default 4002)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from ib_insync import IB

from packages.contracts import TradeEvent
from packages.ledger import Ledger
from packages.notify import notify


async def connect(host: str, port: int, client_id: int) -> int:
    ib = IB()
    ledger = Ledger()
    try:
        print(f"Connecting to {host}:{port} clientId={client_id} ...", flush=True)
        await asyncio.wait_for(
            ib.connectAsync(host, port, clientId=client_id, readonly=True),
            timeout=10,
        )
        accounts = ib.managedAccounts()
        print("CONNECTED")
        print(f"  accounts={accounts}")
        print(f"  serverVersion={ib.client.serverVersion()}")
        account = accounts[0] if accounts else None
        ledger.append(
            TradeEvent(
                event_type="connected",
                account_id=account,
                message=f"readonly connect {host}:{port}",
            )
        )
        notify("IBKR connected", f"accounts={accounts}")
        return 0
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        notify("IBKR connect failed", str(exc), priority="high")
        ledger.append(
            TradeEvent(event_type="error", message=f"connect failed: {exc}")
        )
        print(
            "\nIf you saw Error 10141: accept the Paper Trading API disclaimer "
            "in Client Portal / Gateway, then retry.",
            file=sys.stderr,
        )
        return 1
    finally:
        if ib.isConnected():
            ib.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002, help="4002=Gateway paper")
    parser.add_argument("--client-id", type=int, default=7)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(connect(args.host, args.port, args.client_id)))


if __name__ == "__main__":
    main()
