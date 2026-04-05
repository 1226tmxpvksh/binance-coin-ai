"""
비동기 알림 래퍼
알림 실패가 거래를 방해하지 않도록 보장
"""

import logging
import threading
import queue
import time
from typing import Optional, Callable, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class AsyncNotifier:
    """
    비동기 알림 래퍼
    알림을 별도 스레드에서 처리하여 거래 로직을 방해하지 않음
    """
    
    def __init__(self, notifier, max_queue_size: int = 100):
        """
        Args:
            notifier: 실제 알림 객체 (KakaoNotifier)
            max_queue_size: 알림 큐 최대 크기
        """
        self.notifier = notifier
        self.notification_queue = queue.Queue(maxsize=max_queue_size)
        self.worker_thread = None
        self.is_running = False
        self.failed_count = 0
        self.success_count = 0
        
    def start(self):
        """워커 스레드 시작"""
        if self.is_running:
            logger.warning("알림 워커가 이미 실행 중입니다")
            return
        
        self.is_running = True
        self.worker_thread = threading.Thread(
            target=self._worker,
            name="NotificationWorker",
            daemon=True  # 메인 스레드 종료 시 함께 종료
        )
        self.worker_thread.start()
        logger.info("비동기 알림 워커 시작")
    
    def stop(self):
        """워커 스레드 중지"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # 종료 신호 전송
        try:
            self.notification_queue.put(("STOP", None, None), timeout=1)
        except queue.Full:
            pass
        
        # 워커 스레드 대기 (최대 3초)
        if self.worker_thread:
            self.worker_thread.join(timeout=3)
        
        logger.info(
            f"비동기 알림 워커 종료 "
            f"(성공: {self.success_count}, 실패: {self.failed_count})"
        )
    
    def _worker(self):
        """알림 처리 워커 (별도 스레드)"""
        logger.info("알림 워커 스레드 실행 중...")
        
        while self.is_running:
            try:
                # 큐에서 알림 가져오기 (1초 타임아웃)
                item = self.notification_queue.get(timeout=1)
                
                if item[0] == "STOP":
                    break
                
                method_name, args, kwargs = item
                
                # 알림 메서드 호출
                start_time = time.time()
                method = getattr(self.notifier, method_name, None)
                
                if method is None:
                    logger.error(f"알림 메서드를 찾을 수 없습니다: {method_name}")
                    self.failed_count += 1
                    continue
                
                try:
                    result = method(*args, **kwargs)
                    elapsed = time.time() - start_time
                    
                    if result:
                        self.success_count += 1
                        logger.debug(
                            f"알림 전송 성공: {method_name} ({elapsed:.2f}초)"
                        )
                    else:
                        self.failed_count += 1
                        logger.warning(
                            f"알림 전송 실패: {method_name} ({elapsed:.2f}초)"
                        )
                
                except Exception as e:
                    self.failed_count += 1
                    logger.error(
                        f"알림 전송 중 예외 발생: {method_name}, "
                        f"오류: {type(e).__name__}"
                    )
                
            except queue.Empty:
                # 타임아웃 - 다시 확인
                continue
            
            except Exception as e:
                logger.error(f"워커 루프 오류: {type(e).__name__}")
                time.sleep(1)
        
        logger.info("알림 워커 스레드 종료")
    
    def _enqueue_notification(
        self,
        method_name: str,
        *args,
        **kwargs
    ) -> bool:
        """
        알림을 큐에 추가
        
        Args:
            method_name: 호출할 메서드 이름
            *args: 위치 인자
            **kwargs: 키워드 인자
        
        Returns:
            bool: 큐 추가 성공 여부
        """
        if not self.is_running:
            logger.warning(f"알림 워커가 실행 중이 아닙니다: {method_name}")
            return False
        
        try:
            # 큐가 가득 차면 0.1초만 대기 (거래 지연 최소화)
            self.notification_queue.put(
                (method_name, args, kwargs),
                timeout=0.1
            )
            return True
        
        except queue.Full:
            logger.error(
                f"알림 큐가 가득 찼습니다 ({self.notification_queue.qsize()}). "
                f"알림 건너뜀: {method_name}"
            )
            return False
    
    # ==========================================================================
    # 알림 메서드들 (모두 비동기로 처리)
    # ==========================================================================
    
    def notify_start(self):
        """프로그램 시작 알림"""
        return self._enqueue_notification('notify_start')
    
    def notify_dry_run_mode(self):
        """테스트 모드 알림"""
        return self._enqueue_notification('notify_dry_run_mode')
    
    def notify_entry_signal(
        self,
        side: str,
        price: float,
        market_state: str,
        reason: str
    ):
        """진입 신호 알림"""
        return self._enqueue_notification(
            'notify_entry_signal',
            side, price, market_state, reason
        )
    
    def notify_order_filled(
        self,
        side: str,
        entry_price: float,
        size: float,
        stop_loss: float,
        take_profit: float,
        position_value: float,
        order_type: str = "MARKET"
    ):
        """주문 체결 알림"""
        return self._enqueue_notification(
            'notify_order_filled',
            side, entry_price, size, stop_loss, take_profit,
            position_value, order_type
        )
    
    def notify_position_closed(
        self,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        profit_rate: float,
        reason: str,
        balance: float
    ):
        """포지션 청산 알림"""
        return self._enqueue_notification(
            'notify_position_closed',
            side, entry_price, exit_price, pnl, profit_rate, reason, balance
        )
    
    def notify_error(self, error_message: str):
        """에러 알림"""
        return self._enqueue_notification('notify_error', error_message)
    
    def notify_emergency_stop(self, reason: str, total_loss: float):
        """긴급 정지 알림"""
        return self._enqueue_notification(
            'notify_emergency_stop',
            reason, total_loss
        )
    
    def notify_daily_summary(
        self,
        total_trades: int,
        winning_trades: int,
        daily_pnl: float,
        balance: float
    ):
        """일일 요약 알림"""
        return self._enqueue_notification(
            'notify_daily_summary',
            total_trades, winning_trades, daily_pnl, balance
        )
    
    def notify_insufficient_balance(
        self,
        current_balance_usdt: float,
        required_min_usdt: float,
    ):
        """최소 진입 잔고 미만 알림"""
        return self._enqueue_notification(
            'notify_insufficient_balance',
            current_balance_usdt,
            required_min_usdt,
        )
    
    def get_stats(self) -> dict:
        """알림 통계 반환"""
        return {
            'success_count': self.success_count,
            'failed_count': self.failed_count,
            'queue_size': self.notification_queue.qsize(),
            'is_running': self.is_running
        }


class SilentNotifier:
    """
    조용한 알림 (알림 비활성화 시 사용)
    모든 메서드가 아무것도 하지 않음
    """
    
    def __getattr__(self, name):
        """모든 메서드 호출을 무시"""
        def silent_method(*args, **kwargs):
            return True
        return silent_method
