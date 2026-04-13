from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple


def ensure_min_stop_gap(
    entry_price: float,
    stop_loss_price: float,
    side: str,
    min_gap_ratio: float = 0.005,
) -> Tuple[float, bool]:
    """
    손절가와 진입가 간 최소 이격 보장.

    Returns:
        (조정된 손절가, 최소 이격 보정 여부)
    """
    if entry_price <= 0:
        return stop_loss_price, False
    min_gap = entry_price * min_gap_ratio
    raw_gap = abs(entry_price - stop_loss_price)
    if raw_gap >= min_gap:
        return stop_loss_price, False
    direction = (side or "").upper()
    if direction == "SELL":
        return entry_price + min_gap, True
    return entry_price - min_gap, True


def assess_trade_risk(
    account_balance: float,
    entry_price: float,
    stop_loss_price: float,
    proposed_position_size: float,
    max_risk_ratio: float = 0.01,
    on_block: Optional[Callable[[str], None]] = None,
) -> Dict[str, float | bool | str]:
    if account_balance <= 0 or entry_price <= 0 or proposed_position_size <= 0:
        reason = "입력값이 유효하지 않아 차단"
        if on_block:
            on_block(f"[경고: 리스크 초과로 진입 차단] {reason}")
        return {"allowed": False, "risk_ratio": 1.0, "reason": reason}

    risk_amount = abs(entry_price - stop_loss_price) * proposed_position_size
    risk_ratio = risk_amount / account_balance if account_balance else 1.0
    allowed = risk_ratio <= max_risk_ratio
    reason = (
        f"리스크 {risk_ratio*100:.2f}% <= {max_risk_ratio*100:.2f}%"
        if allowed
        else f"리스크 {risk_ratio*100:.2f}% > {max_risk_ratio*100:.2f}%"
    )
    if not allowed and on_block:
        on_block(f"[경고: 리스크 초과로 진입 차단] {reason}")
    return {"allowed": allowed, "risk_ratio": risk_ratio, "reason": reason}
