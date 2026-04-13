"""
README2.md 기준 전략 백테스트 실행 스크립트

목표:
1. README2 기준 전략 규칙을 `btc_day_strategy`의 공유 모듈로 백테스트
2. 2024년, 2025년 연도별 수익률/승률/MDD/진입 횟수 출력
3. 원본 설정(config.py)과 실전 설정(config_live_settings.py) 비교
"""

import logging
import sys
from datetime import datetime
from typing import Dict, Any

from binance.client import Client

import config as config_original
import config_live_settings as config_live
from backtest_engine import BacktestEngine
from data_loader import fetch_historical_data, validate_data
from indicators import add_all_indicators
from performance import generate_performance_report


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('readme2_backtest.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)


def build_config_dict(config_module) -> Dict[str, Any]:
    """설정 모듈을 백테스트 엔진용 딕셔너리로 변환"""
    config_dict = {
        'INITIAL_CAPITAL': config_module.INITIAL_CAPITAL,
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
        'SINGLE_POSITION_ONLY': config_module.SINGLE_POSITION_ONLY,
    }

    if hasattr(config_module, 'MIN_POSITION_SIZE_USDT'):
        config_dict['MIN_POSITION_SIZE_USDT'] = config_module.MIN_POSITION_SIZE_USDT

    return config_dict


def initialize_client() -> Client:
    """바이낸스 클라이언트 초기화"""
    client = Client(config_original.API_KEY, config_original.API_SECRET)
    logger.info("바이낸스 클라이언트 초기화 완료")
    return client


def load_data(client: Client) -> Any:
    """공통 백테스트 데이터 로드"""
    logger.info(
        "데이터 로드 시작: %s %s ~ %s",
        config_original.SYMBOL,
        config_original.START_DATE,
        config_original.END_DATE,
    )
    data = fetch_historical_data(
        client=client,
        symbol=config_original.SYMBOL,
        interval=config_original.TIMEFRAME,
        limit=1000,
        start_date=config_original.START_DATE,
        end_date=config_original.END_DATE,
    )

    if not validate_data(data):
        raise ValueError("유효하지 않은 백테스트 데이터입니다.")

    return data


def run_backtest(data, config_module) -> Dict[str, Any]:
    """설정별 백테스트 실행"""
    data_with_indicators = add_all_indicators(
        data=data.copy(),
        ema_short=config_module.EMA_SHORT_PERIOD,
        ema_long=config_module.EMA_LONG_PERIOD,
        sma_long=config_module.SMA_LONG_PERIOD,
        atr_period=config_module.ATR_PERIOD,
        vol_ma_period=config_module.VOLUME_MA_PERIOD
    )

    engine = BacktestEngine(build_config_dict(config_module))
    result = engine.run(data_with_indicators)
    report = generate_performance_report(result, config_module.INITIAL_CAPITAL)
    return {
        'result': result,
        'report': report,
    }


def filter_backtest_result_by_year(backtest_result: Dict[str, Any], initial_capital: float, year: int) -> Dict[str, Any]:
    """백테스트 결과를 특정 연도로 필터링하여 재집계"""
    trades = [
        trade for trade in backtest_result['trades']
        if trade['entry_date'].year == year
    ]
    equity_curve = [
        point for point in backtest_result['equity_curve']
        if point['date'].year == year
    ]

    if equity_curve:
        start_equity = equity_curve[0]['equity']
        adjusted_equity_curve = []
        for point in equity_curve:
            adjusted_equity_curve.append({
                'date': point['date'],
                'equity': initial_capital + (point['equity'] - start_equity),
                'has_position': point['has_position'],
            })
        final_capital = adjusted_equity_curve[-1]['equity']
    else:
        adjusted_equity_curve = []
        final_capital = initial_capital

    return {
        'trades': trades,
        'equity_curve': adjusted_equity_curve,
        'final_capital': final_capital,
    }


def summarize_year(label: str, backtest_payload: Dict[str, Any], initial_capital: float, year: int) -> Dict[str, Any]:
    """연도별 핵심 지표 집계"""
    filtered_result = filter_backtest_result_by_year(
        backtest_payload['result'],
        initial_capital,
        year,
    )
    report = generate_performance_report(filtered_result, initial_capital)
    return {
        'label': label,
        'year': year,
        'annual_return_pct': report['returns']['total_return_pct'],
        'win_rate_pct': report['trades']['win_rate_pct'],
        'mdd_pct': report['risk']['max_drawdown_pct'],
        'entries': report['trades']['total_trades'],
    }


def print_yearly_summary(summary: Dict[str, Any]) -> None:
    """연도별 결과 출력"""
    print(
        f"{summary['label']} {summary['year']} | "
        f"수익률 {summary['annual_return_pct']:.2f}% | "
        f"승률 {summary['win_rate_pct']:.2f}% | "
        f"MDD {summary['mdd_pct']:.2f}% | "
        f"진입 {summary['entries']}회"
    )


def print_average_summary(label: str, yearly_summaries: list[Dict[str, Any]]) -> None:
    """평균 결과 출력"""
    count = len(yearly_summaries)
    if count == 0:
        return

    avg_return = sum(item['annual_return_pct'] for item in yearly_summaries) / count
    avg_win_rate = sum(item['win_rate_pct'] for item in yearly_summaries) / count
    avg_mdd = sum(item['mdd_pct'] for item in yearly_summaries) / count
    avg_entries = sum(item['entries'] for item in yearly_summaries) / count

    print(
        f"{label} 평균 | "
        f"연평균 수익률 {avg_return:.2f}% | "
        f"평균 승률 {avg_win_rate:.2f}% | "
        f"평균 MDD {avg_mdd:.2f}% | "
        f"연평균 진입 {avg_entries:.1f}회"
    )


def main() -> None:
    logger.info("README2 기준 백테스트 시작")
    start_time = datetime.now()

    client = initialize_client()
    data = load_data(client)

    original_payload = run_backtest(data, config_original)
    live_payload = run_backtest(data, config_live)

    original_summaries = []
    live_summaries = []

    print("\n" + "=" * 80)
    print("README2 기준 연도별 백테스트")
    print("=" * 80)

    for year in (2024, 2025):
        original_summary = summarize_year(
            "원본 설정",
            original_payload,
            config_original.INITIAL_CAPITAL,
            year,
        )
        live_summary = summarize_year(
            "실전 설정",
            live_payload,
            config_live.INITIAL_CAPITAL,
            year,
        )

        original_summaries.append(original_summary)
        live_summaries.append(live_summary)

        print_yearly_summary(original_summary)
        print_yearly_summary(live_summary)
        print("-" * 80)

    print_average_summary("원본 설정", original_summaries)
    print_average_summary("실전 설정", live_summaries)

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("README2 기준 백테스트 완료 (%.2f초)", elapsed)


if __name__ == "__main__":
    main()
