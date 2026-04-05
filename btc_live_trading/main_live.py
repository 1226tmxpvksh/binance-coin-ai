import logging
import os
import sys
import time
import requests
from datetime import datetime, date
from binance.client import Client

import config_live as config
from kakao_notifier import KakaoNotifier
from async_notifier import AsyncNotifier, SilentNotifier
from order_executor import OrderExecutor
from position_manager import PositionManager
from safety_manager import SafetyManager
from live_trading_engine import LiveTradingEngine
from security_utils import DataSanitizer, SecureComparison
from strategy.data_loader import fetch_historical_data

# 로깅 설정 (보안 강화)
# getattr 대신 안전한 딕셔너리 매핑 사용
LOG_LEVEL_MAP = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}

log_level = LOG_LEVEL_MAP.get(config.LOG_LEVEL, logging.INFO)

# systemd·cron 등 비대화형 실행 시 실전 확인용 (표준입력 없음 → input() EOF 방지)
LIVE_TRADING_CONFIRMED_ENV = "LIVE_TRADING_CONFIRMED"
LIVE_TRADING_CONFIRMED_VALUE = "YES"

# 민감한 정보 필터링을 위한 커스텀 필터
class SensitiveDataFilter(logging.Filter):
    """민감한 정보를 로그에서 제거하는 필터"""
    
    def filter(self, record):
        sanitizer = DataSanitizer()
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            # API 키, 토큰 등 마스킹
            record.msg = sanitizer.sanitize_error_message(record.msg, max_length=500)
        return True

logging.basicConfig(
    level=log_level,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding='utf-8')
    ]
)

# 민감 정보 필터 추가
for handler in logging.root.handlers:
    handler.addFilter(SensitiveDataFilter())


def _reconfigure_logging(level_str: str, log_file: str) -> None:
    """TRADING_MODE=scalping 시 config_scalping 로그 파일로 전환."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    logging.basicConfig(
        level=LOG_LEVEL_MAP.get(level_str, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )
    for handler in logging.root.handlers:
        handler.addFilter(SensitiveDataFilter())


logger = logging.getLogger(__name__)

# API 키 마스킹하여 로그 기록
sanitizer = DataSanitizer()
logger.info(f"API 키 확인: {sanitizer.sanitize_api_key(config.API_KEY)}")


def initialize_components():
    """모든 컴포넌트 초기화"""
    
    logger.info("컴포넌트 초기화 시작...")
    
    # 1. 바이낸스 클라이언트
    client = Client(config.API_KEY, config.API_SECRET)
    
    # 서버 시간 동기화 (바이낸스 -1021 에러 방지)
    # Futures 잔고/주문은 fapi.binance.com 사용 → Futures 서버 시각으로 동기화
    local_time = int(time.time() * 1000)
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/time", timeout=5)
        if r.status_code == 200:
            futures_server_time = r.json()["serverTime"]
            client.time_offset = futures_server_time - local_time - 1500  # 1.5초 마진
            logger.info(f"바이낸스 Futures 서버 시간 동기화. 오프셋: {client.time_offset}ms")
        else:
            raise ValueError("Futures time API 실패")
    except Exception as e:
        logger.warning(f"Futures 시간 조회 실패, Spot 기준으로 시도: {e}")
        server_time = client.get_server_time()
        client.time_offset = server_time["serverTime"] - local_time - 2000  # 2초 마진
        logger.info(f"바이낸스 Spot 서버 시간 동기화. 오프셋: {client.time_offset}ms")
    
    # 2. 알림 설정 (카카오톡 - 비동기 처리)
    if config.KAKAO_ENABLED:
        kakao = KakaoNotifier(
            access_token=config.KAKAO_ACCESS_TOKEN,
            enabled=config.KAKAO_ENABLED,
            rest_api_key=config.KAKAO_REST_API_KEY
        )
        # 비동기 래퍼로 감싸기 (거래 지연 방지)
        notifier = AsyncNotifier(kakao, max_queue_size=100)
        notifier.start()
        logger.info("카카오톡 알림 활성화 (비동기 모드)")
    else:
        # 알림 비활성화 시 조용한 알림 사용
        notifier = SilentNotifier()
        logger.warning("알림이 비활성화되어 있습니다")
    
    # 3. 주문 실행기
    executor = OrderExecutor(
        client=client,
        symbol=config.SYMBOL,
        dry_run=config.DRY_RUN
    )
    
    # 4. 포지션 관리자
    position_mgr = PositionManager()
    
    # 5. 안전장치
    initial_balance = executor.get_account_balance()
    if initial_balance is None:
        raise ValueError("초기 잔고 조회 실패")
    
    safety_mgr = SafetyManager(
        initial_capital=initial_balance,
        daily_max_loss_percent=config.DAILY_MAX_LOSS_PERCENT,
        emergency_stop_loss_percent=config.EMERGENCY_STOP_LOSS_PERCENT,
        max_trades_per_day=config.MAX_TRADES_PER_DAY
    )
    
    # 6. 매매 엔진
    config_dict = {
        'SYMBOL': config.SYMBOL,
        'LEVERAGE': config.LEVERAGE,
        'EMA_SHORT_PERIOD': config.EMA_SHORT_PERIOD,
        'EMA_LONG_PERIOD': config.EMA_LONG_PERIOD,
        'SMA_LONG_PERIOD': config.SMA_LONG_PERIOD,
        'ATR_PERIOD': config.ATR_PERIOD,
        'VOLUME_MA_PERIOD': config.VOLUME_MA_PERIOD,
        'K_MAX': config.K_MAX,
        'STOP_LOSS_MULTIPLIER': config.STOP_LOSS_MULTIPLIER,
        'TAKE_PROFIT_MULTIPLIER': config.TAKE_PROFIT_MULTIPLIER,
        'MAX_HOLD_DAYS': config.MAX_HOLD_DAYS,
        'TRAILING_STOP_ATR_MULT': config.TRAILING_STOP_ATR_MULT,
        'RISK_PER_TRADE': config.RISK_PER_TRADE,
        'MIN_POSITION_SIZE_USDT': config.MIN_POSITION_SIZE_USDT,
        'MIN_TRADING_BALANCE_USDT': config.MIN_TRADING_BALANCE_USDT,
        'DRY_RUN': config.DRY_RUN
    }
    
    engine = LiveTradingEngine(
        config=config_dict,
        order_executor=executor,
        position_manager=position_mgr,
        safety_manager=safety_mgr,
        notifier=notifier
    )
    
    logger.info("모든 컴포넌트 초기화 완료")
    
    return client, engine, notifier


def create_data_fetcher(client):
    """데이터 가져오기 함수 생성"""
    
    def fetch_data():
        """최근 데이터 가져오기"""
        try:
            data = fetch_historical_data(
                client=client,
                symbol=config.SYMBOL,
                interval=config.TIMEFRAME,
                limit=300  # SMA200 + 여유분
            )
            return data
        except Exception as e:
            logger.error(f"데이터 가져오기 실패: {e}")
            return None
    
    return fetch_data


def main():
    """메인 함수"""
    
    logger.info("=" * 60)
    logger.info("⚠️  실전 매매 프로그램 시작")
    logger.info("=" * 60)
    logger.info(f"시작 시각: {datetime.now()}")
    logger.info(f"DRY_RUN: {config.DRY_RUN}")
    
    if not config.DRY_RUN:
        logger.critical("⚠️⚠️⚠️ 실전 모드! 실제 돈이 움직입니다! ⚠️⚠️⚠️")
    
    try:
        # 컴포넌트 초기화
        client, engine, notifier = initialize_components()
        
        # 데이터 가져오기 함수
        data_fetcher = create_data_fetcher(client)
        
        # 엔진 시작
        engine.start()
        
        # 마지막 일일 요약 날짜 (None으로 초기화하여 첫 9시에 전송)
        last_summary_date = None
        summary_sent_today = False  # 오늘 요약 전송 여부
        
        # 메인 루프
        while True:
            try:
                # 현재 시각
                now = datetime.now()
                current_date = now.date()
                current_hour = now.hour
                
                # 날짜가 바뀌면 플래그 리셋
                if last_summary_date != current_date:
                    summary_sent_today = False
                    last_summary_date = current_date
                
                # 9시에만 거래 로직 실행
                if current_hour == config.DAILY_REPORT_HOUR and not summary_sent_today:
                    logger.info(f"\n{'='*60}")
                    logger.info(f"🕘 거래 시간! {now.strftime('%Y-%m-%d %H:%M:%S')}")
                    logger.info(f"{'='*60}")
                    
                    # 1. 시장 체크 및 거래 (긴급 정지는 직전 refresh 기준 + TRANSFER 보정)
                    should_continue = engine.check_and_trade(data_fetcher)
                    
                    if not should_continue:
                        logger.critical("긴급 정지 조건 발생! 프로그램 종료")
                        break
                    
                    # 입출금·당일 결과 반영: 다음 날까지 initial_capital·TRANSFER 조회 기준점 갱신
                    post_trade_balance = engine.executor.get_account_balance()
                    engine.safety.refresh_initial_capital_from_balance(post_trade_balance)
                    
                    # 2. 일일 요약 (거래 처리 후)
                    logger.info("=" * 60)
                    logger.info(f"📊 일일 요약 전송 (오늘 거래 포함)")
                    logger.info("=" * 60)
                    
                    if post_trade_balance is None:
                        logger.error(
                            "거래 주기 종료 후 잔고 조회 실패 — 일일 요약 잔고를 0으로 표시합니다."
                        )
                    balance = post_trade_balance if post_trade_balance is not None else 0.0
                    
                    # 요약 전송
                    engine.send_daily_summary(balance)
                    summary_sent_today = True
                    
                    logger.info("✅ 일일 요약 전송 완료")
                    logger.info("🔄 통계 초기화 완료")
                    
                    # 다음 9시까지 정확히 대기 계산
                    from datetime import timedelta
                    next_9am = (now + timedelta(days=1)).replace(hour=config.DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
                    wait_seconds = (next_9am - datetime.now()).total_seconds()
                    
                    logger.info(f"\n💤 다음 거래: {next_9am.strftime('%Y-%m-%d %H:%M')}")
                    logger.info(f"   대기 시간: {wait_seconds/3600:.1f}시간")
                    
                    time.sleep(wait_seconds)
                else:
                    # 9시가 아니거나 이미 실행됨
                    # 다음 9시까지 대기
                    from datetime import timedelta
                    
                    if current_hour < config.DAILY_REPORT_HOUR:
                        # 오늘 9시
                        next_9am = now.replace(hour=config.DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
                    else:
                        # 내일 9시
                        next_9am = (now + timedelta(days=1)).replace(hour=config.DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
                    
                    wait_seconds = (next_9am - now).total_seconds()
                    
                    logger.info(f"\n💤 거래 시간 아님 ({now.strftime('%H:%M')})")
                    logger.info(f"   다음 거래: {next_9am.strftime('%Y-%m-%d %H:%M')}")
                    logger.info(f"   대기 시간: {wait_seconds/3600:.1f}시간")
                    
                    time.sleep(wait_seconds)
            except KeyboardInterrupt:
                logger.warning("사용자에 의해 중단됨")
                break
            
            except Exception as e:
                # 에러 메시지 정제 (민감한 정보 제거)
                sanitizer = DataSanitizer()
                safe_error = sanitizer.sanitize_error_message(str(e), max_length=100)
                
                logger.error(f"메인 루프 오류: {safe_error}")
                notifier.notify_error(f"메인 루프 오류: {safe_error}")
                
                # 짧은 대기 후 재시도
                time.sleep(10)
        
        # 종료 처리
        engine.stop()
        
        # 알림 워커 종료
        if hasattr(notifier, 'stop'):
            notifier.stop()
            # 알림 통계 출력
            if hasattr(notifier, 'get_stats'):
                notif_stats = notifier.get_stats()
                logger.info(
                    f"알림 통계: 성공={notif_stats['success_count']}, "
                    f"실패={notif_stats['failed_count']}"
                )
        
        # 최종 통계
        stats = engine.position_mgr.get_total_stats()
        logger.info(f"\n{'='*60}")
        logger.info("프로그램 종료")
        logger.info(f"총 거래: {stats['total_trades']}회")
        logger.info(f"총 손익: ${stats['total_pnl']:,.2f}")
        logger.info(f"승률: {stats['win_rate']:.2f}%")
        logger.info(f"{'='*60}")
        
    except Exception as e:
        # 에러 메시지 정제
        sanitizer = DataSanitizer()
        safe_error = sanitizer.sanitize_error_message(str(e))
        logger.critical(f"프로그램 실행 실패: {safe_error}")
        sys.exit(1)


def main_scalping():
    """TRADING_MODE=scalping — 폴링 단타 엔진."""
    import config_scalping as sc
    from binance.client import Client
    from goal_tracker import GoalTracker
    from order_executor import OrderExecutor
    from scalping_engine import ScalpingEngine
    from scalping_position_manager import ScalpingPositionManager
    from safety_manager import SafetyManager
    from utils.ai_advisor import AiAdvisor

    _reconfigure_logging(sc.LOG_LEVEL, sc.LOG_FILE)
    log = logging.getLogger(__name__)
    log.info("TRADING_MODE=scalping 시작 (DRY_RUN=%s)", sc.DRY_RUN)

    client = Client(sc.API_KEY, sc.API_SECRET)
    local_time = int(time.time() * 1000)
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/time", timeout=5)
        if r.status_code == 200:
            futures_server_time = r.json()["serverTime"]
            client.time_offset = futures_server_time - local_time - 1500
            log.info("Futures 시간 동기화 오프셋: %sms", client.time_offset)
        else:
            raise ValueError("Futures time API 실패")
    except Exception as e:
        log.warning("Futures 시간 조회 실패, Spot 시도: %s", e)
        server_time = client.get_server_time()
        client.time_offset = server_time["serverTime"] - local_time - 2000
        log.info("Spot 시간 동기화 오프셋: %sms", client.time_offset)

    if sc.KAKAO_ENABLED:
        kakao = KakaoNotifier(
            access_token=sc.KAKAO_ACCESS_TOKEN,
            enabled=True,
            rest_api_key=sc.KAKAO_REST_API_KEY,
        )
        notifier = AsyncNotifier(kakao, max_queue_size=100)
        notifier.start()
        log.info("카카오톡 알림 활성화 (스캘핑)")
    else:
        notifier = SilentNotifier()
        log.warning("카카오톡 비활성화")

    executors = {
        sym: OrderExecutor(client, sym, sc.DRY_RUN) for sym in sc.SCALPING_SYMBOLS
    }
    first_ex = next(iter(executors.values()))
    initial_balance = first_ex.get_account_balance()
    if initial_balance is None:
        raise ValueError("초기 잔고 조회 실패")

    safety_mgr = SafetyManager(
        initial_capital=initial_balance,
        daily_max_loss_percent=sc.DAILY_MAX_LOSS_PERCENT,
        emergency_stop_loss_percent=sc.EMERGENCY_STOP_LOSS_PERCENT,
        max_trades_per_day=sc.MAX_TRADES_PER_DAY,
    )
    goals = GoalTracker(sc.MONTHLY_TARGET_KRW, sc.KRW_PER_USDT)
    pos_mgr = ScalpingPositionManager()
    ai_adv = AiAdvisor(sc.OPENAI_API_KEY, sc.OPENAI_MODEL) if sc.USE_AI_CONFIRM else None

    config_dict = {
        "SCALPING_SYMBOLS": sc.SCALPING_SYMBOLS,
        "TIMEFRAME": sc.TIMEFRAME,
        "LEVERAGE": sc.LEVERAGE,
        "RSI_PERIOD": sc.RSI_PERIOD,
        "BB_PERIOD": sc.BB_PERIOD,
        "BB_STD": sc.BB_STD,
        "STOP_LOSS_PCT": sc.STOP_LOSS_PCT,
        "TP_PROFIT_KRW": sc.TP_PROFIT_KRW,
        "KRW_PER_USDT": sc.KRW_PER_USDT,
        "RISK_PER_TRADE": sc.RISK_PER_TRADE,
        "MIN_POSITION_SIZE_USDT": sc.MIN_POSITION_SIZE_USDT,
        "MIN_TRADING_BALANCE_USDT": sc.MIN_TRADING_BALANCE_USDT,
        "DRY_RUN": sc.DRY_RUN,
        "USE_AI_CONFIRM": sc.USE_AI_CONFIRM,
        "REQUIRE_USER_CONFIRM": sc.REQUIRE_USER_CONFIRM,
        "CONFIRM_TIMEOUT_SECONDS": sc.CONFIRM_TIMEOUT_SECONDS,
    }

    engine = ScalpingEngine(
        config=config_dict,
        client=client,
        executors=executors,
        position_manager=pos_mgr,
        safety_manager=safety_mgr,
        notifier=notifier,
        goal_tracker=goals,
        ai_advisor=ai_adv,
    )

    try:
        engine.start()
        while True:
            if not engine.run_once():
                log.critical("스캘핑 긴급 정지로 종료")
                break
            time.sleep(sc.POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log.warning("사용자 중단")
    finally:
        engine.stop()
        if hasattr(notifier, "stop"):
            notifier.stop()

    log.info("스캘핑 메인 종료")


if __name__ == "__main__":
    trading_mode = os.getenv("TRADING_MODE", "day").strip().lower()

    if trading_mode == "scalping":
        import config_scalping as sc

        print("\n" + "=" * 60)
        print("⚠️  TRADING_MODE=scalping (AI 단타)")
        print("=" * 60)
        print(f"DRY_RUN: {sc.DRY_RUN}")
        print(f"USE_AI_CONFIRM: {sc.USE_AI_CONFIRM} (장애 시 fail_closed)")

        if sc.DRY_RUN:
            print("✅ 테스트 모드: 실제 주문 없음.")
        else:
            print("🚨 실전 스캘핑: 실제 주문이 나갈 수 있습니다.")
            if sys.stdin.isatty():
                print("\n계속하려면 'YES' 입력:")
                confirm = input().strip()
                if not SecureComparison.constant_time_compare(confirm, "YES"):
                    print("취소합니다.")
                    sys.exit(0)
            else:
                raw = os.environ.get(
                    sc.SCALPING_LIVE_CONFIRMED_ENV, ""
                ).strip()
                if not SecureComparison.constant_time_compare(
                    raw, sc.SCALPING_LIVE_CONFIRMED_VALUE
                ):
                    print(
                        "비대화형 실전 거부. 설정: "
                        f"Environment={sc.SCALPING_LIVE_CONFIRMED_ENV}="
                        f"{sc.SCALPING_LIVE_CONFIRMED_VALUE}"
                    )
                    sys.exit(1)

        main_scalping()
        sys.exit(0)

    # 일봉 변동성 돌파 (기본)
    print("\n" + "=" * 60)
    print("⚠️⚠️⚠️  경고  ⚠️⚠️⚠️")
    print("=" * 60)
    print(f"DRY_RUN 모드: {config.DRY_RUN}")

    if config.DRY_RUN:
        print("✅ 테스트 모드: 실제 주문이 실행되지 않습니다.")
    else:
        print("🚨 실전 모드: 실제 돈이 움직입니다!")
        if sys.stdin.isatty():
            print("\n정말로 실행하시겠습니까?")
            confirm = input("계속하려면 'YES' 입력: ")
            if not SecureComparison.constant_time_compare(confirm, "YES"):
                print("프로그램을 취소합니다.")
                sys.exit(0)
        else:
            raw = os.environ.get(LIVE_TRADING_CONFIRMED_ENV, "").strip()
            if len(raw) != len(LIVE_TRADING_CONFIRMED_VALUE):
                print(
                    "비대화형 환경에서는 실전 실행이 거부되었습니다. "
                    f"systemd unit에 다음을 추가하세요: "
                    f"Environment={LIVE_TRADING_CONFIRMED_ENV}={LIVE_TRADING_CONFIRMED_VALUE}"
                )
                sys.exit(1)
            if not SecureComparison.constant_time_compare(
                raw, LIVE_TRADING_CONFIRMED_VALUE
            ):
                print(
                    "비대화형 환경에서는 실전 실행이 거부되었습니다. "
                    f"Environment={LIVE_TRADING_CONFIRMED_ENV}={LIVE_TRADING_CONFIRMED_VALUE} "
                    "를 정확히 설정하세요."
                )
                sys.exit(1)

    main()
