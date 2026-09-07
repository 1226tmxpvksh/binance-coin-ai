"""
기술적 지표 계산 모듈 (실전 매매용)
EMA, SMA, ATR 등 전략에 필요한 모든 지표를 계산
"""

import pandas as pd
import numpy as np
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """지수 이동 평균 계산"""
    if series is None or len(series) < period:
        logger.warning(f"EMA 계산을 위한 데이터 부족: {len(series)} < {period}")
        return pd.Series(index=series.index, dtype=float)

    return series.ewm(span=period, adjust=False).mean()


def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """단순 이동 평균 계산"""
    if series is None or len(series) < period:
        logger.warning(f"SMA 계산을 위한 데이터 부족: {len(series)} < {period}")
        return pd.Series(index=series.index, dtype=float)

    return series.rolling(window=period).mean()


def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int
) -> pd.Series:
    """Average True Range 계산"""
    if len(high) < period or len(low) < period or len(close) < period:
        logger.warning("ATR 계산을 위한 데이터 부족")
        return pd.Series(index=high.index, dtype=float)

    high_low = high - low
    high_close_prev = abs(high - close.shift(1))
    low_close_prev = abs(low - close.shift(1))
    true_range = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    return atr


def calculate_sma_slope(sma: pd.Series, lookback: int = 5) -> pd.Series:
    """SMA의 기울기 계산"""
    if sma is None or len(sma) < lookback:
        logger.warning("SMA 기울기 계산을 위한 데이터 부족")
        return pd.Series(index=sma.index, dtype=float)
    return sma - sma.shift(lookback)


def calculate_volume_ma(volume: pd.Series, period: int) -> pd.Series:
    """거래량 이동평균 계산"""
    if volume is None or len(volume) < period:
        logger.warning("거래량 이동평균 계산을 위한 데이터 부족")
        return pd.Series(index=volume.index, dtype=float)
    return volume.rolling(window=period).mean()


def add_all_indicators(
    data: pd.DataFrame,
    ema_short: int,
    ema_long: int,
    sma_long: int,
    atr_period: int,
    vol_ma_period: int
) -> pd.DataFrame:
    """데이터에 모든 지표 추가"""
    df = data.copy()

    df['ema_short'] = calculate_ema(df['close'], ema_short)
    df['ema_long'] = calculate_ema(df['close'], ema_long)
    df['sma_long'] = calculate_sma(df['close'], sma_long)
    df['sma_slope'] = calculate_sma_slope(df['sma_long'])
    df['atr'] = calculate_atr(df['high'], df['low'], df['close'], atr_period)
    df['vol_ma'] = calculate_volume_ma(df['volume'], vol_ma_period)

    logger.debug("모든 지표 계산 완료")
    return df


def calculate_k_value(atr_current: float, atr_max: float, k_max: float) -> float:
    """변동성 돌파 K값 계산 (상한 적용)"""
    if atr_max == 0 or atr_current is None or np.isnan(atr_current):
        logger.warning("K값 계산 실패: 유효하지 않은 ATR 값")
        return 0.0

    k_raw = atr_current / atr_max
    return min(k_raw, k_max)
