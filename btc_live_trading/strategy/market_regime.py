"""
시장 국면 판단 모듈 (실전 매매용)
상승장/하락장/중립 구간을 판단하여 롱/숏 허용 여부 결정
"""

import logging
import numpy as np
from enum import Enum
from typing import Dict, Any

logger = logging.getLogger(__name__)


class MarketState(Enum):
    """시장 상태 열거형"""
    BULL = "BULL"      # 상승장 - LONG만 허용
    BEAR = "BEAR"      # 하락장 - SHORT만 허용
    NEUTRAL = "NEUTRAL"  # 중립 - 거래 금지


def determine_market_state(
    close: float,
    ema_short: float,
    ema_long: float,
    sma_long: float,
    sma_slope: float
) -> MarketState:
    """
    현재 시장 국면 판단
    상승장: EMA20 > EMA60
    하락장: close < SMA200 AND SMA200_SLOPE < 0
    그 외: 중립
    """
    # NaN 검증 (실전 안전장치)
    values = [close, ema_short, ema_long, sma_long]
    if any(v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))) for v in values):
        logger.warning("시장 판단: 유효하지 않은 지표값 → 중립 처리")
        return MarketState.NEUTRAL

    # 상승장
    if ema_short > ema_long:
        return MarketState.BULL

    # 하락장
    if close < sma_long and sma_slope < 0:
        return MarketState.BEAR

    return MarketState.NEUTRAL


def is_long_allowed(market_state: MarketState) -> bool:
    """LONG 진입 허용 여부"""
    return market_state == MarketState.BULL


def is_short_allowed(market_state: MarketState) -> bool:
    """SHORT 진입 허용 여부"""
    return market_state == MarketState.BEAR
