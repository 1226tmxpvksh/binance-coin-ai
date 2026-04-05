"""
리스크 관리 모듈 (실전 매매용)
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
    position_size = (account_balance * risk_per_trade) / abs(entry_price - stop_loss)
    """
    if entry_price <= 0 or account_balance <= 0:
        logger.error(f"유효하지 않은 입력: entry_price={entry_price}, balance={account_balance}")
        return 0.0

    risk_amount = account_balance * risk_per_trade
    price_risk_per_coin = abs(entry_price - stop_loss)

    if price_risk_per_coin == 0:
        logger.error("손절가와 진입가가 동일합니다")
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


def stop_loss_price_long(entry_price: float, stop_loss_pct: float) -> float:
    """롱: 진입가 대비 -stop_loss_pct % 가격."""
    if entry_price <= 0 or stop_loss_pct <= 0:
        return 0.0
    return entry_price * (1.0 - stop_loss_pct / 100.0)


def unrealized_pnl_usdt(
    side: str,
    entry_price: float,
    position_size: float,
    mark_price: float,
) -> float:
    if side == "LONG":
        return position_size * (mark_price - entry_price)
    if side == "SHORT":
        return position_size * (entry_price - mark_price)
    return 0.0


def krw_to_usdt(amount_krw: float, krw_per_usdt: float) -> float:
    if krw_per_usdt <= 0:
        return 0.0
    return amount_krw / krw_per_usdt


def tp_target_usdt_from_krw(tp_profit_krw: float, krw_per_usdt: float) -> float:
    return krw_to_usdt(tp_profit_krw, krw_per_usdt)


def should_take_profit_by_pnl_usdt(
    unrealized_pnl_usdt_value: float,
    target_profit_usdt: float,
) -> bool:
    return unrealized_pnl_usdt_value >= target_profit_usdt
