"""
성과 분석 모듈
백테스트 결과를 바탕으로 다양한 성과 지표 계산
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)


def calculate_total_return(
    initial_capital: float,
    final_capital: float
) -> float:
    """
    총 수익률 계산
    
    Args:
        initial_capital: 초기 자본
        final_capital: 최종 자본
    
    Returns:
        float: 총 수익률 (%)
    """
    if initial_capital == 0:
        logger.error("초기 자본이 0입니다")
        return 0.0
    
    total_return = ((final_capital - initial_capital) / initial_capital) * 100
    
    return total_return


def calculate_annualized_return(
    total_return: float,
    start_date: datetime,
    end_date: datetime
) -> float:
    """
    연평균 수익률 계산
    
    Args:
        total_return: 총 수익률 (%)
        start_date: 시작 날짜
        end_date: 종료 날짜
    
    Returns:
        float: 연평균 수익률 (%)
    """
    # 기간 계산 (년 단위)
    days = (end_date - start_date).days
    years = days / 365.25
    
    if years <= 0:
        logger.warning("기간이 0 이하입니다")
        return 0.0
    
    # 연평균 수익률 = ((1 + 총수익률) ^ (1/년수)) - 1
    annualized_return = (((1 + total_return / 100) ** (1 / years)) - 1) * 100
    
    return annualized_return


def calculate_monthly_average_return(
    total_return: float,
    start_date: datetime,
    end_date: datetime
) -> float:
    """
    월 평균 수익률 계산
    
    Args:
        total_return: 총 수익률 (%)
        start_date: 시작 날짜
        end_date: 종료 날짜
    
    Returns:
        float: 월 평균 수익률 (%)
    """
    # 기간 계산 (월 단위)
    days = (end_date - start_date).days
    months = days / 30.44  # 평균 월일수
    
    if months <= 0:
        logger.warning("기간이 0 이하입니다")
        return 0.0
    
    # 월 평균 수익률
    monthly_return = total_return / months
    
    return monthly_return


def calculate_win_rate(trades: List[Dict[str, Any]]) -> float:
    """
    승률 계산
    
    Args:
        trades: 거래 리스트
    
    Returns:
        float: 승률 (%)
    """
    if not trades:
        logger.warning("거래 내역이 없습니다")
        return 0.0
    
    winning_trades = sum(1 for trade in trades if trade['pnl'] > 0)
    total_trades = len(trades)
    
    win_rate = (winning_trades / total_trades) * 100
    
    return win_rate


def calculate_max_drawdown(equity_curve: List[Dict[str, Any]]) -> float:
    """
    최대 낙폭 (MDD) 계산
    
    Args:
        equity_curve: 자본 곡선 리스트
    
    Returns:
        float: 최대 낙폭 (%)
    """
    if not equity_curve:
        logger.warning("자본 곡선 데이터가 없습니다")
        return 0.0
    
    # 자본 시리즈 추출
    equity_series = [point['equity'] for point in equity_curve]
    equity_array = np.array(equity_series)
    
    # 누적 최대값
    cumulative_max = np.maximum.accumulate(equity_array)
    
    # 낙폭 계산
    drawdown = (equity_array - cumulative_max) / cumulative_max * 100
    
    # 최대 낙폭
    max_drawdown = abs(drawdown.min())
    
    return max_drawdown


def calculate_profit_factor(trades: List[Dict[str, Any]]) -> float:
    """
    프로핏 팩터 계산
    
    총 수익 / 총 손실
    
    Args:
        trades: 거래 리스트
    
    Returns:
        float: 프로핏 팩터
    """
    if not trades:
        return 0.0
    
    gross_profit = sum(trade['pnl'] for trade in trades if trade['pnl'] > 0)
    gross_loss = abs(sum(trade['pnl'] for trade in trades if trade['pnl'] < 0))
    
    if gross_loss == 0:
        return float('inf') if gross_profit > 0 else 0.0
    
    profit_factor = gross_profit / gross_loss
    
    return profit_factor


def calculate_average_trade(trades: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    평균 거래 통계
    
    Args:
        trades: 거래 리스트
    
    Returns:
        Dict: 평균 통계
    """
    if not trades:
        return {
            'avg_pnl': 0.0,
            'avg_profit': 0.0,
            'avg_loss': 0.0,
            'avg_hold_days': 0.0
        }
    
    winning_trades = [t for t in trades if t['pnl'] > 0]
    losing_trades = [t for t in trades if t['pnl'] < 0]
    
    avg_pnl = np.mean([t['pnl'] for t in trades])
    avg_profit = np.mean([t['pnl'] for t in winning_trades]) if winning_trades else 0.0
    avg_loss = np.mean([t['pnl'] for t in losing_trades]) if losing_trades else 0.0
    avg_hold_days = np.mean([t['hold_days'] for t in trades])
    
    return {
        'avg_pnl': avg_pnl,
        'avg_profit': avg_profit,
        'avg_loss': avg_loss,
        'avg_hold_days': avg_hold_days
    }


def analyze_yearly_performance(
    equity_curve: List[Dict[str, Any]],
    initial_capital: float
) -> Dict[int, Dict[str, float]]:
    """
    연도별 성과 분석
    
    Args:
        equity_curve: 자본 곡선 리스트
        initial_capital: 초기 자본
    
    Returns:
        Dict: 연도별 성과
    """
    if not equity_curve:
        return {}
    
    # DataFrame으로 변환
    df = pd.DataFrame(equity_curve)
    df['year'] = pd.to_datetime(df['date']).dt.year
    
    yearly_performance = {}
    
    for year in df['year'].unique():
        year_data = df[df['year'] == year]
        
        # 해당 연도 시작/종료 자본
        start_equity = year_data.iloc[0]['equity']
        end_equity = year_data.iloc[-1]['equity']
        
        # 연간 수익률
        year_return = ((end_equity - start_equity) / start_equity) * 100
        
        # 연간 최대 낙폭
        year_equity = year_data['equity'].values
        year_cummax = np.maximum.accumulate(year_equity)
        year_drawdown = (year_equity - year_cummax) / year_cummax * 100
        year_mdd = abs(year_drawdown.min())
        
        yearly_performance[int(year)] = {
            'start_equity': start_equity,
            'end_equity': end_equity,
            'return': year_return,
            'max_drawdown': year_mdd
        }
    
    return yearly_performance


def generate_performance_report(
    backtest_result: Dict[str, Any],
    initial_capital: float
) -> Dict[str, Any]:
    """
    전체 성과 리포트 생성
    
    Args:
        backtest_result: 백테스트 결과
        initial_capital: 초기 자본
    
    Returns:
        Dict: 성과 리포트
    """
    trades = backtest_result['trades']
    equity_curve = backtest_result['equity_curve']
    final_capital = backtest_result['final_capital']
    
    # 날짜 범위
    if equity_curve:
        start_date = equity_curve[0]['date']
        end_date = equity_curve[-1]['date']
    else:
        start_date = end_date = datetime.now()
    
    # 수익률 계산
    total_return = calculate_total_return(initial_capital, final_capital)
    annualized_return = calculate_annualized_return(total_return, start_date, end_date)
    monthly_avg_return = calculate_monthly_average_return(total_return, start_date, end_date)
    
    # 거래 통계
    win_rate = calculate_win_rate(trades)
    max_drawdown = calculate_max_drawdown(equity_curve)
    profit_factor = calculate_profit_factor(trades)
    avg_trade = calculate_average_trade(trades)
    
    # 연도별 성과
    yearly_performance = analyze_yearly_performance(equity_curve, initial_capital)
    
    # 롱/숏 통계
    long_trades = [t for t in trades if t['side'] == 'LONG']
    short_trades = [t for t in trades if t['side'] == 'SHORT']
    
    report = {
        'period': {
            'start_date': start_date,
            'end_date': end_date,
            'days': (end_date - start_date).days
        },
        'capital': {
            'initial': initial_capital,
            'final': final_capital,
            'profit': final_capital - initial_capital
        },
        'returns': {
            'total_return_pct': total_return,
            'annualized_return_pct': annualized_return,
            'monthly_avg_return_pct': monthly_avg_return
        },
        'risk': {
            'max_drawdown_pct': max_drawdown,
            'profit_factor': profit_factor
        },
        'trades': {
            'total_trades': len(trades),
            'long_trades': len(long_trades),
            'short_trades': len(short_trades),
            'win_rate_pct': win_rate,
            'avg_pnl': avg_trade['avg_pnl'],
            'avg_profit': avg_trade['avg_profit'],
            'avg_loss': avg_trade['avg_loss'],
            'avg_hold_days': avg_trade['avg_hold_days']
        },
        'yearly_performance': yearly_performance
    }
    
    return report


def print_performance_summary(report: Dict[str, Any]):
    """
    성과 요약 출력
    
    Args:
        report: 성과 리포트
    """
    print("\n" + "=" * 60)
    print("백테스트 성과 리포트")
    print("=" * 60)
    
    print(f"\n[기간 정보]")
    print(f"시작일: {report['period']['start_date']}")
    print(f"종료일: {report['period']['end_date']}")
    print(f"기간: {report['period']['days']}일")
    
    print(f"\n[자본 정보]")
    print(f"초기 자본: ${report['capital']['initial']:,.2f}")
    print(f"최종 자본: ${report['capital']['final']:,.2f}")
    print(f"순손익: ${report['capital']['profit']:,.2f}")
    
    print(f"\n[수익률]")
    print(f"총 수익률: {report['returns']['total_return_pct']:.2f}%")
    print(f"연평균 수익률: {report['returns']['annualized_return_pct']:.2f}%")
    print(f"월 평균 수익률: {report['returns']['monthly_avg_return_pct']:.2f}%")
    
    print(f"\n[리스크 지표]")
    print(f"최대 낙폭 (MDD): {report['risk']['max_drawdown_pct']:.2f}%")
    print(f"프로핏 팩터: {report['risk']['profit_factor']:.2f}")
    
    print(f"\n[거래 통계]")
    print(f"총 거래 횟수: {report['trades']['total_trades']}회")
    print(f"LONG 거래: {report['trades']['long_trades']}회")
    print(f"SHORT 거래: {report['trades']['short_trades']}회")
    print(f"승률: {report['trades']['win_rate_pct']:.2f}%")
    print(f"평균 손익: ${report['trades']['avg_pnl']:.2f}")
    print(f"평균 수익: ${report['trades']['avg_profit']:.2f}")
    print(f"평균 손실: ${report['trades']['avg_loss']:.2f}")
    print(f"평균 보유 기간: {report['trades']['avg_hold_days']:.1f}일")
    
    print(f"\n[연도별 성과]")
    for year, perf in sorted(report['yearly_performance'].items()):
        print(f"{year}년: 수익률 {perf['return']:.2f}%, MDD {perf['max_drawdown']:.2f}%")
    
    print("\n" + "=" * 60)
