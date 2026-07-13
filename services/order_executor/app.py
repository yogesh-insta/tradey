"""Thin FastAPI surface for Order Executor (local Phase A / later cloud)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException

from packages.contracts.execution import ExecutionResult, PortfolioSnapshot
from packages.contracts.risk import SizedOrderIntent
from services.order_executor.config import ExecutorConfig
from services.order_executor.execution_service import ExecutionService
from services.order_executor.paper_guard import check_paper_guard

app = FastAPI(title="tradey order-executor", version="0.1.0")
_svc: Optional[ExecutionService] = None


def get_service() -> ExecutionService:
    global _svc
    if _svc is None:
        _svc = ExecutionService(config=ExecutorConfig.from_env())
    return _svc


@app.on_event("shutdown")
def _shutdown() -> None:
    global _svc
    if _svc is not None:
        _svc.close()
        _svc = None


@app.get("/health")
def health() -> dict:
    guard = check_paper_guard()
    svc = get_service()
    connected = False
    try:
        connected = svc.adapter.is_connected()
    except Exception:  # noqa: BLE001
        connected = False
    return {
        "ok": guard.ok,
        "service": "order_executor",
        "broker": svc.config.broker,
        "paper_guard": guard.message,
        "connected": connected,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/v1/balance")
def balance() -> PortfolioSnapshot:
    svc = get_service()
    try:
        svc.open()
        snap = svc.safe_portfolio()
    except SystemExit as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if snap is None:
        raise HTTPException(status_code=503, detail="broker disconnect / no snapshot")
    return snap


@app.get("/v1/positions")
def positions() -> dict:
    snap = balance()
    return {
        "account_id": snap.account_id,
        "open_position_count": snap.open_position_count,
        "positions": [p.model_dump(mode="json") for p in snap.positions],
    }


@app.post("/v1/orders", response_model=ExecutionResult)
def place_order(intent: SizedOrderIntent) -> ExecutionResult:
    svc = get_service()
    try:
        svc.open()
    except SystemExit as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not svc.adapter.is_connected():
        raise HTTPException(status_code=503, detail="broker not connected")
    return svc.execute_intent(intent)
