from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from packages.ledger import Ledger
from packages.observability.bot_status import build_bot_status_report
from packages.observability.closed_trades import load_closed_trades
from packages.observability.expectancy import summarize_closed_trades
from packages.observability.pnl_days import build_pnl_days, crude_pnl_from_fills

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="tradey dashboard", version="0.1.0")
ledger = Ledger()


def _ledger_crude_by_day(*, days: int, end: date | None = None) -> dict[str, dict[str, Any]]:
    end_d = end or datetime.now(timezone.utc).date()
    out: dict[str, dict[str, Any]] = {}
    for i in range(days):
        d = end_d - timedelta(days=i)
        fills = ledger.fills_on(d)
        if fills:
            out[d.isoformat()] = crude_pnl_from_fills(fills)
    return out


def _pnl_days_report(*, days: int = 10) -> dict[str, Any]:
    days = max(1, min(int(days), 90))
    trades = load_closed_trades()
    return build_pnl_days(
        trades,
        days=days,
        ledger_by_day=_ledger_crude_by_day(days=days),
    )


def _expectancy_stub() -> dict[str, Any]:
    trades = load_closed_trades()
    summary = summarize_closed_trades(trades)
    last_experiment = None
    experiments = ROOT / "data" / "experiments.md"
    if experiments.is_file():
        # Last non-empty, non-heading line as a light stub for UI
        for line in reversed(experiments.read_text(encoding="utf-8").splitlines()):
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("|---"):
                last_experiment = s[:120]
                break
    return {
        "n_closed": summary.get("n_closed", 0),
        "expectancy_R": summary.get("expectancy_R"),
        "win_rate": summary.get("win_rate"),
        "last_experiment_line": last_experiment,
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "dashboard", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/events")
def api_events(limit: int = 50) -> dict:
    return {"events": ledger.recent(limit=limit)}


@app.get("/api/summary")
def api_summary() -> dict:
    return ledger.summary_today()


@app.get("/api/pnl_days")
def api_pnl_days(days: int = 10) -> dict:
    return _pnl_days_report(days=days)


@app.get("/api/bot_status")
def api_bot_status() -> dict:
    return build_bot_status_report()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    events = ledger.recent(limit=50)
    summary = ledger.summary_today()
    pnl_days = _pnl_days_report(days=10)
    expectancy = _expectancy_stub()
    bot_status = build_bot_status_report()
    paper = os.getenv("PAPER_TRADING", "true").lower() in {"1", "true", "yes"}
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "events": events,
            "summary": summary,
            "pnl_days": pnl_days,
            "expectancy": expectancy,
            "bot_status": bot_status,
            "paper": paper,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "db_path": str(ledger.db_path),
        },
    )
