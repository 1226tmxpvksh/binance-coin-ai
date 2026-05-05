"""
카카오톡 알림 모듈
거래 신호, 체결, 손익 등을 카카오톡 '나에게 보내기'로 알림
"""

import logging
import requests
import json
import os
from typing import Optional
from datetime import datetime
from urllib.parse import quote

from kakao_utils import (
    apply_token_response,
    exchange_authorization_code,
    get_redirect_uri,
    get_refresh_token,
    hydrate_tokens_from_json,
    refresh_access_token_request,
)

logger = logging.getLogger(__name__)

# 외부 전송을 위한 메시지 최대 길이 제한
MAX_MESSAGE_LENGTH = 1000


def _build_kakao_auth_url(rest_api_key: str, redirect_uri: str) -> str:
    if not rest_api_key or not redirect_uri:
        return ""
    encoded_redirect_uri = quote(redirect_uri.strip(), safe="")
    return (
        "https://kauth.kakao.com/oauth/authorize?"
        f"client_id={rest_api_key}&redirect_uri={encoded_redirect_uri}&response_type=code"
    )


def _validate_redirect_uri_config(redirect_uri: str) -> bool:
    env_redirect_uri = os.getenv("KAKAO_REDIRECT_URI", "").strip()
    if not redirect_uri:
        logger.error("KAKAO_REDIRECT_URI가 비어 있습니다. 카카오 개발자 콘솔 Redirect URI와 동일하게 설정하세요.")
        return False
    if redirect_uri in {"https://example.com/oauth", "your_redirect_uri_here"}:
        logger.error("KAKAO_REDIRECT_URI가 기본 예시값입니다. 실제 등록 URI로 변경하세요.")
        return False
    if not redirect_uri.startswith(("http://", "https://")):
        logger.error("KAKAO_REDIRECT_URI 형식이 올바르지 않습니다: %s", redirect_uri)
        return False
    if env_redirect_uri and env_redirect_uri != redirect_uri:
        logger.error("KAKAO_REDIRECT_URI 불일치: env=%s, notifier=%s", env_redirect_uri, redirect_uri)
        return False
    return True


def _response_error_payload(exc: Exception) -> tuple[str, str]:
    response = getattr(exc, "response", None)
    if response is None:
        return "", ""
    try:
        payload = response.json()
        return (
            str(payload.get("error") or "").strip(),
            str(payload.get("error_description") or "").strip(),
        )
    except Exception:
        return "", str(getattr(response, "text", "") or "").strip()


class KakaoNotifier:
    """카카오톡 알림 클래스"""
    
    def __init__(
        self,
        access_token: str,
        enabled: bool = True,
        rest_api_key: str = "",
        redirect_uri: Optional[str] = None,
        prompt_on_refresh_failure: bool = True,
    ):
        """
        Args:
            access_token: 카카오 REST API 액세스 토큰
            enabled: 알림 활성화 여부
            rest_api_key: 카카오 REST API 키 (토큰 재발급용)
            redirect_uri: OAuth 리다이렉트 URI (미지정 시 KAKAO_REDIRECT_URI 또는 기본값)
        """
        self.access_token = access_token
        self.enabled = enabled
        self.rest_api_key = rest_api_key
        self.api_url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
        self.redirect_uri = (redirect_uri or get_redirect_uri()).strip()
        self.prompt_on_refresh_failure = prompt_on_refresh_failure
        self._first_403_disable = True
    
    def send_message(self, title: str, description: str, retry_count: int = 0) -> bool:
        """
        카카오톡 메시지 전송 (SAlertR 방식 적용)
        
        Args:
            title: 메시지 제목
            description: 메시지 내용
            retry_count: 재시도 횟수 (무한루프 방지)
        
        Returns:
            bool: 전송 성공 여부
        """
        if not self.enabled:
            logger.debug("카카오톡 알림이 비활성화되어 있습니다")
            return False
        
        if not self.access_token:
            logger.warning("카카오톡 액세스 토큰이 없습니다")
            return False
        
        # 무한루프 방지 (최대 1회 재시도)
        if retry_count > 1:
            logger.error("카카오톡 메시지 전송 재시도 횟수 초과")
            return False
        
        try:
            # 메시지 길이 제한 (보안상 중요)
            message_text = f"{title}\n\n{description}"
            if len(message_text) > MAX_MESSAGE_LENGTH:
                message_text = message_text[:MAX_MESSAGE_LENGTH - 3] + "..."
                logger.warning(f"메시지가 너무 길어 잘렸습니다: {len(message_text)} -> {MAX_MESSAGE_LENGTH}")
            
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            template_object = {
                "object_type": "text",
                "text": message_text,
                "link": {
                    "web_url": "https://www.binance.com",
                    "mobile_web_url": "https://www.binance.com"
                }
            }
            
            payload = {
                "template_object": json.dumps(template_object)
            }
            
            response = requests.post(
                self.api_url,
                headers=headers,
                data=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info("카카오톡 메시지 전송 성공")
                return True
            elif response.status_code == 401:
                # 토큰 만료 - 자동 재발급 시도 (SAlertR 방식)
                if retry_count == 0:  # 첫 시도에서만 재시도
                    logger.warning("401 Unauthorized: Access Token이 만료되었습니다. 갱신 후 재시도합니다.")
                    
                    # 토큰 자동 갱신
                    if self._auto_refresh_token():
                        # 갱신 성공 시 재시도 (1회만)
                        logger.info("토큰 갱신 성공, 메시지 재전송 시도")
                        return self.send_message(title, description, retry_count + 1)
                    else:
                        logger.error("토큰 자동 갱신 실패")
                        return False
                else:
                    logger.error("토큰 갱신 후에도 401 에러 발생")
                    return False
                    
            elif response.status_code == 403:
                # 권한 부족 - 동의항목 설정 필요
                logger.error("=" * 80)
                logger.error("⚠️ 카카오톡 메시지 전송 권한이 없습니다! (403)")
                logger.error("=" * 80)
                logger.error("")
                auth_url = ""
                if self.rest_api_key and self.rest_api_key != "your_rest_api_key_here":
                    _validate_redirect_uri_config(self.redirect_uri)
                    auth_url = _build_kakao_auth_url(self.rest_api_key, self.redirect_uri)
                logger.error("🔗 토큰 재발급 URL (브라우저에서 열기):")
                logger.error(f"   {auth_url}")
                logger.error("KOE205 발생 시 KAKAO_REDIRECT_URI와 카카오 개발자 콘솔 Redirect URI가 정확히 같은지 확인하세요.")
                logger.error("=" * 80)

                if self._first_403_disable:
                    self.enabled = False
                    self._first_403_disable = False
                    logger.warning("카카오톡 알림이 자동으로 비활성화되었습니다.")

                return False
            else:
                # 응답 내용에서 민감한 정보 제거
                error_msg = f"status={response.status_code}"
                logger.error(f"카카오톡 메시지 전송 실패: {error_msg}")
                return False
                
        except requests.exceptions.Timeout:
            if retry_count == 0:
                logger.error("카카오톡 메시지 전송 타임아웃")
            return False
        except requests.exceptions.RequestException as e:
            if retry_count == 0:
                logger.error(f"카카오톡 메시지 전송 네트워크 오류: {type(e).__name__}")
            return False
        except Exception as e:
            if retry_count == 0:
                logger.error(f"카카오톡 메시지 전송 오류: {type(e).__name__}")
            return False
    
    def _auto_refresh_token(self) -> bool:
        """
        토큰 자동 갱신 (사용자 입력 없이)
        리프레시 토큰이 있으면 자동 갱신, 없으면 수동 갱신 프롬프트
        
        Returns:
            bool: 갱신 성공 여부
        """
        hydrate_tokens_from_json()
        refresh_token = get_refresh_token()

        if refresh_token and refresh_token != "your_refresh_token_here":
            logger.info("리프레시 토큰을 사용하여 액세스 토큰 자동 갱신 중...")
            new_token = self._refresh_access_token(refresh_token)

            if new_token:
                self.access_token = new_token
                logger.info("✅ 액세스 토큰 자동 갱신 완료")
                return True

            logger.warning("리프레시 토큰 갱신 실패, 수동 갱신 필요")
        
        # 리프레시 토큰이 없으면 수동 갱신 프롬프트
        logger.warning("리프레시 토큰이 없거나 만료되었습니다. 수동 토큰 갱신이 필요합니다.")
        
        if self.rest_api_key and self.rest_api_key != "your_rest_api_key_here":
            _validate_redirect_uri_config(self.redirect_uri)
            auth_url = _build_kakao_auth_url(self.rest_api_key, self.redirect_uri)
            logger.error("=" * 80)
            logger.error("⚠️ 카카오톡 액세스 토큰이 만료되었습니다!")
            logger.error("리프레시 토큰까지 만료된 경우 새 인가 코드 발급이 필요합니다.")
            logger.error("=" * 80)
            logger.error("🔗 토큰 재발급 URL (브라우저에서 열기):")
            logger.error(f"   {auth_url}")
            logger.error("절차: URL 접속 → 로그인/동의 → 리다이렉트 URL의 code= 값 복사 → 프롬프트에 입력")
            logger.error("KOE205 발생 시 KAKAO_REDIRECT_URI와 카카오 개발자 콘솔 Redirect URI가 정확히 같은지 확인하세요.")
            logger.error("=" * 80)
            
            if self.prompt_on_refresh_failure:
                self._prompt_token_refresh()
                return True  # 프롬프트 실행됨

            logger.warning("무인 실행 모드라 토큰 갱신 프롬프트를 생략합니다.")
            self.enabled = False
            return False
        
        return False
    
    def _refresh_access_token(self, refresh_token: str) -> Optional[str]:
        """
        리프레시 토큰으로 액세스 토큰 갱신
        
        Args:
            refresh_token: 리프레시 토큰
        
        Returns:
            새로운 액세스 토큰 (실패 시 None)
        """
        if not self.rest_api_key or self.rest_api_key == "your_rest_api_key_here":
            logger.error("KAKAO_REST_API_KEY가 없어 토큰을 갱신할 수 없습니다.")
            return None
        try:
            token_data = refresh_access_token_request(self.rest_api_key, refresh_token)
            access_token = token_data.get("access_token")
            if token_data.get("refresh_token"):
                logger.info("새 리프레시 토큰도 발급되었습니다 (자동 저장됨)")
            if access_token:
                apply_token_response(token_data)
            return access_token
        except Exception as e:
            error_code, error_description = _response_error_payload(e)
            if error_code == "expired_or_invalid_refresh_token":
                logger.error("카카오 리프레시 토큰 만료/무효: 새 인가 코드 발급이 필요합니다.")
            elif error_code:
                logger.error("토큰 갱신 요청 오류: %s (%s)", error_code, error_description or type(e).__name__)
            else:
                logger.error("토큰 갱신 요청 오류: %s", type(e).__name__)
            return None
    
    def _prompt_token_refresh(self):
        """토큰 재발급 인터랙티브 프롬프트"""
        try:
            print("\n" + "=" * 80)
            print("🔄 카카오톡 토큰 갱신")
            print("=" * 80)
            
            response = input("\n토큰을 지금 갱신하시겠습니까? (y/n): ").strip().lower()
            
            if response != 'y':
                print("❌ 토큰 갱신을 건너뜁니다. 카카오톡 알림이 비활성화됩니다.")
                self.enabled = False
                return
            
            # Authorization Code 입력
            print("\n📝 Authorization Code 입력")
            print(f"   현재 redirect_uri: {self.redirect_uri}")
            print("   카카오 개발자 콘솔 Redirect URI와 위 값이 1글자도 다르면 KOE205가 발생합니다.")
            print("   (브라우저에서 리다이렉트된 URL의 'code=' 뒤 값을 복사하세요)")
            print("   💡 붙여넣기: 마우스 우클릭 또는 Ctrl+V")
            auth_code = input("Code: ").strip()
            
            if not auth_code:
                print("❌ 코드가 입력되지 않았습니다.")
                self.enabled = False
                return
            
            # 액세스 토큰 발급
            print("\n⏳ 토큰 발급 중...")
            new_token = self._get_access_token_from_code(auth_code)
            
            if new_token:
                # 현재 인스턴스 토큰 업데이트 (_get_access_token_from_code에서 이미 저장됨)
                self.access_token = new_token
                self.enabled = True

                print("=" * 80)
                logger.info("카카오톡 토큰 갱신 완료")

                print("\n🧪 새 토큰으로 테스트 메시지 전송 중...")
                
                self.enabled = True  # 테스트를 위해 활성화
                test_result = self.send_message(
                    "✅ 카카오톡 연결 성공",
                    f"토큰이 갱신되어 알림이 정상 작동합니다.\n갱신 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                
                if test_result:
                    print("✅ 테스트 메시지 전송 성공! 카카오톡을 확인하세요.")
                    print("💡 프로그램이 계속 실행됩니다. 재시작 불필요!")
                else:
                    print("⚠️ 테스트 메시지 전송 실패")
                    print("📝 카카오 개발자 콘솔에서 '동의항목' 설정 확인:")
                    print("   https://developers.kakao.com/console/app → 동의항목")
                    print("   '카카오톡 메시지 전송' 권한 활성화 필요")
            else:
                print("\n❌ 토큰 발급에 실패했습니다.")
                self.enabled = False
                
        except KeyboardInterrupt:
            print("\n\n❌ 토큰 갱신이 취소되었습니다.")
            self.enabled = False
        except Exception as e:
            logger.error(f"토큰 갱신 중 오류: {type(e).__name__}")
            self.enabled = False
    
    def _get_access_token_from_code(self, auth_code: str) -> Optional[str]:
        """Authorization Code로 액세스 토큰 발급"""
        try:
            token_data = exchange_authorization_code(
                self.rest_api_key, self.redirect_uri, auth_code
            )
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            if refresh_token:
                masked_token = (
                    refresh_token[:8] + "..." + refresh_token[-4:]
                    if len(refresh_token) > 12
                    else "***"
                )
                logger.info("리프레시 토큰도 발급되었습니다: %s", masked_token)
                logger.info("리프레시 토큰이 저장되어 다음부터 자동 갱신됩니다.")
            if access_token:
                apply_token_response(token_data)
            return access_token
        except Exception as e:
            error_code, error_description = _response_error_payload(e)
            if error_code == "KOE205" or "KOE205" in error_description:
                logger.error("토큰 발급 실패(KOE205): KAKAO_REDIRECT_URI가 카카오 개발자 콘솔 Redirect URI와 다릅니다.")
            elif error_code:
                logger.error("토큰 발급 요청 오류: %s (%s)", error_code, error_description or type(e).__name__)
            else:
                logger.error("토큰 발급 요청 오류: %s", type(e).__name__)
            return None
    
    def notify_start(self):
        """프로그램 시작 알림"""
        title = "🚀 실전 매매 프로그램 시작"
        description = (
            f"시작 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"상태: 모니터링 중..."
        )
        return self.send_message(title, description)
    
    def notify_entry_signal(
        self,
        side: str,
        price: float,
        market_state: str,
        reason: str
    ):
        """진입 신호 알림"""
        emoji = "🟢" if side == "LONG" else "🔴"
        title = f"{emoji} 진입 신호 감지!"
        description = (
            f"방향: {side}\n"
            f"현재가: ${price:,.2f}\n"
            f"시장 상태: {market_state}\n"
            f"사유: {reason}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
    
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
        title = f"✅ 주문 체결 완료 ({order_type})"
        description = (
            f"방향: {side}\n"
            f"진입가: ${entry_price:,.2f}\n"
            f"수량: {size:.6f} BTC\n"
            f"포지션 가치: ${position_value:,.2f}\n\n"
            f"손절가: ${stop_loss:,.2f}\n"
            f"익절가: ${take_profit:,.2f}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
    
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
        emoji = "💰" if pnl > 0 else "⚠️"
        sign = "+" if pnl >= 0 else ""
        
        title = f"{emoji} 포지션 청산"
        description = (
            f"방향: {side}\n"
            f"진입가: ${entry_price:,.2f}\n"
            f"청산가: ${exit_price:,.2f}\n\n"
            f"손익: {sign}${pnl:,.2f} ({sign}{profit_rate:.2f}%)\n\n"
            f"청산 사유: {reason}\n"
            f"현재 잔고: ${balance:,.2f}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
    
    def notify_error(self, error_message: str):
        """에러 알림"""
        title = "❌ 에러 발생!"
        description = (
            f"{error_message}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
    
    def notify_insufficient_balance(
        self,
        current_balance_usdt: float,
        required_min_usdt: float,
    ):
        """선물 USDT가 최소 진입 기준 미만일 때 알림"""
        title = "💰 잔고 부족 (신규 진입 보류)"
        shortage = max(0.0, required_min_usdt - current_balance_usdt)
        description = (
            f"잔고가 부족합니다.\n\n"
            f"현재(USDT-M 선물): ${current_balance_usdt:,.2f}\n"
            f"설정 최소: ${required_min_usdt:,.2f}\n"
            f"부족분(참고): 약 ${shortage:,.2f}\n\n"
            f"바이낸스 선물 지갑으로 USDT를 입금해 주세요.\n"
            f"오늘은 신규 진입을 하지 않으며, 내일 다시 잔고를 확인합니다.\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)

    def notify_no_entry(self, market_state: str, reason: str):
        """당일 신규 진입이 없는 사유 알림"""
        title = "ℹ️ 오늘 신규 진입 없음"
        description = (
            f"시장 상태: {market_state}\n"
            f"사유: {reason}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
    
    def notify_daily_summary(
        self,
        total_trades: int,
        winning_trades: int,
        daily_pnl: float,
        balance: float
    ):
        """일일 요약 알림"""
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        sign = "+" if daily_pnl >= 0 else ""
        
        title = "📊 일일 거래 요약"
        description = (
            f"총 거래: {total_trades}회\n"
            f"승리: {winning_trades}회\n"
            f"승률: {win_rate:.1f}%\n\n"
            f"일일 손익: {sign}${daily_pnl:,.2f}\n"
            f"현재 잔고: ${balance:,.2f}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
    
    def notify_emergency_stop(self, reason: str, total_loss: float):
        """긴급 정지 알림"""
        title = "🚨 긴급 정지! 🚨"
        description = (
            f"사유: {reason}\n"
            f"총 손실: -${total_loss:,.2f}\n\n"
            f"프로그램이 중단되었습니다.\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
    
    def notify_dry_run_mode(self):
        """테스트 모드 알림"""
        title = "🧪 테스트 모드(DRY RUN) 실행 중"
        description = (
            f"실제 주문이 실행되지 않습니다.\n"
            f"모든 거래는 시뮬레이션됩니다.\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
    
    def notify_limit_order_attempt(self, side: str, price: float, quantity: float):
        """LIMIT 주문 시도 알림"""
        title = "💡 LIMIT 주문 시도"
        description = (
            f"방향: {side}\n"
            f"가격: ${price:,.2f}\n"
            f"수량: {quantity:.6f} BTC\n"
            f"대기 중... (5초)\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
    
    def notify_limit_to_market_fallback(self):
        """LIMIT → MARKET 전환 알림"""
        title = "🔄 MARKET 주문으로 전환"
        description = (
            f"LIMIT 주문 미체결\n"
            f"MARKET 주문으로 재시도 중...\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(title, description)
