"""Order executor runtime config (env-backed)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutorConfig:
    broker: str = "ibkr"
    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 18
    paper_trading: bool = True
    fill_timeout_sec: float = 45.0
    limit_slippage_pct: float = 0.5  # aggressive limit above last for BUY fills

    @classmethod
    def from_env(cls) -> "ExecutorConfig":
        paper = os.getenv("PAPER_TRADING", "true").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        # Prefer profile client id (ASX=19 vs US=18) when MARKET is set.
        client_raw = os.getenv("IBKR_EXEC_CLIENT_ID", "").strip()
        if client_raw:
            client_id = int(client_raw)
        else:
            try:
                from services.market_profile import get_market_profile

                client_id = get_market_profile().exec_client_id
            except Exception:  # noqa: BLE001
                client_id = 18
        return cls(
            broker=os.getenv("BROKER", "ibkr").strip().lower() or "ibkr",
            host=os.getenv("IBKR_HOST", "127.0.0.1").strip(),
            port=int(os.getenv("IBKR_PORT", "4002")),
            client_id=client_id,
            paper_trading=paper,
            fill_timeout_sec=float(os.getenv("EXEC_FILL_TIMEOUT_SEC", "45")),
            limit_slippage_pct=float(os.getenv("EXEC_LIMIT_SLIPPAGE_PCT", "0.5")),
        )
