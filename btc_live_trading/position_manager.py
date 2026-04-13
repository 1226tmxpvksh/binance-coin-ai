"""
포지션 관리 모듈
실시간으로 포지션을 추적하고 관리
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class PositionSide(Enum):
    """포지션 방향"""
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class LivePosition:
    """실시간 포지션 클래스"""
    
    def __init__(
        self,
        side: str,
        entry_date: datetime,
        entry_price: float,
        size: float,
        stop_loss: float,
        take_profit: float,
        order_id: str = None,
        stop_order_id: str = None,
        tp_order_id: str = None
    ):
        self.side = side
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.size = size
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.order_id = order_id
        self.stop_order_id = stop_order_id  # 손절 주문 ID
        self.tp_order_id = tp_order_id      # 익절 주문 ID
        self.hold_days = 0
        self.is_extended = False
        self.highest_price = entry_price if side == "LONG" else None
        self.lowest_price = entry_price if side == "SHORT" else None
    
    def update_hold_days(self, current_date: datetime):
        """보유 일수 업데이트"""
        delta = current_date - self.entry_date
        self.hold_days = delta.days
    
    def update_trailing_stop(self, new_stop: float):
        """트레일링 스탑 업데이트"""
        if self.side == "LONG":
            # LONG은 손절가를 상향 조정만 가능
            if new_stop > self.stop_loss:
                old_stop = self.stop_loss
                self.stop_loss = new_stop
                logger.info(
                    f"트레일링 스탑 업데이트: "
                    f"${old_stop:,.2f} → ${new_stop:,.2f}"
                )
        else:  # SHORT
            # SHORT는 손절가를 하향 조정만 가능
            if new_stop < self.stop_loss:
                old_stop = self.stop_loss
                self.stop_loss = new_stop
                logger.info(
                    f"트레일링 스탑 업데이트: "
                    f"${old_stop:,.2f} → ${new_stop:,.2f}"
                )
    
    def update_price_extremes(self, current_price: float):
        """최고/최저가 업데이트"""
        if self.side == "LONG":
            if self.highest_price is None or current_price > self.highest_price:
                self.highest_price = current_price
        else:  # SHORT
            if self.lowest_price is None or current_price < self.lowest_price:
                self.lowest_price = current_price
    
    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """
        미실현 손익 계산
        
        Args:
            current_price: 현재 가격
        
        Returns:
            float: 미실현 손익
        """
        if self.side == "LONG":
            pnl = self.size * (current_price - self.entry_price)
        else:  # SHORT
            pnl = self.size * (self.entry_price - current_price)
        
        return pnl
    
    def update_stop_order_id(self, new_stop_order_id: str):
        """손절 주문 ID 업데이트"""
        self.stop_order_id = new_stop_order_id
        logger.debug(f"손절 주문 ID 업데이트: {new_stop_order_id}")
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'side': self.side,
            'entry_date': self.entry_date,
            'entry_price': self.entry_price,
            'size': self.size,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'order_id': self.order_id,
            'hold_days': self.hold_days,
            'is_extended': self.is_extended,
            'highest_price': self.highest_price,
            'lowest_price': self.lowest_price
        }


class PositionManager:
    """포지션 관리 클래스"""
    
    def __init__(self):
        self.current_position: Optional[LivePosition] = None
        self.trade_history: list = []
        self.daily_trades: int = 0
        self.daily_pnl: float = 0.0
        self.last_trade_date: Optional[datetime] = None
    
    def has_position(self) -> bool:
        """포지션 보유 여부"""
        return self.current_position is not None
    
    def get_position(self) -> Optional[LivePosition]:
        """현재 포지션 반환"""
        return self.current_position
    
    def open_position(
        self,
        side: str,
        entry_price: float,
        size: float,
        stop_loss: float,
        take_profit: float,
        order_id: str = None,
        stop_order_id: str = None,
        tp_order_id: str = None
    ) -> LivePosition:
        """
        포지션 오픈
        
        Args:
            side: 포지션 방향
            entry_price: 진입 가격
            size: 수량
            stop_loss: 손절가
            take_profit: 익절가
            order_id: 진입 주문 ID
            stop_order_id: 손절 주문 ID
            tp_order_id: 익절 주문 ID
        
        Returns:
            LivePosition: 생성된 포지션
        """
        self.current_position = LivePosition(
            side=side,
            entry_date=datetime.now(),
            entry_price=entry_price,
            size=size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            order_id=order_id,
            stop_order_id=stop_order_id,
            tp_order_id=tp_order_id
        )
        
        logger.info(
            f"포지션 오픈: {side} @ ${entry_price:,.2f}, "
            f"size={size:.6f}, SL=${stop_loss:,.2f}, TP=${take_profit:,.2f}"
        )
        
        if stop_order_id:
            logger.info(f"  손절 주문 ID: {stop_order_id}")
        if tp_order_id:
            logger.info(f"  익절 주문 ID: {tp_order_id}")
        
        return self.current_position
    
    def close_position(
        self,
        exit_price: float,
        exit_reason: str
    ) -> Optional[Dict[str, Any]]:
        """
        포지션 청산
        
        Args:
            exit_price: 청산 가격
            exit_reason: 청산 사유
        
        Returns:
            Dict: 거래 기록
        """
        if self.current_position is None:
            logger.warning("청산할 포지션이 없습니다")
            return None
        
        # 손익 계산
        if self.current_position.side == "LONG":
            pnl = self.current_position.size * (exit_price - self.current_position.entry_price)
        else:  # SHORT
            pnl = self.current_position.size * (self.current_position.entry_price - exit_price)
        
        position_value = self.current_position.size * self.current_position.entry_price
        profit_rate = (pnl / position_value) * 100
        
        # 거래 기록 생성
        trade_record = {
            'entry_date': self.current_position.entry_date,
            'exit_date': datetime.now(),
            'side': self.current_position.side,
            'entry_price': self.current_position.entry_price,
            'exit_price': exit_price,
            'size': self.current_position.size,
            'pnl': pnl,
            'profit_rate': profit_rate,
            'hold_days': self.current_position.hold_days,
            'exit_reason': exit_reason,
            'is_extended': self.current_position.is_extended
        }
        
        # 일일 통계 업데이트
        self.daily_pnl += pnl
        self.daily_trades += 1
        self.last_trade_date = datetime.now()
        
        # 거래 히스토리에 추가
        self.trade_history.append(trade_record)
        
        logger.info(
            f"포지션 청산: {self.current_position.side} @ ${exit_price:,.2f}, "
            f"PnL=${pnl:,.2f} ({profit_rate:+.2f}%), 사유={exit_reason}"
        )
        
        # 포지션 초기화
        self.current_position = None
        
        return trade_record
    
    def reset_daily_stats(self):
        """일일 통계 초기화"""
        self.daily_trades = 0
        self.daily_pnl = 0.0
        logger.info("일일 통계 초기화")
    
    def get_daily_stats(self) -> Dict[str, Any]:
        """일일 통계 반환"""
        winning_trades = sum(
            1 for trade in self.trade_history 
            if trade['pnl'] > 0 and trade['exit_date'].date() == datetime.now().date()
        )
        
        return {
            'daily_trades': self.daily_trades,
            'daily_pnl': self.daily_pnl,
            'winning_trades': winning_trades
        }
    
    def get_total_stats(self) -> Dict[str, Any]:
        """전체 통계 반환"""
        if not self.trade_history:
            return {
                'total_trades': 0,
                'total_pnl': 0.0,
                'win_rate': 0.0
            }
        
        total_trades = len(self.trade_history)
        total_pnl = sum(trade['pnl'] for trade in self.trade_history)
        winning_trades = sum(1 for trade in self.trade_history if trade['pnl'] > 0)
        win_rate = (winning_trades / total_trades) * 100
        
        return {
            'total_trades': total_trades,
            'total_pnl': total_pnl,
            'win_rate': win_rate,
            'winning_trades': winning_trades
        }
