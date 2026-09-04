"""
디스코드 웹훅 알림 모듈

OAuth/토큰 갱신 없이 고정 웹훅 URL로 embed 메시지를 전송한다.
KakaoNotifier 와 동일하게 send_message(title, description) -> bool 인터페이스를 제공한다.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
EMBED_TITLE_MAX = 256
EMBED_DESCRIPTION_MAX = 4096
DEFAULT_EMBED_COLOR = 0x5865F2  # Discord blurple
_MISSING_URL_WARNED = False


def _mask_webhook_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return "(없음)"
    if len(u) <= 24:
        return f"{u[:8]}…(len={len(u)})"
    return f"{u[:24]}…{u[-6:]}(len={len(u)})"


def _truncate(text: str, limit: int) -> str:
    t = text or ""
    if len(t) <= limit:
        return t
    marker = "...(생략)"
    keep = max(0, limit - len(marker))
    return t[:keep] + marker


class DiscordNotifier:
    """디스코드 Incoming Webhook 알림."""

    def __init__(
        self,
        webhook_url: str = "",
        enabled: bool = True,
        *,
        embed_color: int = DEFAULT_EMBED_COLOR,
    ):
        global _MISSING_URL_WARNED
        self.enabled = bool(enabled)
        self.embed_color = int(embed_color)
        url = (webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "")).strip()
        if not url:
            self.webhook_url = ""
            self.enabled = False
            if not _MISSING_URL_WARNED:
                logger.warning(
                    "DISCORD_WEBHOOK_URL 이 없어 DiscordNotifier 를 비활성화합니다. "
                    "btc_live_trading/.env 에 웹훅 URL을 설정하세요."
                )
                _MISSING_URL_WARNED = True
        else:
            self.webhook_url = url

    def send_message(self, title: str, description: str, retry_count: int = 0) -> bool:
        """Discord embed 1건 전송. 실패해도 예외를 호출자에게 전파하지 않는다."""
        if not self.enabled or not self.webhook_url:
            logger.debug("디스코드 알림 비활성화/URL 없음: %s", (title or "")[:60])
            return False

        safe_title = _truncate((title or "").strip() or "(제목 없음)", EMBED_TITLE_MAX)
        safe_body = _truncate(description or "", EMBED_DESCRIPTION_MAX)
        payload = {
            "embeds": [
                {
                    "title": safe_title,
                    "description": safe_body,
                    "color": self.embed_color,
                }
            ]
        }

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            logger.error(
                "[CRITICAL DISCORD] Timeout — 웹훅 전송 타임아웃: %s url=%s",
                exc,
                _mask_webhook_url(self.webhook_url),
            )
            return False
        except requests.ConnectionError as exc:
            logger.error(
                "[CRITICAL DISCORD] ConnectionError — %s url=%s",
                exc,
                _mask_webhook_url(self.webhook_url),
            )
            return False
        except requests.RequestException as exc:
            logger.error(
                "[CRITICAL DISCORD] RequestException — %s: %s url=%s",
                type(exc).__name__,
                exc,
                _mask_webhook_url(self.webhook_url),
            )
            return False
        except Exception as exc:
            logger.error(
                "[CRITICAL DISCORD] %s — %s url=%s",
                type(exc).__name__,
                exc,
                _mask_webhook_url(self.webhook_url),
            )
            return False

        # Discord 성공: 204 No Content (또는 200)
        if response.status_code in (200, 204):
            logger.info("디스코드 알림 전송 성공: %s", safe_title[:80])
            return True

        if response.status_code == 429 and retry_count < 1:
            retry_after = 1.0
            try:
                retry_after = float(response.headers.get("Retry-After") or 1.0)
            except (TypeError, ValueError):
                try:
                    body = response.json()
                    retry_after = float(body.get("retry_after") or 1.0)
                except Exception:
                    retry_after = 1.0
            retry_after = max(0.2, min(retry_after, 5.0))
            logger.warning(
                "디스코드 rate limit(429) — %.1fs 후 1회 재시도 title=%s",
                retry_after,
                safe_title[:60],
            )
            time.sleep(retry_after)
            return self.send_message(title, description, retry_count + 1)

        logger.error(
            "[CRITICAL DISCORD] 웹훅 전송 실패 HTTP=%s body=%s url=%s title=%s",
            response.status_code,
            (response.text or "")[:300],
            _mask_webhook_url(self.webhook_url),
            safe_title[:80],
        )
        return False

    def notify_start(self) -> bool:
        from datetime import datetime

        return self.send_message(
            "실전 매매 시작",
            f"프로그램이 시작되었습니다.\n시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )
