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
from getpass import getpass

logger = logging.getLogger(__name__)

# 외부 전송을 위한 메시지 최대 길이 제한
MAX_MESSAGE_LENGTH = 1000


class KakaoNotifier:
    """카카오톡 알림 클래스"""
    
    def __init__(self, access_token: str, enabled: bool = True, rest_api_key: str = ""):
        """
        Args:
            access_token: 카카오 REST API 액세스 토큰
            enabled: 알림 활성화 여부
            rest_api_key: 카카오 REST API 키 (토큰 재발급용)
        """
        self.access_token = access_token
        self.enabled = enabled
        self.rest_api_key = rest_api_key
        self.api_url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
        self.redirect_uri = "https://example.com/oauth"
    
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
                if self.rest_api_key and self.rest_api_key != "your_rest_api_key_here":
                    auth_url = f"https://kauth.kakao.com/oauth/authorize?client_id={self.rest_api_key}&redirect_uri={self.redirect_uri}&response_type=code"
                logger.error("🔗 토큰 재발급 URL (브라우저에서 열기):")
                logger.error(f"   {auth_url}")
                logger.error("=" * 80)
                
                if not skip_403_prompt:
                    # 첫 번째 403 에러에서만 알림 비활성화
                    self.enabled = False
                    logger.warning("카카오톡 알림이 자동으로 비활성화되었습니다.")
                
                return False
            elif response.status_code == 401:
                
                if self.rest_api_key and self.rest_api_key != "your_rest_api_key_here":
                    auth_url = f"https://kauth.kakao.com/oauth/authorize?client_id={self.rest_api_key}&redirect_uri={self.redirect_uri}&response_type=code"
                    logger.error("🔗 토큰 재발급 URL (브라우저에서 열기):")
                    logger.error(f"   {auth_url}")
                    logger.error("")
                    logger.error("📝 토큰 재발급 방법:")
                    logger.error("   3. 리다이렉트된 URL에서 'code=' 뒤의 값 복사")
                    logger.error("   4. 아래에서 새 토큰 입력")
                    logger.error("=" * 80)
                    
                    # 토큰 재발급 시도
                    self._prompt_token_refresh()
                else:
                    logger.error("🔗 토큰 재발급 URL 형식:")
                    logger.error("   https://kauth.kakao.com/oauth/authorize?")
                    logger.error("     client_id={YOUR_REST_API_KEY}")
                    logger.error("     &redirect_uri=https://example.com/oauth")
                    logger.error("     &response_type=code")
                    logger.error("=" * 80)
                
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
        # 리프레시 토큰이 환경변수에 있는지 확인
        refresh_token = os.getenv("KAKAO_REFRESH_TOKEN", "")
        
        if refresh_token and refresh_token != "your_refresh_token_here":
            # 리프레시 토큰으로 자동 갱신
            logger.info("리프레시 토큰을 사용하여 액세스 토큰 자동 갱신 중...")
            new_token = self._refresh_access_token(refresh_token)
            
            if new_token:
                self.access_token = new_token
                self._update_env_file(new_token)
                logger.info("✅ 액세스 토큰 자동 갱신 완료")
                return True
            else:
                logger.warning("리프레시 토큰 갱신 실패, 수동 갱신 필요")
        
        # 리프레시 토큰이 없으면 수동 갱신 프롬프트
        logger.warning("리프레시 토큰이 없습니다. 수동 토큰 갱신이 필요합니다.")
        
        if self.rest_api_key and self.rest_api_key != "your_rest_api_key_here":
            auth_url = f"https://kauth.kakao.com/oauth/authorize?client_id={self.rest_api_key}&redirect_uri={self.redirect_uri}&response_type=code"
            logger.error("=" * 80)
            logger.error("⚠️ 카카오톡 액세스 토큰이 만료되었습니다!")
            logger.error("=" * 80)
            logger.error("🔗 토큰 재발급 URL (브라우저에서 열기):")
            logger.error(f"   {auth_url}")
            logger.error("=" * 80)
            
            self._prompt_token_refresh()
            return True  # 프롬프트 실행됨
        
        return False
    
    def _refresh_access_token(self, refresh_token: str) -> Optional[str]:
        """
        리프레시 토큰으로 액세스 토큰 갱신
        
        Args:
            refresh_token: 리프레시 토큰
        
        Returns:
            새로운 액세스 토큰 (실패 시 None)
        """
        try:
            token_url = "https://kauth.kakao.com/oauth/token"
            data = {
                "grant_type": "refresh_token",
                "client_id": self.rest_api_key,
                "refresh_token": refresh_token
            }
            
            response = requests.post(token_url, data=data, timeout=10)
            
            if response.status_code == 200:
                token_data = response.json()
                access_token = token_data.get("access_token")
                
                # 새 리프레시 토큰도 있으면 업데이트
                new_refresh_token = token_data.get("refresh_token")
                if new_refresh_token:
                    logger.info("새 리프레시 토큰도 발급되었습니다 (자동 저장됨)")
                    self._update_refresh_token(new_refresh_token)
                
                return access_token
            else:
                # 보안: 에러 메시지에서 민감한 정보 제거
                try:
                    error_data = response.json()
                    error_type = error_data.get("error", "unknown_error")
                    logger.error(f"토큰 갱신 실패: {error_type}")
                except:
                    logger.error(f"토큰 갱신 실패: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"토큰 갱신 요청 오류: {type(e).__name__}")
            return None
    
    def _update_refresh_token(self, refresh_token: str):
        """리프레시 토큰을 .env 파일에 업데이트"""
        try:
            env_path = os.path.join(os.path.dirname(__file__), '.env')
            
            if not os.path.exists(env_path):
                return
            
            # .env 파일 읽기
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # KAKAO_REFRESH_TOKEN 줄 찾아서 업데이트
            updated = False
            for i, line in enumerate(lines):
                if line.strip().startswith('KAKAO_REFRESH_TOKEN='):
                    lines[i] = f'KAKAO_REFRESH_TOKEN={refresh_token}\n'
                    updated = True
                    break
            
            # 없으면 추가
            if not updated:
                lines.append(f'\nKAKAO_REFRESH_TOKEN={refresh_token}\n')
            
            # .env 파일 쓰기
            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            logger.info("리프레시 토큰 업데이트 완료")
            
        except Exception as e:
            logger.error(f"리프레시 토큰 업데이트 실패: {type(e).__name__}")
    
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
                # .env 파일 업데이트
                self._update_env_file(new_token)
                
                # 현재 인스턴스 토큰 업데이트
                self.access_token = new_token
                self.enabled = True

                print("=" * 80)
                logger.info("카카오톡 토큰 갱신 완료")
                
                # 갱신 후 테스트 메시지 전송 (403 에러 무시)
                print("\n🧪 새 토큰으로 테스트 메시지 전송 중...")
                
                # 테스트 메시지 전송
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
            token_url = "https://kauth.kakao.com/oauth/token"
            data = {
                "grant_type": "authorization_code",
                "client_id": self.rest_api_key,
                "redirect_uri": self.redirect_uri,
                "code": auth_code
            }
            
            response = requests.post(token_url, data=data, timeout=10)
            
            if response.status_code == 200:
                token_data = response.json()
                access_token = token_data.get("access_token")
                
                # 리프레시 토큰도 자동 저장 (장기 사용 가능)
                refresh_token = token_data.get("refresh_token")
                if refresh_token:
                    # 보안: 토큰 일부만 로그에 표시
                    masked_token = refresh_token[:8] + "..." + refresh_token[-4:] if len(refresh_token) > 12 else "***"
                    logger.info(f"리프레시 토큰도 발급되었습니다: {masked_token}")
                    self._update_refresh_token(refresh_token)
                    logger.info("💡 리프레시 토큰이 .env에 저장되어 다음부터 자동 갱신됩니다!")
                
                return access_token
            else:
                error_data = response.json()
                error_msg = error_data.get("error_description", "알 수 없는 오류")
                logger.error(f"토큰 발급 실패: {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"토큰 발급 요청 오류: {type(e).__name__}")
            return None
    
    def _update_env_file(self, new_token: str):
        """..env 파일에 새 토큰 저장"""
        try:
            env_path = os.path.join(os.path.dirname(__file__), '.env')
            
            if not os.path.exists(env_path):
                logger.warning(".env 파일을 찾을 수 없습니다")
                return
            
            # .env 파일 읽기
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # KAKAO_ACCESS_TOKEN 줄 찾아서 업데이트
            updated = False
            for i, line in enumerate(lines):
                if line.strip().startswith('KAKAO_ACCESS_TOKEN='):
                    lines[i] = f'KAKAO_ACCESS_TOKEN={new_token}\n'
                    updated = True
                    break
            
            # 없으면 추가
            if not updated:
                lines.append(f'\nKAKAO_ACCESS_TOKEN={new_token}\n')
            
            # .env 파일 쓰기
            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            logger.info(".env 파일 업데이트 완료")
            
        except Exception as e:
            logger.error(f".env 파일 업데이트 실패: {type(e).__name__}")
    
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

    @staticmethod
    def _scalping_goal_footer(achievement_pct: float, remaining_krw: float) -> str:
        return (
            f"\n\n월 목표 달성률: {achievement_pct:.1f}%\n"
            f"이번 달 구독료 목표(10만 원)까지 남은 금액: {remaining_krw:,.0f}원"
        )

    def notify_scalping_start(
        self,
        achievement_pct: float,
        remaining_krw: float,
        dry_run: bool = False,
    ):
        title = "⚡ 스캘핑(AI 단타) 시작"
        description = (
            f"모드: TRADING_MODE=scalping\n"
            f"DRY_RUN: {'예 (시뮬)' if dry_run else '아니오 (실주문)'}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) + self._scalping_goal_footer(achievement_pct, remaining_krw)
        return self.send_message(title, description)

    def notify_scalping_signal(
        self,
        symbol: str,
        side: str,
        price: float,
        rule_reason: str,
        achievement_pct: float,
        remaining_krw: float,
    ):
        title = f"📈 스캘핑 진입 신호 ({symbol})"
        description = (
            f"방향: {side}\n"
            f"가격: ${price:,.2f}\n"
            f"규칙: {rule_reason}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) + self._scalping_goal_footer(achievement_pct, remaining_krw)
        return self.send_message(title, description)

    def notify_scalping_ai_rejected(
        self,
        symbol: str,
        reason: str,
        achievement_pct: float,
        remaining_krw: float,
    ):
        title = f"🤖 AI 진입 보류 ({symbol})"
        description = (
            f"fail_closed: AI 미승인 또는 API 오류\n"
            f"사유: {reason}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) + self._scalping_goal_footer(achievement_pct, remaining_krw)
        return self.send_message(title, description)

    def notify_scalping_filled(
        self,
        symbol: str,
        entry_price: float,
        size: float,
        stop_loss: float,
        tp_target_usdt: float,
        achievement_pct: float,
        remaining_krw: float,
    ):
        title = f"✅ 스캘핑 체결 ({symbol})"
        description = (
            f"진입가: ${entry_price:,.2f}\n"
            f"수량: {size:.6f}\n"
            f"손절: ${stop_loss:,.2f} (-5%%)\n"
            f"익절 목표: 약 ${tp_target_usdt:.2f} USDT (설정 원화 환산)\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) + self._scalping_goal_footer(achievement_pct, remaining_krw)
        return self.send_message(title, description)

    def notify_scalping_closed(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        reason: str,
        achievement_pct: float,
        remaining_krw: float,
        balance_usdt: float,
    ):
        sign = "+" if pnl >= 0 else ""
        title = f"🏁 스캘핑 청산 ({symbol})"
        description = (
            f"진입: ${entry_price:,.2f} → 청산: ${exit_price:,.2f}\n"
            f"손익: {sign}${pnl:,.2f}\n"
            f"사유: {reason}\n"
            f"추정 잔고: ${balance_usdt:,.2f}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) + self._scalping_goal_footer(achievement_pct, remaining_krw)
        return self.send_message(title, description)

    def notify_scalping_confirm_wait(
        self,
        seconds: int,
        achievement_pct: float,
        remaining_krw: float,
    ):
        title = "⏳ 스캘핑 진입 대기"
        description = (
            f"{seconds}초 후 자동 진행(카카오 수신 확인 불가)\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) + self._scalping_goal_footer(achievement_pct, remaining_krw)
        return self.send_message(title, description)

    def notify_error_scalping(
        self,
        message: str,
        achievement_pct: float,
        remaining_krw: float,
    ):
        title = "❌ 스캘핑 오류"
        description = (
            f"{message}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) + self._scalping_goal_footer(achievement_pct, remaining_krw)
        return self.send_message(title, description)

    def notify_emergency_stop_scalping(
        self,
        reason: str,
        total_loss: float,
        achievement_pct: float,
        remaining_krw: float,
    ):
        title = "🚨 스캘핑 긴급 정지"
        description = (
            f"사유: {reason}\n"
            f"참고 손실: ${total_loss:,.2f}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) + self._scalping_goal_footer(achievement_pct, remaining_krw)
        return self.send_message(title, description)
