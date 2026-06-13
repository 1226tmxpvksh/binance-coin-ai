"""카카오톡 알림 모듈.

기존 btc_live_trading의 토큰 갱신/나에게 보내기 방식을 독립 폴더용으로 차용했다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

KAKAO_MEMO_API_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
KAKAO_TOKEN_API_URL = "https://kauth.kakao.com/oauth/token"
MAX_MESSAGE_LENGTH = 1000
REQUEST_TIMEOUT_SECONDS = 10


class KakaoNotifier:
    def __init__(
        self,
        access_token: str,
        enabled: bool,
        rest_api_key: str,
        refresh_token: str,
        env_path: Path,
    ):
        self.access_token = access_token
        self.enabled = enabled
        self.rest_api_key = rest_api_key
        self.refresh_token = refresh_token
        self.env_path = env_path

    def send_message(self, title: str, description: str, retry: bool = True) -> bool:
        if not self.enabled:
            return False
        if not self.access_token and not self._refresh_access_token():
            return False

        text = f"{title}\n{description}"
        if len(text) > MAX_MESSAGE_LENGTH:
            text = text[:MAX_MESSAGE_LENGTH]
        payload = {
            "object_type": "text",
            "text": text,
            "link": {"web_url": "https://www.binance.com", "mobile_web_url": "https://www.binance.com"},
        }
        try:
            response = requests.post(
                KAKAO_MEMO_API_URL,
                headers={"Authorization": f"Bearer {self.access_token}"},
                data={"template_object": json.dumps(payload)},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                return True
            if response.status_code == 401 and retry and self._refresh_access_token():
                return self.send_message(title, description, retry=False)
            logger.error("카카오 알림 실패: status=%s body=%s", response.status_code, response.text)
            return False
        except requests.RequestException as exc:
            logger.error("카카오 알림 네트워크 오류: %s", exc)
            return False

    def _refresh_access_token(self) -> bool:
        if not self.rest_api_key or not self.refresh_token:
            return False
        try:
            response = requests.post(
                KAKAO_TOKEN_API_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.rest_api_key,
                    "refresh_token": self.refresh_token,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                logger.error("카카오 토큰 갱신 실패: %s %s", response.status_code, response.text)
                return False
            token_data = response.json()
            self.access_token = token_data["access_token"]
            self.refresh_token = token_data.get("refresh_token", self.refresh_token)
            self._update_env_token("KAKAO_ACCESS_TOKEN", self.access_token)
            self._update_env_token("KAKAO_REFRESH_TOKEN", self.refresh_token)
            return True
        except Exception as exc:
            logger.error("카카오 토큰 갱신 오류: %s", exc)
            return False

    def _update_env_token(self, key: str, value: str) -> None:
        if not self.env_path.exists():
            return
        lines = self.env_path.read_text(encoding="utf-8").splitlines()
        prefix = f"{key}="
        updated = False
        for idx, line in enumerate(lines):
            if line.startswith(prefix):
                lines[idx] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")
        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def notify_start(self, dry_run: bool):
        mode = "DRY RUN" if dry_run else "LIVE"
        return self.send_message("BTC 실전 매매 시작", f"모드: {mode}\n시각: {datetime.now():%Y-%m-%d %H:%M:%S}")

    def notify_signal(self, target: float, stop: float, quantity: float, reason: str):
        return self.send_message(
            "BTC 진입 신호",
            f"목표가: ${target:,.2f}\n손절가: ${stop:,.2f}\n수량: {quantity:.6f} BTC\n사유: {reason}",
        )

    def notify_entry(self, entry_price: float, stop_price: float, quantity: float):
        return self.send_message(
            "BTC 포지션 진입",
            f"진입가: ${entry_price:,.2f}\n손절가: ${stop_price:,.2f}\n수량: {quantity:.6f} BTC",
        )

    def notify_stop_update(self, old_stop: float, new_stop: float):
        return self.send_message("BTC 손절 상향", f"기존: ${old_stop:,.2f}\n신규: ${new_stop:,.2f}")

    def notify_close(self, exit_price: float, pnl: float, reason: str):
        sign = "+" if pnl >= 0 else ""
        return self.send_message("BTC 포지션 청산", f"청산가: ${exit_price:,.2f}\n손익: {sign}${pnl:,.2f}\n사유: {reason}")

    def notify_no_entry(self, reason: str):
        return self.send_message("BTC 진입 없음", reason)

    def notify_error(self, message: str):
        return self.send_message("BTC 매매 오류", message)


class SilentNotifier:
    def __getattr__(self, name):
        def _method(*args, **kwargs):
            return True

        return _method

