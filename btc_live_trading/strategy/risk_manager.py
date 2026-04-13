"""
리스크 관리 모듈 (실전 매매용)
포지션 사이즈 계산 및 리스크 제한 관리
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# 보수적 손절 폭 권장 범위: ATR 1.5x ~ 2.0x
DEFAULT_ATR_STOP_MULTIPLIER = 1.8
MIN_ATR_STOP_MULTIPLIER = 1.5
MAX_ATR_STOP_MULTIPLIER = 2.0


def calculate_position_size(
    account_balance: float,
    entry_price: float,
    stop_loss: float,
    risk_per_trade: float,
    leverage: int = 1
) -> float:
    """
    포지션 사이즈 계산
    position_size = (account_balance * risk_per_trade) / abs(entry_price - stop_loss)
    """
    if entry_price <= 0 or account_balance <= 0:
        logger.error(f"유효하지 않은 입력: entry_price={entry_price}, balance={account_balance}")
        return 0.0

    risk_amount = account_balance * risk_per_trade
    price_risk_per_coin = abs(entry_price - stop_loss)

    if price_risk_per_coin == 0:
        logger.warning("손절가와 진입가가 동일함 -> 변동성 부족으로 인한 대기")
        return 0.0

    position_size = risk_amount / price_risk_per_coin
    max_position = (account_balance * leverage) / entry_price
    position_size = min(position_size, max_position)

    logger.info(
        f"포지션 사이즈: 잔고={account_balance:.2f}, 리스크={risk_amount:.2f}, "
        f"1코인손실={price_risk_per_coin:.2f}, 포지션={position_size:.6f}"
    )
    return position_size


def calculate_position_value(position_size: float, entry_price: float) -> float:
    """포지션 가치 계산 (USDT)"""
    return position_size * entry_price


def calculate_stop_loss_by_atr(entry_price: float, atr: float, side: str, atr_multiplier: float = DEFAULT_ATR_STOP_MULTIPLIER) -> float:
    """
    ATR 기반 손절가 계산.
    기본값은 보수적으로 1.8배를 사용하며, 권장 범위는 1.5~2.0배.
    """
    m = max(MIN_ATR_STOP_MULTIPLIER, min(MAX_ATR_STOP_MULTIPLIER, atr_multiplier))
    if side.upper() == "SELL":
        return entry_price + (atr * m)
    return entry_price - (atr * m)
