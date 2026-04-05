"""
시각화 모듈
백테스트 결과를 차트로 시각화
"""

import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
import logging
import os
from typing import Dict, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# 한글 폰트 설정
plt.rcParams['font.family'] = 'Malgun Gothic'  # Windows
plt.rcParams['axes.unicode_minus'] = False  # 마이너스 기호 깨짐 방지


def plot_equity_curve(
    equity_curve: List[Dict[str, Any]],
    initial_capital: float,
    save_path: str = None
):
    """
    전체 자본 곡선 그래프
    
    Args:
        equity_curve: 자본 곡선 리스트
        initial_capital: 초기 자본
        save_path: 저장 경로 (옵션)
    """
    if not equity_curve:
        logger.warning("자본 곡선 데이터가 없습니다")
        return
    
    # DataFrame 변환
    df = pd.DataFrame(equity_curve)
    df['date'] = pd.to_datetime(df['date'])
    
    # 그래프 생성
    fig, ax = plt.subplots(figsize=(14, 7))
    
    ax.plot(df['date'], df['equity'], linewidth=2, color='#2E86AB')
    ax.axhline(y=initial_capital, color='red', linestyle='--', 
               linewidth=1, label='초기 자본')
    
    ax.set_title('전체 자본 곡선 (Equity Curve)', fontsize=16, fontweight='bold')
    ax.set_xlabel('날짜', fontsize=12)
    ax.set_ylabel('자본 (USDT)', fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10)
    
    # 금액 포맷
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info(f"자본 곡선 그래프 저장: {save_path}")
    
    plt.show()


def plot_yearly_cumulative_returns(
    equity_curve: List[Dict[str, Any]],
    initial_capital: float,
    save_path: str = None
):
    """
    연도별 누적 수익률 꺾은선 그래프
    
    Args:
        equity_curve: 자본 곡선 리스트
        initial_capital: 초기 자본
        save_path: 저장 경로 (옵션)
    """
    if not equity_curve:
        logger.warning("자본 곡선 데이터가 없습니다")
        return
    
    # DataFrame 변환
    df = pd.DataFrame(equity_curve)
    df['date'] = pd.to_datetime(df['date'])
    df['year'] = df['date'].dt.year
    
    # 누적 수익률 계산
    df['cumulative_return'] = ((df['equity'] - initial_capital) / initial_capital) * 100
    
    # 그래프 생성
    fig, ax = plt.subplots(figsize=(14, 7))
    
    # 연도별 색상
    colors = plt.cm.Set3(np.linspace(0, 1, len(df['year'].unique())))
    
    for i, year in enumerate(sorted(df['year'].unique())):
        year_data = df[df['year'] == year]
        ax.plot(
            year_data['date'],
            year_data['cumulative_return'],
            linewidth=2.5,
            color=colors[i],
            label=f'{year}년'
        )
    
    ax.axhline(y=0, color='black', linestyle='-', linewidth=1, alpha=0.5)
    
    ax.set_title('연도별 누적 수익률', fontsize=16, fontweight='bold')
    ax.set_xlabel('날짜', fontsize=12)
    ax.set_ylabel('누적 수익률 (%)', fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10, loc='best')
    
    # 퍼센트 포맷
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:.1f}%'))
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info(f"연도별 수익률 그래프 저장: {save_path}")
    
    plt.show()


def plot_drawdown_curve(
    equity_curve: List[Dict[str, Any]],
    save_path: str = None
):
    """
    낙폭 (Drawdown) 곡선 그래프
    
    Args:
        equity_curve: 자본 곡선 리스트
        save_path: 저장 경로 (옵션)
    """
    if not equity_curve:
        logger.warning("자본 곡선 데이터가 없습니다")
        return
    
    # DataFrame 변환
    df = pd.DataFrame(equity_curve)
    df['date'] = pd.to_datetime(df['date'])
    
    # 낙폭 계산
    equity_array = df['equity'].values
    cumulative_max = np.maximum.accumulate(equity_array)
    drawdown = (equity_array - cumulative_max) / cumulative_max * 100
    
    df['drawdown'] = drawdown
    
    # 그래프 생성
    fig, ax = plt.subplots(figsize=(14, 7))
    
    ax.fill_between(df['date'], df['drawdown'], 0, 
                     color='#E63946', alpha=0.6)
    ax.plot(df['date'], df['drawdown'], 
            color='#A4161A', linewidth=1.5)
    
    ax.set_title('낙폭 곡선 (Drawdown)', fontsize=16, fontweight='bold')
    ax.set_xlabel('날짜', fontsize=12)
    ax.set_ylabel('낙폭 (%)', fontsize=12)
    ax.grid(True, alpha=0.3)
    
    # 퍼센트 포맷
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:.1f}%'))
    
    # 최대 낙폭 표시
    max_dd_idx = df['drawdown'].idxmin()
    max_dd_value = df.loc[max_dd_idx, 'drawdown']
    max_dd_date = df.loc[max_dd_idx, 'date']
    
    ax.annotate(
        f'MDD: {max_dd_value:.2f}%',
        xy=(max_dd_date, max_dd_value),
        xytext=(10, 10),
        textcoords='offset points',
        fontsize=11,
        bbox=dict(boxstyle='round,pad=0.5', fc='yellow', alpha=0.7),
        arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0')
    )
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info(f"낙폭 곡선 그래프 저장: {save_path}")
    
    plt.show()


def plot_monthly_returns(
    trades: List[Dict[str, Any]],
    save_path: str = None
):
    """
    월별 수익 분포 막대 그래프
    
    Args:
        trades: 거래 리스트
        save_path: 저장 경로 (옵션)
    """
    if not trades:
        logger.warning("거래 데이터가 없습니다")
        return
    
    # DataFrame 변환
    df = pd.DataFrame(trades)
    df['exit_date'] = pd.to_datetime(df['exit_date'])
    df['year_month'] = df['exit_date'].dt.to_period('M')
    
    # 월별 수익 합계
    monthly_pnl = df.groupby('year_month')['pnl'].sum()
    
    # 그래프 생성
    fig, ax = plt.subplots(figsize=(14, 7))
    
    colors = ['green' if x > 0 else 'red' for x in monthly_pnl.values]
    
    monthly_pnl.plot(kind='bar', ax=ax, color=colors, alpha=0.7, width=0.8)
    
    ax.axhline(y=0, color='black', linestyle='-', linewidth=1)
    
    ax.set_title('월별 수익 분포', fontsize=16, fontweight='bold')
    ax.set_xlabel('연월', fontsize=12)
    ax.set_ylabel('수익 (USDT)', fontsize=12)
    ax.grid(True, alpha=0.3, axis='y')
    
    # X축 레이블 회전
    plt.xticks(rotation=45, ha='right')
    
    # 금액 포맷
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info(f"월별 수익 그래프 저장: {save_path}")
    
    plt.show()


def plot_trade_distribution(
    trades: List[Dict[str, Any]],
    save_path: str = None
):
    """
    거래 분포 히스토그램
    
    Args:
        trades: 거래 리스트
        save_path: 저장 경로 (옵션)
    """
    if not trades:
        logger.warning("거래 데이터가 없습니다")
        return
    
    df = pd.DataFrame(trades)
    
    # 2x2 서브플롯
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # 1. 손익 분포
    axes[0, 0].hist(df['pnl'], bins=30, color='skyblue', edgecolor='black', alpha=0.7)
    axes[0, 0].axvline(x=0, color='red', linestyle='--', linewidth=2)
    axes[0, 0].set_title('손익 분포', fontsize=12, fontweight='bold')
    axes[0, 0].set_xlabel('손익 (USDT)', fontsize=10)
    axes[0, 0].set_ylabel('빈도', fontsize=10)
    axes[0, 0].grid(True, alpha=0.3)
    
    # 2. 수익률 분포
    axes[0, 1].hist(df['profit_rate'], bins=30, color='lightgreen', edgecolor='black', alpha=0.7)
    axes[0, 1].axvline(x=0, color='red', linestyle='--', linewidth=2)
    axes[0, 1].set_title('수익률 분포', fontsize=12, fontweight='bold')
    axes[0, 1].set_xlabel('수익률 (%)', fontsize=10)
    axes[0, 1].set_ylabel('빈도', fontsize=10)
    axes[0, 1].grid(True, alpha=0.3)
    
    # 3. 보유 기간 분포
    axes[1, 0].hist(df['hold_days'], bins=20, color='orange', edgecolor='black', alpha=0.7)
    axes[1, 0].set_title('보유 기간 분포', fontsize=12, fontweight='bold')
    axes[1, 0].set_xlabel('보유 일수', fontsize=10)
    axes[1, 0].set_ylabel('빈도', fontsize=10)
    axes[1, 0].grid(True, alpha=0.3)
    
    # 4. 롱/숏 비율 파이 차트
    side_counts = df['side'].value_counts()
    axes[1, 1].pie(
        side_counts.values,
        labels=side_counts.index,
        autopct='%1.1f%%',
        startangle=90,
        colors=['#66B2FF', '#FF9999']
    )
    axes[1, 1].set_title('롱/숏 거래 비율', fontsize=12, fontweight='bold')
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info(f"거래 분포 그래프 저장: {save_path}")
    
    plt.show()


def create_all_visualizations(
    backtest_result: Dict[str, Any],
    initial_capital: float,
    output_dir: str = "output"
):
    """
    모든 시각화 생성
    
    Args:
        backtest_result: 백테스트 결과
        initial_capital: 초기 자본
        output_dir: 출력 디렉토리
    """
    import os
    
    # 출력 디렉토리 생성
    os.makedirs(output_dir, exist_ok=True)
    
    equity_curve = backtest_result['equity_curve']
    trades = backtest_result['trades']
    
    logger.info("시각화 생성 시작...")
    
    # 1. 자본 곡선
    plot_equity_curve(
        equity_curve,
        initial_capital,
        save_path=os.path.join(output_dir, '01_equity_curve.png')
    )
    
    # 2. 연도별 누적 수익률
    plot_yearly_cumulative_returns(
        equity_curve,
        initial_capital,
        save_path=os.path.join(output_dir, '02_yearly_returns.png')
    )
    
    # 3. 낙폭 곡선
    plot_drawdown_curve(
        equity_curve,
        save_path=os.path.join(output_dir, '03_drawdown.png')
    )
    
    # 4. 월별 수익
    plot_monthly_returns(
        trades,
        save_path=os.path.join(output_dir, '04_monthly_returns.png')
    )
    
    # 5. 거래 분포
    plot_trade_distribution(
        trades,
        save_path=os.path.join(output_dir, '05_trade_distribution.png')
    )
    
    logger.info(f"모든 시각화 완료. 저장 위치: {output_dir}")


def create_comparison_chart(
    result_original: Dict[str, Any],
    result_live: Dict[str, Any],
    initial_capital: float,
    output_dir: str = "output/comparison"
):
    """
    두 전략 비교 차트 생성
    
    Args:
        result_original: 원본 전략 백테스트 결과
        result_live: 실전 설정 백테스트 결과
        initial_capital: 초기 자본
        output_dir: 저장 디렉토리
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 자본 곡선 비교
    equity_original = pd.DataFrame(result_original['equity_curve'])
    equity_original['date'] = pd.to_datetime(equity_original['date'])
    
    equity_live = pd.DataFrame(result_live['equity_curve'])
    equity_live['date'] = pd.to_datetime(equity_live['date'])
    
    # 그래프 생성
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    # 1. 자본 곡선 비교
    ax1 = axes[0, 0]
    ax1.plot(equity_original['date'], equity_original['equity'], 
             linewidth=2, color='#2E86AB', label='원본 전략 (RISK 2%)', alpha=0.8)
    ax1.plot(equity_live['date'], equity_live['equity'], 
             linewidth=2, color='#A23B72', label='실전 설정 (RISK 1%)', alpha=0.8)
    ax1.axhline(y=initial_capital, color='gray', linestyle='--', 
                linewidth=1, alpha=0.5, label='초기 자본')
    ax1.set_title('자본 곡선 비교', fontsize=14, fontweight='bold')
    ax1.set_xlabel('날짜', fontsize=11)
    ax1.set_ylabel('자본 (USDT)', fontsize=11)
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    
    # 2. 수익률 비교 (바 차트)
    ax2 = axes[0, 1]
    from performance import generate_performance_report
    report_orig = generate_performance_report(result_original, initial_capital)
    report_live = generate_performance_report(result_live, initial_capital)
    
    metrics = ['총 수익률', '연간 수익률', '승률']
    orig_values = [report_orig['total_return'], report_orig['annual_return'], report_orig['win_rate']]
    live_values = [report_live['total_return'], report_live['annual_return'], report_live['win_rate']]
    
    x = np.arange(len(metrics))
    width = 0.35
    
    ax2.bar(x - width/2, orig_values, width, label='원본 (RISK 2%)', color='#2E86AB', alpha=0.8)
    ax2.bar(x + width/2, live_values, width, label='실전 (RISK 1%)', color='#A23B72', alpha=0.8)
    
    ax2.set_title('수익률 비교', fontsize=14, fontweight='bold')
    ax2.set_ylabel('퍼센트 (%)', fontsize=11)
    ax2.set_xticks(x)
    ax2.set_xticklabels(metrics)
    ax2.legend()
    ax2.grid(True, alpha=0.3, axis='y')
    
    # 값 표시
    for i, (ov, lv) in enumerate(zip(orig_values, live_values)):
        ax2.text(i - width/2, ov + 1, f'{ov:.1f}%', ha='center', va='bottom', fontsize=9)
        ax2.text(i + width/2, lv + 1, f'{lv:.1f}%', ha='center', va='bottom', fontsize=9)
    
    # 3. 리스크 비교
    ax3 = axes[1, 0]
    
    risk_metrics = ['최대 낙폭', '평균 손실']
    orig_risk = [abs(report_orig['max_drawdown']), abs(report_orig['avg_loss'])]
    live_risk = [abs(report_live['max_drawdown']), abs(report_live['avg_loss'])]
    
    x = np.arange(len(risk_metrics))
    ax3.bar(x - width/2, orig_risk, width, label='원본 (RISK 2%)', color='#FF6B6B', alpha=0.8)
    ax3.bar(x + width/2, live_risk, width, label='실전 (RISK 1%)', color='#FFA94D', alpha=0.8)
    
    ax3.set_title('리스크 비교 (낮을수록 좋음)', fontsize=14, fontweight='bold')
    ax3.set_ylabel('값', fontsize=11)
    ax3.set_xticks(x)
    ax3.set_xticklabels(risk_metrics)
    ax3.legend()
    ax3.grid(True, alpha=0.3, axis='y')
    
    # 값 표시
    for i, (ov, lv) in enumerate(zip(orig_risk, live_risk)):
        if risk_metrics[i] == '최대 낙폭':
            ax3.text(i - width/2, ov + 0.5, f'{ov:.1f}%', ha='center', va='bottom', fontsize=9)
            ax3.text(i + width/2, lv + 0.5, f'{lv:.1f}%', ha='center', va='bottom', fontsize=9)
        else:
            ax3.text(i - width/2, ov + 5, f'${ov:.0f}', ha='center', va='bottom', fontsize=9)
            ax3.text(i + width/2, lv + 5, f'${lv:.0f}', ha='center', va='bottom', fontsize=9)
    
    # 4. 거래 통계 비교
    ax4 = axes[1, 1]
    
    trade_metrics = ['총 거래', '승리', '손실']
    orig_trades = [
        report_orig['total_trades'],
        report_orig['winning_trades'],
        report_orig['losing_trades']
    ]
    live_trades = [
        report_live['total_trades'],
        report_live['winning_trades'],
        report_live['losing_trades']
    ]
    
    x = np.arange(len(trade_metrics))
    ax4.bar(x - width/2, orig_trades, width, label='원본 (RISK 2%)', color='#2E86AB', alpha=0.8)
    ax4.bar(x + width/2, live_trades, width, label='실전 (RISK 1%)', color='#A23B72', alpha=0.8)
    
    ax4.set_title('거래 통계 비교', fontsize=14, fontweight='bold')
    ax4.set_ylabel('횟수', fontsize=11)
    ax4.set_xticks(x)
    ax4.set_xticklabels(trade_metrics)
    ax4.legend()
    ax4.grid(True, alpha=0.3, axis='y')
    
    # 값 표시
    for i, (ov, lv) in enumerate(zip(orig_trades, live_trades)):
        ax4.text(i - width/2, ov + 1, f'{int(ov)}', ha='center', va='bottom', fontsize=9)
        ax4.text(i + width/2, lv + 1, f'{int(lv)}', ha='center', va='bottom', fontsize=9)
    
    plt.tight_layout()
    
    # 저장
    save_path = f"{output_dir}/strategy_comparison.png"
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    logger.info(f"비교 차트 저장: {save_path}")
    
    plt.close()
