"""Market bars via Yahoo Finance chart API (httpx). No IBKR.

Phase A default for screening/filters. Includes retry/backoff for Yahoo 429s.
"""

from __future__ import annotations

import time
from typing import Optional

import httpx
import pandas as pd

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def ibkr_to_yahoo(symbol: str) -> str:
    """IBKR class-B space form → Yahoo hyphen form (BRK B → BRK-B)."""
    return symbol.strip().replace(" ", "-")


def yahoo_to_ibkr(symbol: str) -> str:
    return symbol.strip().replace("-", " ")


def _parse_chart_payload(payload: dict) -> pd.DataFrame:
    result = (payload.get("chart") or {}).get("result")
    if not result:
        return pd.DataFrame()
    block = result[0]
    timestamps = block.get("timestamp") or []
    quote = (block.get("indicators") or {}).get("quote") or [{}]
    q0 = quote[0] if quote else {}
    if not timestamps:
        return pd.DataFrame()
    frame = pd.DataFrame(
        {
            "Open": q0.get("open"),
            "High": q0.get("high"),
            "Low": q0.get("low"),
            "Close": q0.get("close"),
            "Volume": q0.get("volume"),
        },
        index=pd.to_datetime(timestamps, unit="s", utc=True),
    )
    return frame.dropna(subset=["Close"])


def _get_chart_json(
    client: httpx.Client,
    yahoo_symbol: str,
    *,
    period: str,
    interval: str,
    retries: int = 3,
) -> Optional[dict]:
    url = _CHART_URL.format(symbol=yahoo_symbol)
    delay = 0.35
    for attempt in range(retries):
        try:
            resp = client.get(url, params={"range": period, "interval": interval})
            if resp.status_code == 429:
                time.sleep(delay * (attempt + 1) * 2)
                continue
            if resp.status_code >= 400:
                return None
            if not resp.content:
                time.sleep(delay)
                continue
            return resp.json()
        except Exception:  # noqa: BLE001
            time.sleep(delay * (attempt + 1))
    return None


def fetch_chart(
    yahoo_symbol: str,
    *,
    period: str = "5d",
    interval: str = "1d",
    timeout: float = 20.0,
) -> pd.DataFrame:
    """Return OHLCV DataFrame indexed by UTC timestamps. Empty on failure."""
    with httpx.Client(timeout=timeout, headers=_HEADERS, follow_redirects=True) as client:
        payload = _get_chart_json(client, yahoo_symbol, period=period, interval=interval)
    if not payload:
        return pd.DataFrame()
    return _parse_chart_payload(payload)


def download_daily_bars(
    yahoo_tickers: list[str],
    *,
    period: str = "5d",
    threads: int = 5,
    pause_s: float = 0.12,
    max_consecutive_failures: int = 15,
) -> pd.DataFrame:
    """Bulk daily bars as MultiIndex columns (ticker, field).

    Sequential with pacing to reduce Yahoo 429s. Stops early if Yahoo looks down.
    `threads` kept for blog CLI compatibility but is not used for concurrency.
    """
    _ = threads
    if not yahoo_tickers:
        return pd.DataFrame()

    frames: dict[str, pd.DataFrame] = {}
    consecutive_fail = 0
    with httpx.Client(timeout=20.0, headers=_HEADERS, follow_redirects=True) as client:
        for i, sym in enumerate(yahoo_tickers):
            payload = _get_chart_json(
                client, sym, period=period, interval="1d", retries=2
            )
            if payload:
                frame = _parse_chart_payload(payload)
                if not frame.empty:
                    frames[sym] = frame
                    consecutive_fail = 0
                else:
                    consecutive_fail += 1
            else:
                consecutive_fail += 1

            if consecutive_fail >= max_consecutive_failures and not frames:
                break

            if pause_s and i + 1 < len(yahoo_tickers):
                time.sleep(pause_s)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1)


def download_symbol_history(
    yahoo_symbol: str,
    *,
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame:
    return fetch_chart(yahoo_symbol, period=period, interval=interval)


def extract_ticker_frame(data: pd.DataFrame, yahoo_symbol: str) -> Optional[pd.DataFrame]:
    """Pull one ticker's OHLCV from a multi-ticker download result."""
    if data is None or data.empty:
        return None
    if isinstance(data.columns, pd.MultiIndex):
        level0 = data.columns.get_level_values(0)
        if yahoo_symbol in level0:
            frame = data[yahoo_symbol].copy()
        else:
            return None
    else:
        frame = data.copy()
    if frame.empty or "Close" not in frame.columns:
        return None
    return frame.dropna(subset=["Close"])
