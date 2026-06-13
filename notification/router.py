"""여러 알림 채널을 동시에 호출하는 라우터."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class NotificationRouter:
    def __init__(self, notifiers: list):
        self.notifiers = notifiers

    def __getattr__(self, name):
        def _dispatch(*args, **kwargs):
            ok = False
            for notifier in self.notifiers:
                method = getattr(notifier, name, None)
                if method is None:
                    continue
                try:
                    ok = bool(method(*args, **kwargs)) or ok
                except Exception as exc:
                    logger.error("알림 채널 실패: %s %s", name, exc)
            return ok

        return _dispatch

