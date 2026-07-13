"""Order Executor paper/live safety guards. No discretionary trading here."""

from __future__ import annotations

import os
from dataclasses import dataclass

# IBKR paper ports we allow for Phase A local Gateway / TWS paper.
PAPER_PORTS = {4002, 7497}
# IBKR live ports — refuse when PAPER_TRADING=true
LIVE_PORTS = {4001, 7496}


@dataclass(frozen=True)
class PaperGuardResult:
    ok: bool
    message: str


def check_paper_guard(
    *,
    port: int | None = None,
    paper_trading: bool | None = None,
) -> PaperGuardResult:
    """Hard abort conditions for mismatched paper/live configuration."""
    if port is None:
        port = int(os.getenv("IBKR_PORT", "4002"))
    if paper_trading is None:
        paper_trading = os.getenv("PAPER_TRADING", "true").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    if paper_trading and port in LIVE_PORTS:
        return PaperGuardResult(
            False,
            f"ABORT: PAPER_TRADING=true but IBKR_PORT={port} is a live port",
        )
    if (not paper_trading) and port in PAPER_PORTS:
        return PaperGuardResult(
            False,
            f"ABORT: PAPER_TRADING=false but IBKR_PORT={port} is a paper port",
        )
    if paper_trading and port not in PAPER_PORTS:
        return PaperGuardResult(
            False,
            f"ABORT: unexpected paper port {port}; expected one of {sorted(PAPER_PORTS)}",
        )
    return PaperGuardResult(True, "paper guard ok")


def assert_paper_safe() -> None:
    result = check_paper_guard()
    if not result.ok:
        raise SystemExit(result.message)
