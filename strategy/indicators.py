"""4H 전략 지표 계산: EMA200, ATR14, Pivot Low."""

from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calculate_atr(data: pd.DataFrame, period: int) -> pd.Series:
    high_low = data["high"] - data["low"]
    high_close_prev = (data["high"] - data["close"].shift(1)).abs()
    low_close_prev = (data["low"] - data["close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()


def add_confirmed_pivots(data: pd.DataFrame, length: int) -> pd.DataFrame:
    df = data.copy()
    df["confirmed_pivot_low"] = np.nan
    df["confirmed_pivot_low_time"] = pd.NaT

    for center_idx in range(length, len(df) - length):
        center_low = df["low"].iloc[center_idx]
        left_low = df["low"].iloc[center_idx - length : center_idx].min()
        right_low = df["low"].iloc[center_idx + 1 : center_idx + length + 1].min()
        if center_low < left_low and center_low < right_low:
            confirm_idx = center_idx + length
            df.iloc[confirm_idx, df.columns.get_loc("confirmed_pivot_low")] = float(center_low)
            df.iloc[confirm_idx, df.columns.get_loc("confirmed_pivot_low_time")] = df.index[center_idx]
    return df


def prepare_indicators(data: pd.DataFrame, ema_period: int, atr_period: int, pivot_length: int) -> pd.DataFrame:
    df = data.copy()
    df["ema_200"] = calculate_ema(df["close"], ema_period)
    df["atr"] = calculate_atr(df, atr_period)
    return add_confirmed_pivots(df, pivot_length)

