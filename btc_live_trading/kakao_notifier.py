"""
카카오톡 알림 모듈
거래 신호, 체결, 청산, 에러를 간단히 전송한다.

토큰은 전송·갱신 직전 kakao_utils 정본에서 동기화하고,
갱신 HTTP는 refresh_kakao_access_token_sync() 단일 경로만 사용한다.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import requests

from kakao_utils import (
    ensure_access_token_for_login,
    get_access_token,
    get_refresh_token,
    hydrate_tokens_from_json,
    is_access_token_locally_fresh,
    kakao_api_allowed,
    refresh_kakao_access_token_sync,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 1000
REQUEST_TIMEOUT_SECONDS = 10
MAX_REFRESH_RETRY_COUNT = 1
TOKEN_REFRESH_MAX_ATTEMPTS = 3
TOKEN_REFRESH_RETRY_DELAY_SECONDS = 3
KAKAO_MEMO_API_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


class KakaoNotifier:
    """카카오톡 알림 클래스"""

    def __init__(
        self,
        access_token: str = "",
        enabled: bool = True,
        rest_api_key: str = "",
        refresh_token: str = "",
        env_path: Optional[str] = None,
        *,
        redirect_uri: Optional[str] = None,
        prompt_on_refresh_failure: bool = False,
    ):
        self.enabled = enabled
        self.rest_api_key = (rest_api_key or os.getenv("KAKAO_REST_API_KEY", "")).strip()
        self.api_url = KAKAO_MEMO_API_URL
        self.env_path = env_path or os.path.join(os.path.dirname(__file__), ".env")
        self.prompt_on_refresh_failure = prompt_on_refresh_failure
        # 생성자 인자는 초기값일 뿐 — send/refresh 직전 _sync_tokens_from_store()로 덮어씀
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._sync_tokens_from_store()

    def _sync_tokens_from_store(self) -> None:
        """kakao_code.json / os.environ 정본 → 인스턴스 동기화."""
        hydrate_tokens_from_json()
        live_access = get_access_token().strip()
        live_refresh = get_refresh_token().strip()
        if live_access:
            self.access_token = live_access
        if live_refresh and live_refresh != "your_refresh_token_here":
            self.refresh_token = live_refresh

    def _rest_api_key(self) -> str:
        key = (self.rest_api_key or os.getenv("KAKAO_REST_API_KEY", "")).strip()
        if key == "your_rest_api_key_here":
            return ""
        return key

    def send_message(self, title: str, description: str, retry_count: int = 0) -> bool:
        if not self.enabled:
            logger.debug("카카오 알림 비활성화: %s", title)
            return False

        if not kakao_api_allowed():
            logger.debug("환경 화이트리스트 불일치 — 카카오 메시지 전송 차단")
            return False

        self._sync_tokens_from_store()

        # 카톡 자동로그인처럼: 만료됐을 때만(또는 토큰 없을 때만) 로그인 시점에 갱신
        if (not self.access_token) or (not is_access_token_locally_fresh()):
            logger.info(
                "카카오 자동 로그인 — 알림 전송 직전 토큰 확보 (access=%s fresh=%s)",
                "있음" if self.access_token else "없음",
                is_access_token_locally_fresh(),
            )
            if not self._refresh_access_token(force=bool(self.access_token)):
                logger.error(
                    "카카오 자동 로그인 실패 — 액세스 토큰을 확보하지 못해 알림을 보내지 않습니다. "
                    "원인: refresh 실패 또는 토큰 파일 저장 실패. journalctl에서 "
                    "'토큰 파일 덮어쓰기 실패' / 'invalid_grant' 를 확인하세요."
                )
                return False
            self._sync_tokens_from_store()

        message_text = f"{title}\n{description}"
        if len(message_text) > MAX_MESSAGE_LENGTH:
            logger.warning(
                "카카오 메시지 길이 제한 초과: %s -> %s",
                len(message_text),
                MAX_MESSAGE_LENGTH,
            )
            message_text = message_text[:MAX_MESSAGE_LENGTH]

        payload = {
            "object_type": "text",
            "text": message_text,
            "link": {
                "web_url": "https://www.binance.com",
                "mobile_web_url": "https://www.binance.com",
            },
        }
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                data={"template_object": json.dumps(payload)},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                logger.info("카카오 알림 전송 성공: %s", title)
                return True

            if response.status_code == 401 and retry_count < MAX_REFRESH_RETRY_COUNT:
                logger.warning(
                    "카카오 액세스 토큰 만료/무효(HTTP 401). 로그인 재시도로 강제 갱신합니다. body=%s",
                    (response.text or "")[:200],
                )
                if self._refresh_access_token(force=True):
                    logger.info("카카오 액세스 토큰 갱신 성공. 메시지 전송을 재시도합니다.")
                    return self.send_message(title, description, retry_count + 1)
                logger.error(
                    "카카오 액세스 토큰 갱신 실패로 메시지 전송을 중단합니다. "
                    "원인: invalid_grant 또는 토큰 파일 저장 실패 가능."
                )
                return False

            logger.error(
                "카카오 알림 전송 실패: status=%s body=%s",
                response.status_code,
                (response.text or "")[:300],
            )
            return False
        except requests.RequestException as exc:
            logger.error("카카오 알림 전송 중 네트워크 오류: %s — 원인=%s", type(exc).__name__, exc)
            return False
        except Exception as exc:
            logger.error("카카오 알림 전송 중 오류: %s — 원인=%s", type(exc).__name__, exc)
            return False

    def _refresh_access_token(self, *, force: bool = False) -> bool:
        """중앙 갱신 경로로 액세스 토큰 재발급. 최대 3회 재시도 후 포기.

        force=False: 로컬 만료일 때만 refresh (ensure_access_token_for_login)
        force=True: 401 등 실무효 확인 후 강제 refresh
        재시도 사이에 정본(kakao_code.json)을 다시 읽어, 외부 재인증으로
        새 토큰이 저장된 경우 즉시 사용한다.
        """
        client_id = self._rest_api_key()
        if not client_id:
            logger.error("카카오 토큰 갱신 실패: REST API 키가 없습니다.")
            return False

        self._sync_tokens_from_store()
        if not self.refresh_token:
            logger.error(
                "카카오 토큰 갱신 실패: 리프레시 토큰이 없습니다. "
                "kakao_code.json / .env 의 KAKAO_REFRESH_TOKEN 을 확인하세요."
            )
            return False

        last_error: Exception | str | None = None
        for attempt in range(1, TOKEN_REFRESH_MAX_ATTEMPTS + 1):
            try:
                if force:
                    new_access = refresh_kakao_access_token_sync(
                        client_id, "", force=True
                    ).strip()
                else:
                    new_access = ensure_access_token_for_login(client_id, "").strip()
            except Exception as exc:
                last_error = exc
                logger.error(
                    "카카오 토큰 갱신 요청 실패 (시도 %s/%s): %s — 원인=%s",
                    attempt,
                    TOKEN_REFRESH_MAX_ATTEMPTS,
                    type(exc).__name__,
                    exc,
                )
                new_access = ""

            if new_access:
                self._sync_tokens_from_store()
                if attempt > 1:
                    logger.info("카카오 액세스 토큰 자동 갱신 완료 (재시도 %s회 만에 성공)", attempt)
                else:
                    logger.info("카카오 액세스 토큰 자동 갱신 완료")
                return True

            if attempt < TOKEN_REFRESH_MAX_ATTEMPTS:
                time.sleep(TOKEN_REFRESH_RETRY_DELAY_SECONDS)
                self._sync_tokens_from_store()
                # 외부 재인증으로 새 refresh가 들어왔으면 다음 시도에서 사용
                if self.access_token and is_access_token_locally_fresh() and not force:
                    return True

        logger.error(
            "카카오 토큰 갱신 실패: %s회 재시도 후 포기 (마지막 오류: %s). "
            "리프레시 토큰 만료·파일 저장 실패 시 서버에서 coinbot_watch.sh 로 재인증하세요.",
            TOKEN_REFRESH_MAX_ATTEMPTS,
            last_error or "새 액세스 토큰 없음/저장 검증 실패",
        )
        return False

    def notify_start(self):
        return self.send_message(
            "실전 매매 시작",
            f"프로그램이 시작되었습니다.\n시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )

    def notify_dry_run_mode(self):
        return self.send_message(
            "테스트 모드 실행",
            f"실제 주문 없이 시뮬레이션으로 동작합니다.\n시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )

    def notify_entry_signal(self, side: str, price: float, market_state: str, reason: str):
        return self.send_message(
            f"{side} 진입 신호",
            (
                f"목표가: ${price:,.2f}\n"
                f"시장 상태: {market_state}\n"
                f"사유: {reason}\n"
                f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        )

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
        return self.send_message(
            f"주문 체결 완료 ({order_type})",
            (
                f"방향: {side}\n"
                f"진입가: ${entry_price:,.2f}\n"
                f"수량: {size:.6f} BTC\n"
                f"포지션 가치: ${position_value:,.2f}\n"
                f"손절가: ${stop_loss:,.2f}\n"
                f"익절가: ${take_profit:,.2f}\n"
                f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
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
        sign = "+" if pnl >= 0 else ""
        return self.send_message(
            "포지션 청산",
            (
                f"방향: {side}\n"
                f"진입가: ${entry_price:,.2f}\n"
                f"청산가: ${exit_price:,.2f}\n"
                f"손익: {sign}${pnl:,.2f} ({sign}{profit_rate:.2f}%)\n"
                f"사유: {reason}\n"
                f"현재 잔고: ${balance:,.2f}\n"
                f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        )

    def notify_error(self, error_message: str):
        return self.send_message(
            "에러 발생",
            f"{error_message}\n시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )

    def notify_insufficient_balance(self, current_balance_usdt: float, required_min_usdt: float):
        shortage = max(0.0, required_min_usdt - current_balance_usdt)
        return self.send_message(
            "잔고 부족",
            (
                f"현재 잔고: ${current_balance_usdt:,.2f}\n"
                f"필요 최소액: ${required_min_usdt:,.2f}\n"
                f"부족액: ${shortage:,.2f}\n"
                f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        )

    def notify_no_entry(self, market_state: str, reason: str):
        return self.send_message(
            "오늘은 진입 없음",
            (
                f"시장 상태: {market_state}\n"
                f"사유: {reason}\n"
                f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        )

    def notify_emergency_stop(self, reason: str, total_loss: float):
        return self.send_message(
            "긴급 정지",
            (
                f"사유: {reason}\n"
                f"총 손실: -${total_loss:,.2f}\n"
                f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        )

    def notify_daily_summary(
        self,
        total_trades: int,
        winning_trades: int,
        daily_pnl: float,
        balance: float,
    ):
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        return self.send_message(
            "일일 요약",
            (
                f"총 거래: {total_trades}회\n"
                f"승리: {winning_trades}회\n"
                f"일일 손익: ${daily_pnl:,.2f}\n"
                f"승률: {win_rate:.2f}%\n"
                f"잔고: ${balance:,.2f}\n"
                f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        )


class SilentNotifier:
    """알림 비활성화 시 사용하는 무동작 알림기"""

    def __getattr__(self, name):
        def silent_method(*args, **kwargs):
            return True

        return silent_method
