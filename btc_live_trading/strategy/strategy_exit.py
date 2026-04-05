"""
청산 전략 모듈 (실전 매매용)
데이트레이딩 기본 청산 + LONG 확장형 보유 로직
"""

import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


def calculate_initial_stops(
    entry_price: float,
    atr: float,
    position_side: str,
    sl_mult: float,
    tp_mult: float
) -> Tuple[float, float]:
    """초기 손절/익절 가격 계산"""
    if position_side == "LONG":
        stop_loss = entry_price - (atr * sl_mult)
        take_profit = entry_price + (atr * tp_mult)
    else:
        stop_loss = entry_price + (atr * sl_mult)
        take_profit = entry_price - (atr * tp_mult)

    logger.debug(
        f"{position_side} 손익 설정: 진입가={entry_price:.2f}, "
        f"SL={stop_loss:.2f}, TP={take_profit:.2f}"
    )
    return stop_loss, take_profit


def check_basic_exit(
    current_high: float,
    current_low: float,
    current_close: float,
    stop_loss: float,
    take_profit: float,
    position_side: str
) -> Tuple[bool, Optional[str], Optional[float]]:
    """기본 손절/익절 체크"""
    if position_side == "LONG":
        if current_low <= stop_loss:
            return True, "stop_loss", stop_loss
        if current_high >= take_profit:
            return True, "take_profit", take_profit
    else:
        if current_high >= stop_loss:
            return True, "stop_loss", stop_loss
        if current_low <= take_profit:
            return True, "take_profit", take_profit

    return False, None, None


def check_long_hold_extension(
    current_close: float,
    entry_price: float,
    ema_short: float,
    ema_long: float
) -> bool:
    """LONG 보유 연장 조건 확인"""
    condition1 = current_close > ema_short
    condition2 = ema_short > ema_long
    condition3 = current_close > entry_price
    return condition1 and condition2 and condition3


def calculate_trailing_stop(
    previous_stop: float,
    ema_short: float,
    atr: float,
    trailing_mult: float
) -> float:
    """트레일링 스탑 계산"""
    new_stop = ema_short - (atr * trailing_mult)
    return max(previous_stop, new_stop)


def check_extension_exit(
    current_close: float,
    current_volume: float,
    ema_short: float,
    vol_ma: float,
    hold_days: int,
    max_hold_days: int
) -> Tuple[bool, Optional[str]]:
    """확장 보유 중 청산 조건 체크"""
    if current_close < ema_short:
        return True, "ema_break"
    if current_volume < vol_ma:
        return True, "volume_low"
    if hold_days >= max_hold_days:
        return True, "max_days"
    return False, None


def should_force_short_exit(position_side: str, hold_days: int) -> bool:
    """SHORT 포지션 강제 청산 (익일 청산)"""
    return position_side == "SHORT" and hold_days >= 1
