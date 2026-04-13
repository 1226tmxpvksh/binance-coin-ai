"""
청산 전략 모듈
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
    """
    초기 손절/익절 가격 계산
    
    Args:
        entry_price: 진입 가격
        atr: ATR 값
        position_side: 포지션 방향 ("LONG" or "SHORT")
        sl_mult: 손절 ATR 배수
        tp_mult: 익절 ATR 배수
    
    Returns:
        Tuple[float, float]: (손절가, 익절가)
    """
    if position_side == "LONG":
        stop_loss = entry_price - (atr * sl_mult)
        take_profit = entry_price + (atr * tp_mult)
    else:  # SHORT
        stop_loss = entry_price + (atr * sl_mult)
        take_profit = entry_price - (atr * tp_mult)
    
    logger.debug(
        f"{position_side} 손익 설정: "
        f"진입가={entry_price:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}"
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
    """
    기본 손절/익절 체크
    
    Args:
        current_high: 현재 고가
        current_low: 현재 저가
        current_close: 현재 종가
        stop_loss: 손절가
        take_profit: 익절가
        position_side: 포지션 방향
    
    Returns:
        Tuple[bool, Optional[str], Optional[float]]: 
            (청산 여부, 청산 사유, 청산 가격)
    """
    if position_side == "LONG":
        # 손절 체크
        if current_low <= stop_loss:
            logger.info(f"LONG 손절: {current_low:.2f} <= {stop_loss:.2f}")
            return True, "stop_loss", stop_loss
        
        # 익절 체크
        if current_high >= take_profit:
            logger.info(f"LONG 익절: {current_high:.2f} >= {take_profit:.2f}")
            return True, "take_profit", take_profit
    
    else:  # SHORT
        # 손절 체크
        if current_high >= stop_loss:
            logger.info(f"SHORT 손절: {current_high:.2f} >= {stop_loss:.2f}")
            return True, "stop_loss", stop_loss
        
        # 익절 체크
        if current_low <= take_profit:
            logger.info(f"SHORT 익절: {current_low:.2f} <= {take_profit:.2f}")
            return True, "take_profit", take_profit
    
    return False, None, None


def check_long_hold_extension(
    current_close: float,
    entry_price: float,
    ema_short: float,
    ema_long: float
) -> bool:
    """
    LONG 보유 연장 조건 확인
    
    조건:
    1. close[t] > EMA20[t]
    2. EMA20[t] > EMA60[t]
    3. close[t] > entry_price
    
    Args:
        current_close: 현재 종가
        entry_price: 진입 가격
        ema_short: 단기 EMA
        ema_long: 장기 EMA
    
    Returns:
        bool: 보유 연장 가능 시 True
    """
    condition1 = current_close > ema_short
    condition2 = ema_short > ema_long
    condition3 = current_close > entry_price
    
    can_hold = condition1 and condition2 and condition3
    
    logger.debug(
        f"LONG 보유 연장 체크: "
        f"close({current_close:.2f}) > EMA20({ema_short:.2f}): {condition1}, "
        f"EMA20 > EMA60({ema_long:.2f}): {condition2}, "
        f"수익 상태: {condition3} => {can_hold}"
    )
    
    return can_hold


def calculate_trailing_stop(
    previous_stop: float,
    ema_short: float,
    atr: float,
    trailing_mult: float
) -> float:
    """
    트레일링 스탑 계산
    
    Args:
        previous_stop: 이전 손절가
        ema_short: 단기 EMA
        atr: ATR 값
        trailing_mult: 트레일링 ATR 배수
    
    Returns:
        float: 새로운 손절가
    """
    new_stop = ema_short - (atr * trailing_mult)
    trailing_stop = max(previous_stop, new_stop)
    
    logger.debug(
        f"트레일링 스탑 업데이트: "
        f"이전={previous_stop:.2f}, 신규={new_stop:.2f}, "
        f"최종={trailing_stop:.2f}"
    )
    
    return trailing_stop


def check_extension_exit(
    current_close: float,
    current_volume: float,
    ema_short: float,
    vol_ma: float,
    hold_days: int,
    max_hold_days: int
) -> Tuple[bool, Optional[str]]:
    """
    확장 보유 중 청산 조건 체크
    
    조건:
    1. close < EMA20
    2. volume < VOL_MA20
    3. hold_days >= MAX_HOLD_DAYS
    
    Args:
        current_close: 현재 종가
        current_volume: 현재 거래량
        ema_short: 단기 EMA
        vol_ma: 거래량 이동평균
        hold_days: 현재 보유 일수
        max_hold_days: 최대 보유 일수
    
    Returns:
        Tuple[bool, Optional[str]]: (청산 여부, 청산 사유)
    """
    # EMA 이탈
    if current_close < ema_short:
        logger.info(
            f"확장 청산: EMA 이탈 - "
            f"close({current_close:.2f}) < EMA20({ema_short:.2f})"
        )
        return True, "ema_break"
    
    # 거래량 감소
    if current_volume < vol_ma:
        logger.info(
            f"확장 청산: 거래량 감소 - "
            f"volume({current_volume:.0f}) < vol_ma({vol_ma:.0f})"
        )
        return True, "volume_low"
    
    # 최대 보유 일수 도달
    if hold_days >= max_hold_days:
        logger.info(
            f"확장 청산: 최대 보유 일수 도달 - "
            f"{hold_days} >= {max_hold_days}"
        )
        return True, "max_days"
    
    return False, None


def should_force_short_exit(position_side: str, hold_days: int) -> bool:
    """
    SHORT 포지션 강제 청산 여부
    
    SHORT는 무조건 익일 청산 (확장 금지)
    
    Args:
        position_side: 포지션 방향
        hold_days: 보유 일수
    
    Returns:
        bool: 청산해야 하면 True
    """
    if position_side == "SHORT" and hold_days >= 1:
        logger.info("SHORT 익일 강제 청산")
        return True
    
    return False
