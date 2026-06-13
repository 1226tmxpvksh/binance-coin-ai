"""리스크 기반 포지션 크기 계산."""

from __future__ import annotations


def calculate_position_size(balance: float, entry_price: float, stop_price: float, risk_per_trade: float) -> float:
    risk_amount = balance * risk_per_trade
    stop_distance = entry_price - stop_price
    if balance <= 0 or entry_price <= 0 or stop_distance <= 0:
        return 0.0
    return risk_amount / stop_distance


def calculate_position_value(quantity: float, entry_price: float) -> float:
    return quantity * entry_price

