"""
백테스트 엔진
전략을 과거 데이터에 적용하여 성과를 시뮬레이션
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

from indicators import add_all_indicators, calculate_k_value
from market_regime import determine_market_state, MarketState
from strategy_entry import evaluate_entry_signal, calculate_entry_price, PositionSide
from strategy_exit import (
    calculate_initial_stops,
    check_basic_exit,
    check_long_hold_extension,
    calculate_trailing_stop,
    check_extension_exit,
    should_force_short_exit
)
from risk_manager import (
    calculate_position_size,
    calculate_position_value,
    calculate_pnl,
    calculate_profit_rate,
    can_open_position
)

logger = logging.getLogger(__name__)


class Position:
    """포지션 정보를 담는 클래스"""
    
    def __init__(
        self,
        side: str,
        entry_date: datetime,
        entry_price: float,
        size: float,
        stop_loss: float,
        take_profit: float,
        atr: float
    ):
        self.side = side
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.size = size
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.atr = atr
        self.hold_days = 0
        self.is_extended = False
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'side': self.side,
            'entry_date': self.entry_date,
            'entry_price': self.entry_price,
            'size': self.size,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'hold_days': self.hold_days,
            'is_extended': self.is_extended
        }


class BacktestEngine:
    """백테스트 실행 엔진"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: 설정 딕셔너리
        """
        self.config = config
        self.initial_capital = config['INITIAL_CAPITAL']
        self.capital = self.initial_capital
        self.position: Optional[Position] = None
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = []
    
    def run(self, data: pd.DataFrame) -> Dict[str, Any]:
        """
        백테스트 실행
        
        Args:
            data: 지표가 추가된 OHLCV 데이터
        
        Returns:
            Dict: 백테스트 결과
        """
        logger.info("백테스트 시작")
        
        # 초기화
        self.capital = self.initial_capital
        self.position = None
        self.trades = []
        self.equity_curve = []
        
        # 데이터 검증
        if data is None or len(data) < self.config['SMA_LONG_PERIOD']:
            logger.error("백테스트를 위한 충분한 데이터가 없습니다")
            return {}
        
        # 각 캔들 순회
        for i in range(self.config['SMA_LONG_PERIOD'], len(data)):
            current_date = data.index[i]
            current_row = data.iloc[i]
            prev_row = data.iloc[i-1]
            
            # 포지션이 있는 경우 청산 체크
            if self.position is not None:
                self._check_exit(current_date, current_row, prev_row)
            
            # 포지션이 없는 경우 진입 체크
            if self.position is None:
                self._check_entry(current_date, current_row, prev_row, data.iloc[:i+1])
            
            # 자본 곡선 기록
            current_equity = self._calculate_current_equity(current_row)
            self.equity_curve.append({
                'date': current_date,
                'equity': current_equity,
                'has_position': self.position is not None
            })
        
        # 마지막 포지션이 남아있으면 강제 청산
        if self.position is not None:
            last_row = data.iloc[-1]
            self._force_close_position(data.index[-1], last_row['close'], "end_of_data")
        
        logger.info(f"백테스트 완료: {len(self.trades)}개 거래")
        
        return {
            'trades': self.trades,
            'equity_curve': self.equity_curve,
            'final_capital': self.capital
        }
    
    def _check_entry(
        self,
        current_date: datetime,
        current_row: pd.Series,
        prev_row: pd.Series,
        historical_data: pd.DataFrame
    ):
        """진입 조건 체크"""
        
        # 포지션 오픈 가능 여부
        if not can_open_position(
            self.position is not None,
            self.config['SINGLE_POSITION_ONLY']
        ):
            return
        
        # 시장 상태 판단
        market_state = determine_market_state(
            current_row['close'],
            current_row['ema_short'],
            current_row['ema_long'],
            current_row['sma_long'],
            current_row['sma_slope']
        )
        
        # 중립 구간이면 거래 안 함
        if market_state == MarketState.NEUTRAL:
            return
        
        # K값 계산
        atr_lookback = historical_data['atr'].tail(self.config['ATR_PERIOD'])
        atr_max = atr_lookback.max()
        k_value = calculate_k_value(
            current_row['atr'],
            atr_max,
            self.config['K_MAX']
        )
        
        # 진입 신호 평가
        current_data = {
            'high': current_row['high'],
            'low': current_row['low'],
            'open': current_row['open'],
            'close': current_row['close'],
            'volume': current_row['volume']
        }
        
        prev_data = {
            'high': prev_row['high'],
            'low': prev_row['low'],
            'volume': prev_row['volume'],
            'vol_ma': prev_row['vol_ma']
        }
        
        signal = evaluate_entry_signal(
            market_state.value,
            current_data,
            prev_data,
            k_value
        )
        
        # 진입 신호가 있으면 포지션 오픈
        if signal is not None and signal != PositionSide.NONE:
            self._open_position(
                current_date,
                signal.value,
                current_row['open'],
                current_row['atr']
            )
    
    def _open_position(
        self,
        entry_date: datetime,
        side: str,
        entry_price: float,
        atr: float
    ):
        """포지션 오픈"""
        
        # 손절/익절 계산
        stop_loss, take_profit = calculate_initial_stops(
            entry_price,
            atr,
            side,
            self.config['STOP_LOSS_MULTIPLIER'],
            self.config['TAKE_PROFIT_MULTIPLIER']
        )
        
        # 포지션 사이즈 계산
        position_size = calculate_position_size(
            self.capital,
            entry_price,
            stop_loss,
            self.config['RISK_PER_TRADE'],
            self.config['LEVERAGE']
        )
        
        if position_size <= 0:
            logger.warning("포지션 사이즈가 0입니다. 진입 취소")
            return
        
        # 최소 포지션 크기 체크 (실전 설정에만 있음)
        if 'MIN_POSITION_SIZE_USDT' in self.config:
            position_value = position_size * entry_price
            min_size = self.config['MIN_POSITION_SIZE_USDT']
            
            if position_value < min_size:
                logger.warning(
                    f"포지션이 최소 크기보다 작습니다: "
                    f"${position_value:.2f} < ${min_size:.2f}"
                )
                return
        
        # 포지션 생성
        self.position = Position(
            side=side,
            entry_date=entry_date,
            entry_price=entry_price,
            size=position_size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr
        )
        
        logger.info(
            f"포지션 오픈: {side} @ {entry_price:.2f}, "
            f"size={position_size:.6f}, SL={stop_loss:.2f}, TP={take_profit:.2f}"
        )
    
    def _check_exit(
        self,
        current_date: datetime,
        current_row: pd.Series,
        prev_row: pd.Series
    ):
        """청산 조건 체크"""
        
        if self.position is None:
            return
        
        # 보유 일수 증가
        self.position.hold_days += 1
        
        # 기본 손절/익절 체크
        should_exit, exit_reason, exit_price = check_basic_exit(
            current_row['high'],
            current_row['low'],
            current_row['close'],
            self.position.stop_loss,
            self.position.take_profit,
            self.position.side
        )
        
        if should_exit:
            self._close_position(current_date, exit_price, exit_reason)
            return
        
        # SHORT는 익일 강제 청산
        if should_force_short_exit(self.position.side, self.position.hold_days):
            self._close_position(current_date, current_row['close'], "short_day_exit")
            return
        
        # LONG인 경우 확장 보유 체크
        if self.position.side == "LONG":
            self._check_long_extension(current_date, current_row)
    
    def _check_long_extension(
        self,
        current_date: datetime,
        current_row: pd.Series
    ):
        """LONG 확장 보유 체크"""
        
        # 보유 연장 가능한지 확인
        can_extend = check_long_hold_extension(
            current_row['close'],
            self.position.entry_price,
            current_row['ema_short'],
            current_row['ema_long']
        )
        
        if can_extend:
            # 확장 모드 활성화
            if not self.position.is_extended:
                self.position.is_extended = True
                logger.info(f"LONG 확장 모드 진입: {current_date}")
            
            # 트레일링 스탑 업데이트
            self.position.stop_loss = calculate_trailing_stop(
                self.position.stop_loss,
                current_row['ema_short'],
                current_row['atr'],
                self.config['TRAILING_STOP_ATR_MULT']
            )
            
            # 확장 중 청산 조건 체크
            should_exit, exit_reason = check_extension_exit(
                current_row['close'],
                current_row['volume'],
                current_row['ema_short'],
                current_row['vol_ma'],
                self.position.hold_days,
                self.config['MAX_HOLD_DAYS']
            )
            
            if should_exit:
                self._close_position(current_date, current_row['close'], exit_reason)
        else:
            # 확장 조건 불충족 시 청산
            self._close_position(current_date, current_row['close'], "extension_fail")
    
    def _close_position(
        self,
        exit_date: datetime,
        exit_price: float,
        exit_reason: str
    ):
        """포지션 청산"""
        
        if self.position is None:
            return
        
        # 손익 계산
        pnl = calculate_pnl(
            self.position.size,
            self.position.entry_price,
            exit_price,
            self.position.side
        )
        
        # 수익률 계산
        position_value = calculate_position_value(
            self.position.size,
            self.position.entry_price
        )
        profit_rate = calculate_profit_rate(pnl, position_value)
        
        # 자본 업데이트
        self.capital += pnl
        
        # 거래 기록
        trade_record = {
            'entry_date': self.position.entry_date,
            'exit_date': exit_date,
            'side': self.position.side,
            'entry_price': self.position.entry_price,
            'exit_price': exit_price,
            'size': self.position.size,
            'pnl': pnl,
            'profit_rate': profit_rate,
            'hold_days': self.position.hold_days,
            'exit_reason': exit_reason,
            'is_extended': self.position.is_extended,
            'capital_after': self.capital
        }
        
        self.trades.append(trade_record)
        
        logger.info(
            f"포지션 청산: {self.position.side} @ {exit_price:.2f}, "
            f"PnL={pnl:.2f}, 수익률={profit_rate:.2f}%, "
            f"보유일={self.position.hold_days}, 사유={exit_reason}"
        )
        
        # 포지션 초기화
        self.position = None
    
    def _force_close_position(
        self,
        exit_date: datetime,
        exit_price: float,
        exit_reason: str
    ):
        """강제 청산"""
        logger.warning(f"강제 청산 실행: {exit_reason}")
        self._close_position(exit_date, exit_price, exit_reason)
    
    def _calculate_current_equity(self, current_row: pd.Series) -> float:
        """현재 자산 계산"""
        
        if self.position is None:
            return self.capital
        
        # 현재 가격으로 미실현 손익 계산
        unrealized_pnl = calculate_pnl(
            self.position.size,
            self.position.entry_price,
            current_row['close'],
            self.position.side
        )
        
        return self.capital + unrealized_pnl
