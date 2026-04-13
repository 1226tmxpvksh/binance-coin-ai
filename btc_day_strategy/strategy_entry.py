"""
진입 전략 모듈
변동성 돌파 기반 LONG/SHORT 진입 조건 판단
"""

import logging
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class PositionSide(Enum):
    """포지션 방향"""
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


def check_long_entry(
    current_high: float,
    current_open: float,
    prev_high: float,
    prev_low: float,
    prev_volume: float,
    vol_ma: float,
    k_value: float
) -> bool:
    """
    LONG 진입 조건 확인
    
    조건:
    1. 변동성 돌파: high[t] > open[t] + (high[t-1] - low[t-1]) * k
    2. 거래량 확인: volume[t-1] > VOL_MA20[t-1]
    
    Args:
        current_high: 현재 고가
        current_open: 현재 시가
        prev_high: 이전 고가
        prev_low: 이전 저가
        prev_volume: 이전 거래량
        vol_ma: 거래량 이동평균
        k_value: 변동성 계수
    
    Returns:
        bool: 진입 조건 충족 시 True
    """
    # 변동성 돌파 가격 계산
    range_prev = prev_high - prev_low
    breakout_price = current_open + (range_prev * k_value)
    
    # 조건 1: 변동성 돌파
    breakout_condition = current_high > breakout_price
    
    # 조건 2: 거래량 확인
    volume_condition = prev_volume > vol_ma
    
    if breakout_condition and volume_condition:
        logger.info(
            f"LONG 진입 조건 충족: "
            f"high({current_high:.2f}) > breakout({breakout_price:.2f}), "
            f"volume({prev_volume:.0f}) > vol_ma({vol_ma:.0f})"
        )
        return True
    
    logger.debug(
        f"LONG 진입 조건 미충족: "
        f"breakout={breakout_condition}, volume={volume_condition}"
    )
    return False


def check_short_entry(
    current_low: float,
    current_open: float,
    prev_high: float,
    prev_low: float,
    prev_volume: float,
    vol_ma: float,
    k_value: float
) -> bool:
    """
    SHORT 진입 조건 확인
    
    조건:
    1. 변동성 돌파: low[t] < open[t] - (high[t-1] - low[t-1]) * k
    2. 거래량 확인: volume[t-1] > VOL_MA20[t-1]
    
    Args:
        current_low: 현재 저가
        current_open: 현재 시가
        prev_high: 이전 고가
        prev_low: 이전 저가
        prev_volume: 이전 거래량
        vol_ma: 거래량 이동평균
        k_value: 변동성 계수
    
    Returns:
        bool: 진입 조건 충족 시 True
    """
    # 변동성 돌파 가격 계산
    range_prev = prev_high - prev_low
    breakout_price = current_open - (range_prev * k_value)
    
    # 조건 1: 변동성 돌파
    breakout_condition = current_low < breakout_price
    
    # 조건 2: 거래량 확인
    volume_condition = prev_volume > vol_ma
    
    if breakout_condition and volume_condition:
        logger.info(
            f"SHORT 진입 조건 충족: "
            f"low({current_low:.2f}) < breakout({breakout_price:.2f}), "
            f"volume({prev_volume:.0f}) > vol_ma({vol_ma:.0f})"
        )
        return True
    
    logger.debug(
        f"SHORT 진입 조건 미충족: "
        f"breakout={breakout_condition}, volume={volume_condition}"
    )
    return False


def evaluate_entry_signal(
    market_state: str,
    current_data: Dict[str, float],
    prev_data: Dict[str, float],
    k_value: float
) -> Optional[PositionSide]:
    """
    진입 신호 평가
    
    Args:
        market_state: 시장 상태 ("BULL", "BEAR", "NEUTRAL")
        current_data: 현재 캔들 데이터
        prev_data: 이전 캔들 데이터
        k_value: 변동성 계수
    
    Returns:
        Optional[PositionSide]: 진입할 포지션 방향 (없으면 None)
    """
    # 상승장 - LONG 신호 확인
    if market_state == "BULL":
        is_long = check_long_entry(
            current_data['high'],
            current_data['open'],
            prev_data['high'],
            prev_data['low'],
            prev_data['volume'],
            prev_data['vol_ma'],
            k_value
        )
        if is_long:
            return PositionSide.LONG
    
    # 하락장 - SHORT 신호 확인
    elif market_state == "BEAR":
        is_short = check_short_entry(
            current_data['low'],
            current_data['open'],
            prev_data['high'],
            prev_data['low'],
            prev_data['volume'],
            prev_data['vol_ma'],
            k_value
        )
        if is_short:
            return PositionSide.SHORT
    
    # 중립 또는 조건 미충족
    return None


def calculate_entry_price(
    position_side: PositionSide,
    current_open: float,
    current_high: float,
    current_low: float
) -> float:
    """
    진입 가격 계산
    
    실제로는 돌파 시점의 가격을 사용하지만,
    백테스트에서는 시가를 기준으로 계산
    
    Args:
        position_side: 포지션 방향
        current_open: 현재 시가
        current_high: 현재 고가
        current_low: 현재 저가
    
    Returns:
        float: 진입 가격
    """
    # 간단하게 시가를 진입가로 사용
    # 실전에서는 돌파 시점의 실제 가격 사용
    return current_open
