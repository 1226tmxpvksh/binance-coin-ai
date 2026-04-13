"""
시장 국면 판단 모듈
상승장/하락장/중립 구간을 판단하여 롱/숏 허용 여부 결정
"""

import logging
from enum import Enum
from typing import Dict, Any

logger = logging.getLogger(__name__)


class MarketState(Enum):
    """시장 상태 열거형"""
    BULL = "BULL"  # 상승장 - LONG만 허용
    BEAR = "BEAR"  # 하락장 - SHORT만 허용
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
    
    상승장 조건: EMA20 > EMA60
    하락장 조건: close < SMA200 AND SMA200_SLOPE < 0
    그 외: 중립
    
    Args:
        close: 현재 종가
        ema_short: 단기 EMA (20)
        ema_long: 장기 EMA (60)
        sma_long: 장기 SMA (200)
        sma_slope: SMA200의 기울기
    
    Returns:
        MarketState: 시장 상태
    """
    # 상승장 판단 (LONG 허용)
    if ema_short > ema_long:
        logger.debug(
            f"상승장 판단: EMA20({ema_short:.2f}) > EMA60({ema_long:.2f})"
        )
        return MarketState.BULL
    
    # 하락장 판단 (SHORT 허용 - 엄격)
    if close < sma_long and sma_slope < 0:
        logger.debug(
            f"하락장 판단: close({close:.2f}) < SMA200({sma_long:.2f}) "
            f"AND SMA_SLOPE({sma_slope:.2f}) < 0"
        )
        return MarketState.BEAR
    
    # 그 외 중립
    logger.debug(
        f"중립 구간: EMA20({ema_short:.2f}), EMA60({ema_long:.2f}), "
        f"close({close:.2f}), SMA200({sma_long:.2f}), slope({sma_slope:.2f})"
    )
    return MarketState.NEUTRAL


def is_long_allowed(market_state: MarketState) -> bool:
    """
    LONG 진입 허용 여부
    
    Args:
        market_state: 현재 시장 상태
    
    Returns:
        bool: LONG 허용 시 True
    """
    return market_state == MarketState.BULL


def is_short_allowed(market_state: MarketState) -> bool:
    """
    SHORT 진입 허용 여부
    
    Args:
        market_state: 현재 시장 상태
    
    Returns:
        bool: SHORT 허용 시 True
    """
    return market_state == MarketState.BEAR


def get_market_analysis(
    close: float,
    ema_short: float,
    ema_long: float,
    sma_long: float,
    sma_slope: float
) -> Dict[str, Any]:
    """
    시장 분석 정보 반환
    
    Args:
        close: 현재 종가
        ema_short: 단기 EMA
        ema_long: 장기 EMA
        sma_long: 장기 SMA
        sma_slope: SMA 기울기
    
    Returns:
        Dict: 시장 분석 정보
    """
    market_state = determine_market_state(
        close, ema_short, ema_long, sma_long, sma_slope
    )
    
    return {
        "market_state": market_state,
        "long_allowed": is_long_allowed(market_state),
        "short_allowed": is_short_allowed(market_state),
        "close": close,
        "ema_short": ema_short,
        "ema_long": ema_long,
        "sma_long": sma_long,
        "sma_slope": sma_slope
    }
