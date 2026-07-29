"""Proof-capital ceiling (AUD) → USD sizing notional for Risk Manager.

IBKR NetLiquidation is typically USD for US stock accounts. Risk sizing must
never exceed the configured AUD proof-capital cap (default A$10,000), converted
to USD via USD_AUD (AUD per 1 USD).

Env:
  PORTFOLIO_VALUE_AUD=10000   # hard proof-capital ceiling (preferred)
  USD_AUD=1.52                # AUD per 1 USD (approx FX for conversion)
  PORTFOLIO_VALUE_USD=...     # optional explicit USD override (skips AUD→USD)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


DEFAULT_PORTFOLIO_AUD = 10_000.0
DEFAULT_USD_AUD = 1.52  # ~AUD per USD; update periodically — not live FX


@dataclass(frozen=True)
class CapitalConfig:
    """Sizing capital source of truth for Phase A paper proof."""

    portfolio_value_aud: float = DEFAULT_PORTFOLIO_AUD
    usd_aud: float = DEFAULT_USD_AUD
    portfolio_value_usd_override: Optional[float] = None

    @classmethod
    def from_env(cls) -> "CapitalConfig":
        aud = float(os.getenv("PORTFOLIO_VALUE_AUD", str(DEFAULT_PORTFOLIO_AUD)))
        usd_aud = float(os.getenv("USD_AUD", str(DEFAULT_USD_AUD)))
        raw_usd = os.getenv("PORTFOLIO_VALUE_USD", "").strip()
        override = float(raw_usd) if raw_usd else None
        return cls(
            portfolio_value_aud=aud,
            usd_aud=usd_aud if usd_aud > 0 else DEFAULT_USD_AUD,
            portfolio_value_usd_override=override,
        )

    @property
    def portfolio_value_usd_cap(self) -> float:
        """AUD proof capital expressed as USD notional for US equity sizing."""
        if self.portfolio_value_usd_override is not None and self.portfolio_value_usd_override > 0:
            return float(self.portfolio_value_usd_override)
        if self.usd_aud <= 0:
            return self.portfolio_value_aud
        return self.portfolio_value_aud / self.usd_aud


def aud_to_usd(amount_aud: float, *, usd_aud: float) -> float:
    """Convert AUD → USD using AUD-per-USD rate."""
    if usd_aud <= 0:
        raise ValueError("usd_aud must be > 0")
    return float(amount_aud) / float(usd_aud)


def usd_to_aud(amount_usd: float, *, usd_aud: float) -> float:
    """Convert USD → AUD using AUD-per-USD rate."""
    if usd_aud <= 0:
        raise ValueError("usd_aud must be > 0")
    return float(amount_usd) * float(usd_aud)


def sizing_portfolio_usd(
    *,
    broker_net_liquidation_usd: Optional[float] = None,
    config: Optional[CapitalConfig] = None,
) -> float:
    """USD notional Risk may size against: min(broker NL, AUD proof-cap in USD).

    When broker NL is missing/zero (dry-run), returns the AUD-derived USD cap.
    """
    cfg = config or CapitalConfig.from_env()
    cap = cfg.portfolio_value_usd_cap
    if broker_net_liquidation_usd is None or broker_net_liquidation_usd <= 0:
        return cap
    return min(float(broker_net_liquidation_usd), cap)


def sizing_portfolio(
    *,
    broker_net_liquidation: Optional[float] = None,
    config: Optional[CapitalConfig] = None,
    currency: str = "USD",
) -> float:
    """Native sizing units for Risk: USD path (with FX) or AUD ceiling (no FX).

    ASX prices are AUD — pass portfolio_value_aud as the sizing notional.
    Broker NL is typically account base (often USD); for ASX v1 we size against
    the AUD proof ceiling so FX noise does not inflate ASX risk.
    """
    cfg = config or CapitalConfig.from_env()
    if (currency or "USD").upper() == "AUD":
        return float(cfg.portfolio_value_aud)
    return sizing_portfolio_usd(
        broker_net_liquidation_usd=broker_net_liquidation,
        config=cfg,
    )
