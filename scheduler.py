"""5분 주기로 새 4H 캔들 이후 엔진을 실행하는 스케줄러."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def next_candle_check_time(now: datetime, interval_hours: int = 4) -> datetime:
    next_hour = ((now.hour // interval_hours) + 1) * interval_hours
    if next_hour >= 24:
        return (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    return now.replace(hour=next_hour, minute=1, second=0, microsecond=0)


class Scheduler:
    def __init__(self, engine, poll_seconds: int = 300):
        self.engine = engine
        self.poll_seconds = poll_seconds
        self.next_run_at = next_candle_check_time(datetime.now())

    def run_forever(self) -> None:
        self.engine.run_once()
        while True:
            now = datetime.now()
            if now >= self.next_run_at:
                logger.info("새 캔들 점검 실행: %s", now.strftime("%Y-%m-%d %H:%M:%S"))
                self.engine.run_once()
                self.next_run_at = next_candle_check_time(datetime.now())
            logger.info("다음 점검 시각: %s", self.next_run_at.strftime("%Y-%m-%d %H:%M:%S"))
            time.sleep(self.poll_seconds)

