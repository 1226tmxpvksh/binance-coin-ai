"""
진입 전략 모듈 (실전 매매용)
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
    조건: 변동성 돌파 + 거래량 > VOL_MA20
    """
    range_prev = prev_high - prev_low
    breakout_price = current_open + (range_prev * k_value)
    breakout_condition = current_high > breakout_price
    volume_condition = prev_volume > vol_ma

    if breakout_condition and volume_condition:
        logger.info(
            f"LONG 진입 조건 충족: high({current_high:.2f}) > breakout({breakout_price:.2f}), "
            f"volume({prev_volume:.0f}) > vol_ma({vol_ma:.0f})"
        )
        return True

    logger.debug(f"LONG 진입 조건 미충족: breakout={breakout_condition}, volume={volume_condition}")
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
    조건: 변동성 돌파 + 거래량 > VOL_MA20
    """
    range_prev = prev_high - prev_low
    breakout_price = current_open - (range_prev * k_value)
    breakout_condition = current_low < breakout_price
    volume_condition = prev_volume > vol_ma

    if breakout_condition and volume_condition:
        logger.info(
            f"SHORT 진입 조건 충족: low({current_low:.2f}) < breakout({breakout_price:.2f}), "
            f"volume({prev_volume:.0f}) > vol_ma({vol_ma:.0f})"
        )
        return True

    logger.debug(f"SHORT 진입 조건 미충족: breakout={breakout_condition}, volume={volume_condition}")
    return False


def evaluate_entry_signal(
    market_state: str,
    current_data: Dict[str, float],
    prev_data: Dict[str, float],
    k_value: float
) -> Optional[PositionSide]:
    """진입 신호 평가"""
    # vol_ma NaN/0 검증 (실전 안전장치)
    vol_ma = prev_data.get('vol_ma', 0)
    if vol_ma is None or (isinstance(vol_ma, float) and (vol_ma != vol_ma or vol_ma <= 0)):
        logger.debug("진입 신호: 유효하지 않은 vol_ma → 거래 보류")
        return None

    if market_state == "BULL":
        is_long = check_long_entry(
            current_data['high'], current_data['open'],
            prev_data['high'], prev_data['low'],
            prev_data['volume'], prev_data['vol_ma'],
            k_value
        )
        if is_long:
            return PositionSide.LONG

    elif market_state == "BEAR":
        is_short = check_short_entry(
            current_data['low'], current_data['open'],
            prev_data['high'], prev_data['low'],
            prev_data['volume'], prev_data['vol_ma'],
            k_value
        )
        if is_short:
            return PositionSide.SHORT

    return None
