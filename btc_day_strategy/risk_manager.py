"""
리스크 관리 모듈
포지션 사이즈 계산 및 리스크 제한 관리
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def calculate_position_size(
    account_balance: float,
    entry_price: float,
    stop_loss: float,
    risk_per_trade: float,
    leverage: int = 1
) -> float:
    """
    포지션 사이즈 계산
    
    계산식: position_size = (account_balance * risk_per_trade) / abs(entry_price - stop_loss)
    
    Args:
        account_balance: 계좌 잔고
        entry_price: 진입 가격
        stop_loss: 손절 가격
        risk_per_trade: 거래당 리스크 비율 (예: 0.01 = 1%)
        leverage: 레버리지 배수
    
    Returns:
        float: 포지션 사이즈 (코인 개수)
    """
    if entry_price <= 0 or account_balance <= 0:
        logger.error(
            f"유효하지 않은 입력값: "
            f"entry_price={entry_price}, balance={account_balance}"
        )
        return 0.0
    
    # 거래당 리스크 금액
    risk_amount = account_balance * risk_per_trade
    
    # 1코인당 손실 금액
    price_risk_per_coin = abs(entry_price - stop_loss)
    
    if price_risk_per_coin == 0:
        logger.error("손절가와 진입가가 동일합니다")
        return 0.0
    
    # 포지션 사이즈 계산
    position_size = risk_amount / price_risk_per_coin
    
    # 레버리지 적용 시 최대 포지션 제한
    max_position = (account_balance * leverage) / entry_price
    position_size = min(position_size, max_position)
    
    logger.info(
        f"포지션 사이즈 계산: "
        f"잔고={account_balance:.2f}, 리스크금액={risk_amount:.2f}, "
        f"1코인 손실={price_risk_per_coin:.2f}, "
        f"포지션={position_size:.6f}, 최대={max_position:.6f}"
    )
    
    return position_size


def calculate_position_value(
    position_size: float,
    entry_price: float
) -> float:
    """
    포지션 가치 계산
    
    Args:
        position_size: 포지션 사이즈
        entry_price: 진입 가격
    
    Returns:
        float: 포지션 가치 (USDT)
    """
    return position_size * entry_price


def validate_position(
    position_size: float,
    account_balance: float,
    entry_price: float,
    max_position_ratio: float = 1.0
) -> bool:
    """
    포지션 유효성 검증
    
    Args:
        position_size: 포지션 사이즈
        account_balance: 계좌 잔고
        entry_price: 진입 가격
        max_position_ratio: 최대 포지션 비율 (기본 100%)
    
    Returns:
        bool: 유효한 포지션이면 True
    """
    if position_size <= 0:
        logger.warning("포지션 사이즈가 0 이하입니다")
        return False
    
    position_value = calculate_position_value(position_size, entry_price)
    max_value = account_balance * max_position_ratio
    
    if position_value > max_value:
        logger.warning(
            f"포지션이 너무 큽니다: "
            f"{position_value:.2f} > {max_value:.2f}"
        )
        return False
    
    return True


def calculate_pnl(
    position_size: float,
    entry_price: float,
    exit_price: float,
    position_side: str
) -> float:
    """
    손익 계산
    
    Args:
        position_size: 포지션 사이즈
        entry_price: 진입 가격
        exit_price: 청산 가격
        position_side: 포지션 방향 ("LONG" or "SHORT")
    
    Returns:
        float: 손익 (USDT)
    """
    if position_side == "LONG":
        pnl = position_size * (exit_price - entry_price)
    else:  # SHORT
        pnl = position_size * (entry_price - exit_price)
    
    logger.debug(
        f"손익 계산: {position_side}, "
        f"size={position_size:.6f}, entry={entry_price:.2f}, "
        f"exit={exit_price:.2f}, pnl={pnl:.2f}"
    )
    
    return pnl


def calculate_profit_rate(
    pnl: float,
    position_value: float
) -> float:
    """
    수익률 계산
    
    Args:
        pnl: 손익 금액
        position_value: 포지션 가치
    
    Returns:
        float: 수익률 (%)
    """
    if position_value == 0:
        logger.error("포지션 가치가 0입니다")
        return 0.0
    
    profit_rate = (pnl / position_value) * 100
    
    return profit_rate


def check_daily_trade_limit(
    trades_today: int,
    max_trades_per_day: int
) -> bool:
    """
    일일 거래 횟수 제한 체크
    
    Args:
        trades_today: 오늘 거래 횟수
        max_trades_per_day: 최대 일일 거래 횟수
    
    Returns:
        bool: 거래 가능하면 True
    """
    if trades_today >= max_trades_per_day:
        logger.warning(
            f"일일 거래 한도 도달: {trades_today} >= {max_trades_per_day}"
        )
        return False
    
    return True


def can_open_position(
    has_position: bool,
    single_position_only: bool
) -> bool:
    """
    포지션 오픈 가능 여부 체크
    
    Args:
        has_position: 현재 포지션 보유 여부
        single_position_only: 단일 포지션만 허용 여부
    
    Returns:
        bool: 포지션 오픈 가능하면 True
    """
    if single_position_only and has_position:
        logger.debug("이미 포지션을 보유 중입니다 (단일 포지션 모드)")
        return False
    
    return True
