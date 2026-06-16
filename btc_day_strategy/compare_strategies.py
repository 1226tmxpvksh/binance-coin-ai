"""
전략 비교 백테스트
원본 전략 vs 실전 매매 설정 비교
"""

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from binance.client import Client

import config as config_original
import config_live_settings as config_live
from data_loader import fetch_historical_data
from indicators import add_all_indicators
from backtest_engine import BacktestEngine
from performance import generate_performance_report
from visualization import create_comparison_chart

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            'strategy_comparison.log', maxBytes=512 * 1024, backupCount=2, encoding='utf-8'
        )
    ]
)

logger = logging.getLogger(__name__)


def run_backtest_with_config(data, config_module, config_name):
    """
    특정 설정으로 백테스트 실행
    
    Args:
        data: OHLCV 데이터
        config_module: 설정 모듈
        config_name: 설정 이름
    
    Returns:
        dict: 백테스트 결과
    """
    logger.info("=" * 60)
    logger.info(f"백테스트 실행: {config_name}")
    logger.info("=" * 60)
    
    # 지표 계산
    data = add_all_indicators(
        data,
        config_module.EMA_SHORT_PERIOD,
        config_module.EMA_LONG_PERIOD,
        config_module.SMA_LONG_PERIOD,
        config_module.ATR_PERIOD,
        config_module.VOLUME_MA_PERIOD
    )
    
    # 설정 딕셔너리 생성
    config_dict = {
        'SYMBOL': config_module.SYMBOL,
        'LEVERAGE': config_module.LEVERAGE,
        'EMA_SHORT_PERIOD': config_module.EMA_SHORT_PERIOD,
        'EMA_LONG_PERIOD': config_module.EMA_LONG_PERIOD,
        'SMA_LONG_PERIOD': config_module.SMA_LONG_PERIOD,
        'ATR_PERIOD': config_module.ATR_PERIOD,
        'VOLUME_MA_PERIOD': config_module.VOLUME_MA_PERIOD,
        'K_MAX': config_module.K_MAX,
        'STOP_LOSS_MULTIPLIER': config_module.STOP_LOSS_MULTIPLIER,
        'TAKE_PROFIT_MULTIPLIER': config_module.TAKE_PROFIT_MULTIPLIER,
        'MAX_HOLD_DAYS': config_module.MAX_HOLD_DAYS,
        'TRAILING_STOP_ATR_MULT': config_module.TRAILING_STOP_ATR_MULT,
        'RISK_PER_TRADE': config_module.RISK_PER_TRADE,
        'MAX_TRADES_PER_DAY': config_module.MAX_TRADES_PER_DAY,
        'SINGLE_POSITION_ONLY': config_module.SINGLE_POSITION_ONLY
    }
    
    # MIN_POSITION_SIZE_USDT가 있으면 추가 (실전 설정에만 있음)
    if hasattr(config_module, 'MIN_POSITION_SIZE_USDT'):
        config_dict['MIN_POSITION_SIZE_USDT'] = config_module.MIN_POSITION_SIZE_USDT
    
    # 백테스트 엔진 생성
    engine = BacktestEngine(
        config=config_dict,
        initial_capital=config_module.INITIAL_CAPITAL
    )
    
    # 백테스트 실행
    result = engine.run(data)
    
    return result


def print_comparison_summary(results):
    """
    비교 결과 요약 출력
    
    Args:
        results: {config_name: (result, report)} 딕셔너리
    """
    print("\n" + "=" * 80)
    print("📊 전략 비교 요약")
    print("=" * 80)
    
    # 헤더
    print(f"\n{'항목':<20} {'원본 전략':>20} {'실전 설정':>20} {'차이':>15}")
    print("-" * 80)
    
    # 데이터 추출
    original = results['원본 전략'][1]
    live = results['실전 설정'][1]
    
    # 주요 지표 비교
    metrics = [
        ('초기 자본', 'initial_capital', '$'),
        ('최종 자본', 'final_capital', '$'),
        ('총 수익률', 'total_return', '%'),
        ('연간 수익률', 'annual_return', '%'),
        ('총 거래 횟수', 'total_trades', '회'),
        ('승률', 'win_rate', '%'),
        ('평균 수익', 'avg_profit', '$'),
        ('평균 손실', 'avg_loss', '$'),
        ('손익비', 'profit_factor', '배'),
        ('최대 낙폭', 'max_drawdown', '%'),
        ('샤프 비율', 'sharpe_ratio', ''),
    ]
    
    for name, key, unit in metrics:
        orig_val = original.get(key, 0)
        live_val = live.get(key, 0)
        
        # 차이 계산
        if unit == '%' or unit == '배':
            diff = live_val - orig_val
            diff_str = f"{diff:+.2f}{unit}"
        elif unit == '$':
            diff = live_val - orig_val
            diff_str = f"${diff:+,.2f}"
        elif unit == '회':
            diff = int(live_val - orig_val)
            diff_str = f"{diff:+d}{unit}"
        else:
            diff = live_val - orig_val
            diff_str = f"{diff:+.3f}"
        
        # 포맷팅
        if unit == '$':
            orig_str = f"${orig_val:,.2f}"
            live_str = f"${live_val:,.2f}"
        elif unit == '%':
            orig_str = f"{orig_val:.2f}%"
            live_str = f"{live_val:.2f}%"
        elif unit == '회':
            orig_str = f"{int(orig_val)}{unit}"
            live_str = f"{int(live_val)}{unit}"
        elif unit == '배':
            orig_str = f"{orig_val:.2f}{unit}"
            live_str = f"{live_val:.2f}{unit}"
        else:
            orig_str = f"{orig_val:.3f}"
            live_str = f"{live_val:.3f}"
        
        print(f"{name:<20} {orig_str:>20} {live_str:>20} {diff_str:>15}")
    
    print("=" * 80)
    
    # 핵심 차이점 설명
    print("\n💡 핵심 차이점:")
    print(f"  - RISK_PER_TRADE: 2% (원본) vs 1% (실전)")
    print(f"  - MIN_POSITION_SIZE: 없음 (원본) vs $20 (실전)")
    print(f"  - 안전장치: 없음 (원본) vs 일일/긴급 정지 (실전)")
    
    # 추천
    print("\n✨ 추천:")
    if live['total_return'] > original['total_return']:
        print("  → 실전 설정이 더 좋은 성과를 보입니다!")
    elif live['max_drawdown'] < original['max_drawdown']:
        print("  → 실전 설정이 더 안전합니다 (낙폭 작음)")
    else:
        print("  → 원본 설정이 더 공격적이지만 위험합니다")
    
    print("\n" + "=" * 80)


def main():
    """메인 함수"""
    
    print("\n" + "=" * 80)
    print("🔬 전략 비교 백테스트")
    print("=" * 80)
    print("원본 전략 vs 실전 매매 설정 비교")
    print("=" * 80)
    
    try:
        # 1. 바이낸스 클라이언트 초기화
        logger.info("바이낸스 클라이언트 초기화...")
        client = Client(config_original.API_KEY, config_original.API_SECRET)
        
        # 2. 데이터 로드 (두 설정 모두 동일한 기간)
        logger.info(f"데이터 로드: {config_original.START_DATE} ~ {config_original.END_DATE}")
        data = fetch_historical_data(
            client=client,
            symbol=config_original.SYMBOL,
            interval=config_original.TIMEFRAME,
            start_date=config_original.START_DATE,
            end_date=config_original.END_DATE
        )
        
        if data is None or data.empty:
            logger.error("데이터 로드 실패")
            return
        
        logger.info(f"데이터 로드 완료: {len(data)}개 캔들")
        
        # 3. 백테스트 실행
        results = {}
        
        # 원본 전략
        logger.info("\n[1/2] 원본 전략 백테스트...")
        result_original = run_backtest_with_config(
            data.copy(),
            config_original,
            "원본 전략 (RISK 2%)"
        )
        report_original = generate_performance_report(
            result_original,
            config_original.INITIAL_CAPITAL
        )
        results['원본 전략'] = (result_original, report_original)
        
        # 실전 설정
        logger.info("\n[2/2] 실전 설정 백테스트...")
        result_live = run_backtest_with_config(
            data.copy(),
            config_live,
            "실전 설정 (RISK 1%)"
        )
        report_live = generate_performance_report(
            result_live,
            config_live.INITIAL_CAPITAL
        )
        results['실전 설정'] = (result_live, report_live)
        
        # 4. 비교 요약 출력
        print_comparison_summary(results)
        
        # 5. 상세 리포트
        print("\n" + "=" * 80)
        print("📈 원본 전략 (RISK 2%) 상세 결과")
        print("=" * 80)
        print(f"총 수익률: {report_original['total_return']:.2f}%")
        print(f"승률: {report_original['win_rate']:.2f}%")
        print(f"최대 낙폭: {report_original['max_drawdown']:.2f}%")
        print(f"샤프 비율: {report_original['sharpe_ratio']:.3f}")
        
        print("\n" + "=" * 80)
        print("📈 실전 설정 (RISK 1%) 상세 결과")
        print("=" * 80)
        print(f"총 수익률: {report_live['total_return']:.2f}%")
        print(f"승률: {report_live['win_rate']:.2f}%")
        print(f"최대 낙폭: {report_live['max_drawdown']:.2f}%")
        print(f"샤프 비율: {report_live['sharpe_ratio']:.3f}")
        
        # 6. 시각화 생성
        logger.info("\n시각화 생성 중...")
        
        # 개별 결과 시각화
        from visualization import create_all_visualizations
        
        create_all_visualizations(
            result_original,
            config_original.INITIAL_CAPITAL,
            output_dir="output/original"
        )
        logger.info("✅ 원본 전략 시각화 저장: output/original/")
        
        create_all_visualizations(
            result_live,
            config_live.INITIAL_CAPITAL,
            output_dir="output/live_settings"
        )
        logger.info("✅ 실전 설정 시각화 저장: output/live_settings/")
        
        # 비교 차트 생성
        create_comparison_chart(
            results['원본 전략'][0],
            results['실전 설정'][0],
            config_original.INITIAL_CAPITAL,
            output_dir="output/comparison"
        )
        logger.info("✅ 비교 차트 저장: output/comparison/")
        
        print("\n✅ 전략 비교 완료!")
        print(f"📊 결과 그래프:")
        print(f"   - 원본 전략: output/original/")
        print(f"   - 실전 설정: output/live_settings/")
        print(f"   - 비교 차트: output/comparison/")
        
    except KeyboardInterrupt:
        logger.warning("사용자에 의해 중단됨")
        sys.exit(0)
    
    except Exception as e:
        logger.error(f"오류 발생: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
