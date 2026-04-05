"""
메인 실행 파일
백테스트 실행 및 결과 출력
"""

import logging
import sys
from datetime import datetime

import config
from data_loader import fetch_historical_data, validate_data
from indicators import add_all_indicators
from backtest_engine import BacktestEngine
from performance import generate_performance_report, print_performance_summary
from visualization import create_all_visualizations

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('backtest.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)


def initialize_binance_client():
    """
    바이낸스 클라이언트 초기화
    
    Returns:
        Client: 바이낸스 클라이언트 인스턴스
    """
    try:
        from binance.client import Client
        import time
        
        client = Client(config.API_KEY, config.API_SECRET)
        
        # 서버 시간 동기화
        server_time = client.get_server_time()
        local_time = int(time.time() * 1000)
        client.time_offset = server_time['serverTime'] - local_time
        
        logger.info(f"바이낸스 클라이언트 초기화 완료. 시간 오프셋: {client.time_offset}ms")
        
        return client
    
    except Exception as e:
        logger.error(f"바이낸스 클라이언트 초기화 실패: {e}")
        raise


def load_and_prepare_data(client):
    """
    데이터 로드 및 지표 계산
    
    Args:
        client: 바이낸스 클라이언트
    
    Returns:
        pd.DataFrame: 지표가 추가된 데이터
    """
    logger.info(f"데이터 로드 시작: {config.SYMBOL} ({config.START_DATE} ~ {config.END_DATE})")
    
    # 데이터 로드
    data = fetch_historical_data(
        client=client,
        symbol=config.SYMBOL,
        interval=config.TIMEFRAME,
        limit=1000,
        start_date=config.START_DATE,
        end_date=config.END_DATE
    )
    
    # 데이터 검증
    if not validate_data(data):
        raise ValueError("유효하지 않은 데이터입니다")
    
    logger.info(f"데이터 로드 완료: {len(data)}개 캔들")
    
    # 지표 추가
    logger.info("기술적 지표 계산 중...")
    data_with_indicators = add_all_indicators(
        data=data,
        ema_short=config.EMA_SHORT_PERIOD,
        ema_long=config.EMA_LONG_PERIOD,
        sma_long=config.SMA_LONG_PERIOD,
        atr_period=config.ATR_PERIOD,
        vol_ma_period=config.VOLUME_MA_PERIOD
    )
    
    logger.info("지표 계산 완료")
    
    return data_with_indicators


def run_backtest(data):
    """
    백테스트 실행
    
    Args:
        data: 지표가 추가된 데이터
    
    Returns:
        Dict: 백테스트 결과
    """
    logger.info("=" * 60)
    logger.info("백테스트 시작")
    logger.info("=" * 60)
    
    # 설정 딕셔너리 생성
    config_dict = {
        'INITIAL_CAPITAL': config.INITIAL_CAPITAL,
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
        'SINGLE_POSITION_ONLY': config.SINGLE_POSITION_ONLY
    }
    
    # 백테스트 엔진 초기화
    engine = BacktestEngine(config_dict)
    
    # 백테스트 실행
    result = engine.run(data)
    
    logger.info("=" * 60)
    logger.info("백테스트 완료")
    logger.info("=" * 60)
    
    return result


def main():
    """메인 함수"""
    
    try:
        # 시작 시간 기록
        start_time = datetime.now()
        logger.info(f"프로그램 시작: {start_time}")
        
        # 1. 바이낸스 클라이언트 초기화
        client = initialize_binance_client()
        
        # 2. 데이터 로드 및 준비
        data = load_and_prepare_data(client)
        
        # 3. 백테스트 실행
        backtest_result = run_backtest(data)
        
        # 4. 성과 분석
        logger.info("성과 분석 중...")
        performance_report = generate_performance_report(
            backtest_result,
            config.INITIAL_CAPITAL
        )
        
        # 5. 성과 요약 출력
        print_performance_summary(performance_report)
        
        # 6. 시각화 생성
        logger.info("시각화 생성 중...")
        create_all_visualizations(
            backtest_result,
            config.INITIAL_CAPITAL,
            output_dir="output"
        )
        
        # 종료 시간 기록
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        
        logger.info(f"프로그램 종료: {end_time}")
        logger.info(f"총 소요 시간: {elapsed_time:.2f}초")
        
        print(f"\n✅ 백테스트가 성공적으로 완료되었습니다!")
        print(f"📊 결과 그래프는 'output' 폴더에 저장되었습니다.")
        print(f"📝 로그는 'backtest.log' 파일에 저장되었습니다.")
        
    except KeyboardInterrupt:
        logger.warning("사용자에 의해 프로그램이 중단되었습니다")
        sys.exit(0)
    
    except Exception as e:
        logger.error(f"프로그램 실행 중 오류 발생: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
