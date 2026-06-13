"""Market Data Layer: 4H OHLCV 데이터 검증과 지표 준비."""

from __future__ import annotations

import pandas as pd

from strategy.indicators import prepare_indicators

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]
EXPECTED_INTERVALS = {
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "8h": pd.Timedelta(hours=8),
    "12h": pd.Timedelta(hours=12),
    "1d": pd.Timedelta(days=1),
}


def validate_ohlcv(data: pd.DataFrame, interval: str, max_gap_multiplier: float) -> tuple[bool, str]:
    if data is None or data.empty:
        return False, "데이터가 비어 있습니다"
    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        return False, f"필수 컬럼 누락: {missing}"
    if data[REQUIRED_COLUMNS].isna().any().any():
        return False, "OHLCV 결측값 존재"
    if data.index.has_duplicates:
        return False, "중복 timestamp 존재"
    if not data.index.is_monotonic_increasing:
        return False, "timestamp 정렬 오류"
    if (data["volume"] < 0).any():
        return False, "음수 거래량 존재"

    invalid_ohlc = (
        (data["high"] < data[["open", "close"]].max(axis=1))
        | (data["low"] > data[["open", "close"]].min(axis=1))
        | (data["high"] < data["low"])
    )
    if invalid_ohlc.any():
        return False, "OHLC 관계 오류"

    expected_delta = EXPECTED_INTERVALS.get(interval)
    if expected_delta is not None and len(data) > 1:
        max_allowed_gap = expected_delta * max_gap_multiplier
        gaps = data.index.to_series().diff().dropna()
        if (gaps > max_allowed_gap).any():
            return False, "시간봉 연속성 오류"
    return True, "OK"


def build_market_data(
    raw_data: pd.DataFrame,
    interval: str,
    max_gap_multiplier: float,
    ema_period: int,
    atr_period: int,
    pivot_length: int,
) -> pd.DataFrame:
    is_valid, reason = validate_ohlcv(raw_data, interval, max_gap_multiplier)
    if not is_valid:
        raise ValueError(reason)

    data = prepare_indicators(raw_data, ema_period, atr_period, pivot_length)
    data.rename(
        columns={
            "ema_200": "ema200",
            "atr": "atr14",
            "confirmed_pivot_low": "pivot_low",
        },
        inplace=True,
    )
    data["ema_200"] = data["ema200"]
    data["atr"] = data["atr14"]
    data["confirmed_pivot_low"] = data["pivot_low"]
    return data

