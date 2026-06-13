"""포지션과 안전장치 상태를 JSON으로 저장."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from monitor.safety import SafetyState


@dataclass
class LivePositionState:
    has_position: bool = False
    entry_time: str | None = None
    entry_price: float = 0.0
    quantity: float = 0.0
    stop_price: float = 0.0
    stop_order_id: str | None = None
    last_signal_time: str | None = None
    last_pivot_low: float | None = None
    last_pivot_low_time: str | None = None


@dataclass
class TradingState:
    position: LivePositionState = field(default_factory=LivePositionState)
    safety: SafetyState = field(default_factory=SafetyState)


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> TradingState:
        if not self.path.exists():
            return TradingState()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return TradingState(
            position=LivePositionState(**raw.get("position", {})),
            safety=SafetyState(**raw.get("safety", {})),
        )

    def save(self, state: TradingState) -> None:
        payload = {
            "position": asdict(state.position),
            "safety": asdict(state.safety),
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

