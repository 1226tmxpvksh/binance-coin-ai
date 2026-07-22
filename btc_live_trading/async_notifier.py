"""
비동기 알림 래퍼
알림 실패가 거래를 방해하지 않도록 별도 스레드에서 처리한다.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

logger = logging.getLogger(__name__)


class AsyncNotifier:
    """알림을 별도 스레드에서 처리한다."""

    def __init__(self, notifier, max_queue_size: int = 100):
        self.notifier = notifier
        self.notification_queue: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue(
            maxsize=max_queue_size
        )
        self.worker_thread: threading.Thread | None = None
        self.is_running = False
        self.failed_count = 0
        self.success_count = 0

    def is_alive(self) -> bool:
        """워커 스레드가 실제로 살아 있는지 확인 (is_running 플래그만 믿지 않는다)."""
        return (
            self.is_running
            and self.worker_thread is not None
            and self.worker_thread.is_alive()
        )

    def start(self):
        if self.is_alive():
            logger.warning("알림 워커가 이미 실행 중입니다")
            return
        if self.is_running and not self.is_alive():
            logger.error("알림 워커 스레드가 비정상 종료된 상태 — 재기동합니다")
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def stop(self, *, drain_timeout: float = 5.0) -> None:
        if not self.is_running:
            return
        self.is_running = False
        if self.worker_thread is not None:
            self.worker_thread.join(timeout=drain_timeout)

    def _worker(self):
        try:
            while self.is_running or not self.notification_queue.empty():
                try:
                    method_name, args = self.notification_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                try:
                    method = getattr(self.notifier, method_name)
                    result = method(*args)
                    if result is False:
                        self.failed_count += 1
                    else:
                        self.success_count += 1
                except Exception as exc:
                    self.failed_count += 1
                    logger.error("비동기 알림 처리 실패: %s", exc)
                finally:
                    self.notification_queue.task_done()
        except BaseException:
            # 워커가 소리 없이 죽어 메시지가 증발하는 것을 방지:
            # 플래그를 내려 enqueue가 False를 반환하게 하고(호출측 동기 폴백 유도) 원인을 남긴다.
            logger.exception("비동기 알림 워커 스레드 비정상 종료 — 다음 알림 시 재기동됩니다")
            self.is_running = False
            raise

    def _enqueue_notification(self, method_name: str, *args: Any) -> bool:
        if not self.is_alive():
            return False
        try:
            self.notification_queue.put_nowait((method_name, args))
            return True
        except queue.Full:
            logger.warning("알림 큐가 가득 차서 알림을 건너뜁니다: %s", method_name)
            return False

    def send_message(self, title: str, description: str) -> bool:
        return self._enqueue_notification("send_message", title, description)

    def send_message_sync(self, title: str, description: str) -> bool:
        """종료·긴급 알림 등 즉시 전송이 필요할 때 동기 호출."""
        try:
            result = self.notifier.send_message(title, description)
            if result:
                self.success_count += 1
            else:
                self.failed_count += 1
            return bool(result)
        except Exception as exc:
            self.failed_count += 1
            logger.error("동기 알림 처리 실패: %s", exc)
            return False

    def notify_start(self):
        return self._enqueue_notification("notify_start")

    def notify_dry_run_mode(self):
        return self._enqueue_notification("notify_dry_run_mode")

    def notify_entry_signal(self, side: str, price: float, market_state: str, reason: str):
        return self._enqueue_notification("notify_entry_signal", side, price, market_state, reason)

    def notify_order_filled(
        self,
        side: str,
        entry_price: float,
        size: float,
        stop_loss: float,
        take_profit: float,
        position_value: float,
        order_type: str = "MARKET",
    ):
        return self._enqueue_notification(
            "notify_order_filled",
            side,
            entry_price,
            size,
            stop_loss,
            take_profit,
            position_value,
            order_type,
        )

    def notify_position_closed(
        self,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        profit_rate: float,
        reason: str,
        balance: float,
    ):
        return self._enqueue_notification(
            "notify_position_closed",
            side,
            entry_price,
            exit_price,
            pnl,
            profit_rate,
            reason,
            balance,
        )

    def notify_error(self, error_message: str):
        return self._enqueue_notification("notify_error", error_message)

    def notify_emergency_stop(self, reason: str, total_loss: float):
        return self._enqueue_notification("notify_emergency_stop", reason, total_loss)

    def notify_daily_summary(
        self,
        total_trades: int,
        winning_trades: int,
        daily_pnl: float,
        balance: float,
    ):
        return self._enqueue_notification(
            "notify_daily_summary",
            total_trades,
            winning_trades,
            daily_pnl,
            balance,
        )

    def notify_insufficient_balance(self, current_balance_usdt: float, required_min_usdt: float):
        return self._enqueue_notification(
            "notify_insufficient_balance",
            current_balance_usdt,
            required_min_usdt,
        )

    def notify_no_entry(self, market_state: str, reason: str):
        return self._enqueue_notification("notify_no_entry", market_state, reason)

    def get_stats(self) -> dict:
        return {
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "queue_size": self.notification_queue.qsize(),
            "is_running": self.is_running,
        }
