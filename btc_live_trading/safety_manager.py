"""
안전장치 모듈
손실 제한, 긴급 정지 등 리스크 관리
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, date

logger = logging.getLogger(__name__)

# 퍼센트 계산 시 분모로 쓰기 위한 최소 자본 (이하이면 나눗셈 대신 거래 불가 처리)
MIN_CAPITAL_FOR_PCT_RULES = 1e-8


class SafetyManager:
    """안전장치 관리 클래스"""
    
    def __init__(
        self,
        initial_capital: float,
        daily_max_loss_percent: float,
        emergency_stop_loss_percent: float,
        max_trades_per_day: int
    ):
        """
        Args:
            initial_capital: 초기 자본
            daily_max_loss_percent: 일일 최대 손실 (%)
            emergency_stop_loss_percent: 긴급 정지 손실 (%)
            max_trades_per_day: 일일 최대 거래 횟수
        """
        self.initial_capital = initial_capital
        self.daily_max_loss_percent = daily_max_loss_percent
        self.emergency_stop_loss_percent = emergency_stop_loss_percent
        self.max_trades_per_day = max_trades_per_day
        
        # 일일 손실 추적
        self.today = date.today()
        self.daily_loss = 0.0
        self.daily_trades = 0
        
        # 긴급 정지 플래그
        self.emergency_stopped = False
        self.emergency_reason = ""
        # 입출금(TRANSFER) 누적 조회 구간 시작 시각(ms) — refresh 시 갱신
        self.initial_capital_baseline_ms = int(time.time() * 1000)
    
    def refresh_initial_capital_from_balance(self, balance: Optional[float]) -> None:
        """
        긴급 정지(누적 손실 %) 계산의 기준 자본을 현재 잔고로 맞춤.
        거래 시각 흐름이 끝난 뒤 호출해 다음 날까지의 기준점을 저장한다.

        Note:
            직전 refresh 이후 바이낸스 TRANSFER(입출금) 합계로 손실 %를 보정하므로,
            출금만 한 경우 거래 손실로 과대 계산되는 문제를 완화할 수 있다.
        """
        if balance is None:
            logger.warning(
                "기준 자본(initial_capital) 갱신 스킵: 잔고 조회 결과가 None 입니다."
            )
            return
        if balance <= MIN_CAPITAL_FOR_PCT_RULES:
            logger.warning(
                "기준 자본(initial_capital) 갱신 스킵: 잔고가 유효 범위 이하입니다. "
                f"({balance:,.8f} USDT)"
            )
            return
        previous = self.initial_capital
        self.initial_capital = balance
        self.initial_capital_baseline_ms = int(time.time() * 1000)
        logger.info(
            f"기준 자본(initial_capital) 갱신: {previous:,.2f} → {balance:,.2f} USDT"
        )
    
    def reset_daily_stats(self):
        """일일 통계 초기화"""
        today = date.today()
        
        if today != self.today:
            logger.info(f"날짜 변경: {self.today} → {today}, 일일 통계 초기화")
            self.today = today
            self.daily_loss = 0.0
            self.daily_trades = 0
    
    def update_daily_pnl(self, pnl: float):
        """
        일일 손익 업데이트
        
        Args:
            pnl: 손익
        """
        self.reset_daily_stats()
        
        if pnl < 0:
            self.daily_loss += abs(pnl)
            logger.debug(f"일일 손실 업데이트: ${self.daily_loss:,.2f}")
    
    def increment_daily_trades(self):
        """일일 거래 횟수 증가"""
        self.reset_daily_stats()
        self.daily_trades += 1
        logger.debug(f"일일 거래 횟수: {self.daily_trades}")
    
    def check_daily_loss_limit(self, current_capital: float) -> bool:
        """
        일일 손실 한도 체크
        
        Args:
            current_capital: 현재 자본
        
        Returns:
            bool: 거래 가능하면 True
        """
        self.reset_daily_stats()
        
        if current_capital <= MIN_CAPITAL_FOR_PCT_RULES:
            logger.warning(
                "일일 손실 한도 계산 불가: 현재 자본이 거의 0입니다 "
                f"(${current_capital:,.2f}). 입금·API·선물 지갑을 확인하세요."
            )
            return False
        
        daily_loss_percent = (self.daily_loss / current_capital) * 100
        
        if daily_loss_percent >= self.daily_max_loss_percent:
            logger.warning(
                f"⚠️ 일일 최대 손실 도달: "
                f"{daily_loss_percent:.2f}% >= {self.daily_max_loss_percent}%"
            )
            return False
        
        return True
    
    def check_trade_limit(self) -> bool:
        """
        일일 거래 횟수 제한 체크
        
        Returns:
            bool: 거래 가능하면 True
        """
        self.reset_daily_stats()
        
        if self.daily_trades >= self.max_trades_per_day:
            logger.warning(
                f"⚠️ 일일 거래 한도 도달: "
                f"{self.daily_trades} >= {self.max_trades_per_day}"
            )
            return False
        
        return True
    
    def check_emergency_stop(
        self,
        current_capital: float,
        external_transfer_net_usdt: float = 0.0,
    ) -> bool:
        """
        긴급 정지 조건 체크
        
        Args:
            current_capital: 현재 자본 (선물 지갑 USDT 등)
            external_transfer_net_usdt: 기준 시각 이후 바이낸스 TRANSFER income 합계 (입금+ / 출금-).
                거래 손실과 분리하기 위해 current에서 빼 보정한다.
                adjusted = current_capital - external_transfer_net_usdt
        
        Returns:
            bool: 정지해야 하면 True
        """
        # 이미 정지 상태면
        if self.emergency_stopped:
            return True
        
        if self.initial_capital <= MIN_CAPITAL_FOR_PCT_RULES:
            logger.error(
                "긴급 정지 퍼센트 계산 불가: initial_capital 설정이 "
                f"{self.initial_capital:,.2f} 입니다. config를 확인하세요."
            )
            return False
        
        adjusted_capital = current_capital - external_transfer_net_usdt
        if abs(external_transfer_net_usdt) > MIN_CAPITAL_FOR_PCT_RULES:
            logger.debug(
                "긴급 정지 % 산출: 잔고 %s, TRANSFER 누적 %s → 보정 순잔고 %s USDT",
                f"{current_capital:,.2f}",
                f"{external_transfer_net_usdt:,.2f}",
                f"{adjusted_capital:,.2f}",
            )
        
        # 총 손실 계산 (외부 입출금 보정 반영)
        total_loss_percent = (
            (self.initial_capital - adjusted_capital) / self.initial_capital
        ) * 100
        
        if total_loss_percent >= self.emergency_stop_loss_percent:
            self.emergency_stopped = True
            self.emergency_reason = (
                f"총 손실 {total_loss_percent:.2f}% 도달 "
                f"(한도: {self.emergency_stop_loss_percent}%)"
            )
            logger.critical(f"🚨 긴급 정지! {self.emergency_reason}")
            return True
        
        return False
    
    def can_trade(
        self,
        current_capital: Optional[float],
        external_transfer_net_usdt: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        거래 가능 여부 종합 체크
        
        Args:
            current_capital: 현재 자본
            external_transfer_net_usdt: TRANSFER 입출금 누적(입금+ / 출금-). 긴급 정지 %만 보정.
        
        Returns:
            tuple[bool, str]: (거래 가능 여부, 사유)
        """
        if current_capital is None:
            return False, "잔고 조회 실패"
        
        if current_capital <= MIN_CAPITAL_FOR_PCT_RULES:
            return (
                False,
                "USDT 잔고가 0에 가깝습니다. 선물 지갑 입금·API 권한을 확인하세요.",
            )
        
        # 긴급 정지 체크
        if self.check_emergency_stop(
            current_capital,
            external_transfer_net_usdt=external_transfer_net_usdt,
        ):
            return False, f"긴급 정지: {self.emergency_reason}"
        
        # 일일 손실 한도 체크
        if not self.check_daily_loss_limit(current_capital):
            return False, "일일 손실 한도 초과"
        
        # 일일 거래 횟수 체크
        if not self.check_trade_limit():
            return False, "일일 거래 횟수 초과"
        
        return True, "OK"
    
    def get_status(self, current_capital: float) -> Dict[str, Any]:
        """
        현재 안전장치 상태
        
        Args:
            current_capital: 현재 자본
        
        Returns:
            Dict: 상태 정보
        """
        if self.initial_capital <= MIN_CAPITAL_FOR_PCT_RULES:
            total_loss_percent = 0.0
        else:
            total_loss_percent = (
                (self.initial_capital - current_capital) / self.initial_capital
            ) * 100
        
        if current_capital <= MIN_CAPITAL_FOR_PCT_RULES:
            daily_loss_percent = 0.0
        else:
            daily_loss_percent = (self.daily_loss / current_capital) * 100
        
        return {
            'emergency_stopped': self.emergency_stopped,
            'emergency_reason': self.emergency_reason,
            'total_loss_percent': total_loss_percent,
            'daily_loss_percent': daily_loss_percent,
            'daily_trades': self.daily_trades,
            'daily_max_loss_limit': self.daily_max_loss_percent,
            'emergency_stop_limit': self.emergency_stop_loss_percent,
            'trade_limit': self.max_trades_per_day
        }
