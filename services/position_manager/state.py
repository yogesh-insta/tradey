"""JSON persistence for open managed positions (Phase A)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from packages.contracts.position import ManagedPosition

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = ROOT / "data" / "us" / "open_positions.json"


def default_state_path() -> Path:
    raw = os.getenv("MANAGED_POSITIONS_PATH", "")
    if raw.strip():
        path = Path(raw.strip())
        if not path.is_absolute():
            path = ROOT / path
        return path
    try:
        from services.market_profile import get_market_profile

        return get_market_profile().open_positions_path
    except Exception:  # noqa: BLE001
        return DEFAULT_STATE_PATH


class PositionStore:
    """Simple JSON list keyed by symbol (one managed long per symbol in v1)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_state_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[ManagedPosition]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        positions: list[ManagedPosition] = []
        for item in raw:
            try:
                positions.append(ManagedPosition.model_validate(item))
            except Exception:  # noqa: BLE001
                continue
        return positions

    def save(self, positions: list[ManagedPosition]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [p.model_dump(mode="json") for p in positions]
        text = json.dumps(payload, indent=2) + "\n"
        # Atomic replace so a crash mid-write cannot leave truncate/corrupt JSON
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    def upsert(self, position: ManagedPosition) -> list[ManagedPosition]:
        positions = [p for p in self.load() if p.symbol != position.symbol]
        positions.append(position)
        self.save(positions)
        return positions

    def remove(self, symbol: str) -> list[ManagedPosition]:
        positions = [p for p in self.load() if p.symbol != symbol]
        self.save(positions)
        return positions

    def get(self, symbol: str) -> Optional[ManagedPosition]:
        for pos in self.load():
            if pos.symbol == symbol:
                return pos
        return None
