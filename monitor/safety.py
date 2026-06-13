"""실전 보호 장치."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class SafetyState:
    daily_trades: int = 0
    daily_realized_loss: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: str | None = None
    day: str | None = None


class SafetyManager:
    def __init__(
        self,
        daily_max_loss_percent: float,
        max_trades_per_day: int,
        max_consecutive_losses: int,
        cooldown_hours: int,
    ):
        self.daily_max_loss_percent = daily_max_loss_percent
        self.max_trades_per_day = max_trades_per_day
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_hours = cooldown_hours

    def reset_if_new_day(self, state: SafetyState) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if state.day != today:
            state.day = today
            state.daily_trades = 0
            state.daily_realized_loss = 0.0

    def can_open_new_trade(self, state: SafetyState, balance: float) -> tuple[bool, str]:
        self.reset_if_new_day(state)
        if state.cooldown_until:
            cooldown_until = datetime.fromisoformat(state.cooldown_until)
            if datetime.now() < cooldown_until:
                return False, f"연속 손실 쿨다운 중: {state.cooldown_until}"
            state.cooldown_until = None

        if state.daily_trades >= self.max_trades_per_day:
            return False, "일일 최대 거래 수 도달"
        if balance > 0:
            daily_loss_pct = state.daily_realized_loss / balance * 100
            if daily_loss_pct >= self.daily_max_loss_percent:
                return False, f"일일 최대 손실 초과: {daily_loss_pct:.2f}%"
        if state.consecutive_losses >= self.max_consecutive_losses:
            state.cooldown_until = (datetime.now() + timedelta(hours=self.cooldown_hours)).isoformat()
            return False, "최대 연속 손실 도달"
        return True, "OK"

    def record_trade_close(self, state: SafetyState, pnl: float) -> None:
        self.reset_if_new_day(state)
        if pnl < 0:
            state.daily_realized_loss += abs(pnl)
            state.consecutive_losses += 1
        else:
            state.consecutive_losses = 0
        state.daily_trades += 1

