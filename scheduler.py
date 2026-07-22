"""Binance UTC 4H 캔들 종료 직후 엔진을 실행하는 스케줄러."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def next_candle_check_time(
    now: datetime,
    interval_hours: int = 4,
    delay_minutes: int = 1,
) -> datetime:
    if now.tzinfo is None:
        now = now.astimezone()
    now_utc = now.astimezone(timezone.utc)
    boundary_hour = (now_utc.hour // interval_hours) * interval_hours
    target_utc = now_utc.replace(
        hour=boundary_hour,
        minute=delay_minutes,
        second=0,
        microsecond=0,
    )
    if now_utc >= target_utc:
        target_utc += timedelta(hours=interval_hours)
    return target_utc.astimezone(now.tzinfo)


class Scheduler:
    def __init__(self, engine):
        self.engine = engine
        self.next_run_at = next_candle_check_time(datetime.now().astimezone())

    def run_forever(self) -> None:
        self.engine.run_once(allow_entry=False)
        while True:
            now = datetime.now().astimezone()
            wait_seconds = max(0.0, (self.next_run_at - now).total_seconds())
            logger.info(
                "다음 Binance 4H 완료봉 점검 시각: %s (대기 %.1f분)",
                self.next_run_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
                wait_seconds / 60,
            )
            time.sleep(wait_seconds)

            now = datetime.now().astimezone()
            logger.info("새 Binance 4H 완료봉 점검 실행: %s", now.strftime("%Y-%m-%d %H:%M:%S %Z"))
            self.engine.run_once()
            self.next_run_at = next_candle_check_time(datetime.now().astimezone())

