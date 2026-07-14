"""Market bars via Yahoo Finance chart API (httpx). No IBKR.

Phase A default for screening/filters. Includes retry/backoff for Yahoo 429s.
"""

from __future__ import annotations

import time
from typing import Optional

import httpx
import pandas as pd

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# Chrome-like UAs get Edge rate-limited hard; a short compatible UA is more reliable.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; tradey/1.0)",
    "Accept": "application/json",
}

# Last download failure summary for callers (prefilter error strings).
_last_yahoo_error: Optional[str] = None


def get_last_yahoo_error() -> Optional[str]:
    return _last_yahoo_error


def _set_last_yahoo_error(msg: Optional[str]) -> None:
    global _last_yahoo_error
    _last_yahoo_error = msg


def ibkr_to_yahoo(symbol: str, *, yahoo_suffix: str = "") -> str:
    """IBKR symbol → Yahoo chart symbol.

    US: class-B space → hyphen (BRK B → BRK-B).
    ASX: append .AX (BHP → BHP.AX) when yahoo_suffix=".AX".
    """
    base = symbol.strip().replace(" ", "-")
    if not yahoo_suffix:
        return base
    if base.upper().endswith(yahoo_suffix.upper()):
        return base
    return f"{base}{yahoo_suffix}"


def yahoo_to_ibkr(symbol: str, *, yahoo_suffix: str = "") -> str:
    """Yahoo chart symbol → IBKR (strip suffix, hyphen → space)."""
    s = symbol.strip()
    if yahoo_suffix and s.upper().endswith(yahoo_suffix.upper()):
        s = s[: -len(yahoo_suffix)]
    return s.replace("-", " ")


def _parse_chart_payload(payload: dict) -> pd.DataFrame:
    chart = payload.get("chart") or {}
    err = chart.get("error")
    result = chart.get("result")
    if err:
        desc = err.get("description") if isinstance(err, dict) else str(err)
        _set_last_yahoo_error(f"yahoo chart error: {desc}")
        return pd.DataFrame()
    if not result:
        _set_last_yahoo_error("yahoo chart result was null/empty")
        return pd.DataFrame()
    block = result[0]
    timestamps = block.get("timestamp") or []
    quote = (block.get("indicators") or {}).get("quote") or [{}]
    q0 = quote[0] if quote else {}
    if not timestamps:
        _set_last_yahoo_error("yahoo chart had no timestamps")
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


# Transient Yahoo / Edge statuses worth retrying (403 flaps under load; 429 rate limit).
_RETRYABLE_STATUS = frozenset({403, 429, 502, 503})


def _get_chart_json(
    client: httpx.Client,
    yahoo_symbol: str,
    *,
    period: str,
    interval: str,
    retries: int = 4,
) -> Optional[dict]:
    url = _CHART_URL.format(symbol=yahoo_symbol)
    delay = 0.5
    last_status: Optional[int] = None
    last_body = ""
    for attempt in range(retries):
        try:
            resp = client.get(url, params={"range": period, "interval": interval})
            last_status = resp.status_code
            last_body = (resp.text or "").strip()[:120]
            if resp.status_code in _RETRYABLE_STATUS:
                # Exponential backoff — Yahoo Edge cool-down can take tens of seconds.
                time.sleep(min(20.0, delay * (2**attempt)))
                continue
            if resp.status_code >= 400:
                _set_last_yahoo_error(
                    f"yahoo HTTP {resp.status_code} for {yahoo_symbol}"
                    + (f": {last_body}" if last_body else "")
                )
                return None
            if not resp.content:
                time.sleep(delay)
                continue
            # Non-JSON bodies (plain "Too Many Requests") — treat as soft fail.
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype and not resp.content.lstrip().startswith(b"{"):
                snippet = resp.text.strip()[:80]
                _set_last_yahoo_error(
                    f"yahoo non-JSON response for {yahoo_symbol}: {snippet or resp.status_code}"
                )
                time.sleep(delay * (attempt + 1))
                continue
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            # httpx raises ProxyError/HTTPStatusError for some 403s; keep message clear.
            _set_last_yahoo_error(f"yahoo request failed for {yahoo_symbol}: {exc}")
            time.sleep(delay * (attempt + 1))
    if last_status == 429:
        _set_last_yahoo_error(
            f"yahoo rate-limited (HTTP 429) for {yahoo_symbol} after {retries} retries"
        )
    elif last_status is not None:
        detail = f": {last_body}" if last_body else ""
        _set_last_yahoo_error(
            f"yahoo HTTP {last_status} for {yahoo_symbol} after {retries} retries{detail}"
        )
    return None


def fetch_chart(
    yahoo_symbol: str,
    *,
    period: str = "5d",
    interval: str = "1d",
    timeout: float = 20.0,
) -> pd.DataFrame:
    """Return OHLCV DataFrame indexed by UTC timestamps. Empty on failure."""
    _set_last_yahoo_error(None)
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
    pause_s: float = 0.15,
    max_consecutive_failures: int = 15,
) -> pd.DataFrame:
    """Bulk daily bars as MultiIndex columns (ticker, field).

    Sequential with pacing to reduce Yahoo 429s. Stops early if Yahoo looks down.
    `threads` kept for blog CLI compatibility but is not used for concurrency.
    """
    _ = threads
    _set_last_yahoo_error(None)
    if not yahoo_tickers:
        _set_last_yahoo_error("yahoo download called with empty ticker list")
        return pd.DataFrame()

    frames: dict[str, pd.DataFrame] = {}
    consecutive_fail = 0
    status_429 = 0
    other_fail = 0
    attempted = 0
    with httpx.Client(timeout=20.0, headers=_HEADERS, follow_redirects=True) as client:
        for i, sym in enumerate(yahoo_tickers):
            attempted += 1
            payload = _get_chart_json(
                client, sym, period=period, interval="1d", retries=3
            )
            after_err = get_last_yahoo_error() or ""
            if payload:
                frame = _parse_chart_payload(payload)
                if not frame.empty:
                    frames[sym] = frame
                    consecutive_fail = 0
                else:
                    consecutive_fail += 1
                    other_fail += 1
            else:
                consecutive_fail += 1
                if "429" in after_err:
                    status_429 += 1
                else:
                    other_fail += 1

            if consecutive_fail >= max_consecutive_failures and not frames:
                break

            if pause_s and i + 1 < len(yahoo_tickers):
                time.sleep(pause_s)

    if not frames:
        parts = [f"tried {attempted}/{len(yahoo_tickers)}", "0 frames"]
        if status_429:
            parts.append(f"{status_429} HTTP 429")
        if other_fail:
            parts.append(f"{other_fail} other failures")
        detail = get_last_yahoo_error()
        summary = "yahoo returned empty dataframe (" + ", ".join(parts) + ")"
        if detail and not detail.startswith("yahoo returned empty"):
            summary = f"{summary}; last: {detail}"
        _set_last_yahoo_error(summary)
        return pd.DataFrame()
    _set_last_yahoo_error(None)
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
