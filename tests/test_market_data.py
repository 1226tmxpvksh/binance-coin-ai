import pandas as pd

from strategy.market_data import validate_ohlcv


def test_validate_ohlcv_accepts_valid_4h_data():
    index = pd.date_range("2024-01-01", periods=3, freq="4h")
    data = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1.0, 2.0, 3.0],
        },
        index=index,
    )

    is_valid, reason = validate_ohlcv(data, "4h", 1.2)

    assert is_valid is True
    assert reason == "OK"


def test_validate_ohlcv_rejects_invalid_ohlc():
    index = pd.date_range("2024-01-01", periods=1, freq="4h")
    data = pd.DataFrame(
        {
            "open": [100.0],
            "high": [99.0],
            "low": [98.0],
            "close": [101.0],
            "volume": [1.0],
        },
        index=index,
    )

    is_valid, reason = validate_ohlcv(data, "4h", 1.2)

    assert is_valid is False
    assert reason == "OHLC 관계 오류"

