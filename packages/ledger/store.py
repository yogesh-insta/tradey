"""SQLite append-only ledger for trades/events (dashboard + ATO later)."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from dotenv import load_dotenv

from packages.contracts.events import TradeEvent

load_dotenv()


def _default_db_path() -> Path:
    raw = os.getenv("LEDGER_DB_PATH", "data/ledger.sqlite3")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class Ledger:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    timestamp_utc TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    broker TEXT,
                    account_id TEXT,
                    symbol TEXT,
                    venue TEXT,
                    currency TEXT,
                    side TEXT,
                    qty REAL,
                    price REAL,
                    fees REAL,
                    status TEXT,
                    message TEXT,
                    raw_ref TEXT,
                    metadata_json TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp_utc DESC)"
            )

    def append(self, event: TradeEvent) -> bool:
        """Insert event. Returns False if duplicate event_id (idempotent)."""
        payload = event.model_dump()
        ts = payload["timestamp_utc"]
        if isinstance(ts, datetime):
            ts = ts.astimezone(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO events (
                        event_id, timestamp_utc, event_type, broker, account_id,
                        symbol, venue, currency, side, qty, price, fees, status,
                        message, raw_ref, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["event_id"],
                        ts,
                        payload["event_type"],
                        payload.get("broker"),
                        payload.get("account_id"),
                        payload.get("symbol"),
                        payload.get("venue"),
                        payload.get("currency"),
                        payload.get("side"),
                        payload.get("qty"),
                        payload.get("price"),
                        payload.get("fees"),
                        payload.get("status"),
                        payload.get("message"),
                        payload.get("raw_ref"),
                        json.dumps(payload.get("metadata") or {}),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                ORDER BY timestamp_utc DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def fills_on(self, day: Optional[date] = None) -> list[dict[str, Any]]:
        """Fills for a UTC calendar day (YYYY-MM-DD prefix on timestamp_utc)."""
        day_s = (day or date.today()).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE event_type = 'fill'
                  AND substr(timestamp_utc, 1, 10) = ?
                ORDER BY timestamp_utc DESC
                """,
                (day_s,),
            ).fetchall()
        return [dict(r) for r in rows]

    def fills_today(self) -> list[dict[str, Any]]:
        return self.fills_on(date.today())

    def summary_today(self) -> dict[str, Any]:
        fills = self.fills_today()
        buys = [f for f in fills if (f.get("side") or "").upper() == "BUY"]
        sells = [f for f in fills if (f.get("side") or "").upper() == "SELL"]
        buy_notional = sum((f.get("qty") or 0) * (f.get("price") or 0) for f in buys)
        sell_notional = sum((f.get("qty") or 0) * (f.get("price") or 0) for f in sells)
        return {
            "fill_count": len(fills),
            "buy_count": len(buys),
            "sell_count": len(sells),
            "buy_notional": round(buy_notional, 2),
            "sell_notional": round(sell_notional, 2),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
