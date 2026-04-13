"""
데이터 로드 모듈 (실전 매매용)
바이낸스 API를 통해 OHLCV 데이터를 가져오고 pandas DataFrame으로 변환
"""

import pandas as pd
import logging
from typing import Optional
from binance.client import Client

logger = logging.getLogger(__name__)


def fetch_historical_data(
    client: Client,
    symbol: str,
    interval: str,
    limit: int = 500,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> pd.DataFrame:
    """바이낸스 선물 시장에서 과거 데이터를 가져옴"""
    try:
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        if start_date:
            params["startTime"] = int(pd.Timestamp(start_date).timestamp() * 1000)
        if end_date:
            params["endTime"] = int(pd.Timestamp(end_date).timestamp() * 1000)

        klines = client.futures_klines(**params)

        if not klines:
            raise ValueError(f"심볼 {symbol}에 대한 데이터를 가져오지 못했습니다")

    except Exception as e:
        logger.error(f"데이터 가져오기 실패: {e}")
        raise RuntimeError(f"데이터 가져오기 실패: {e}") from e

    try:
        data = pd.DataFrame(klines, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ])
        data["timestamp"] = pd.to_datetime(data["timestamp"], unit="ms")
        data = data[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        data[["open", "high", "low", "close", "volume"]] = \
            data[["open", "high", "low", "close", "volume"]].astype(float)
        data.set_index("timestamp", inplace=True)

        logger.info(f"{symbol} 데이터 로드 완료: {len(data)}개 캔들")
        return data

    except Exception as e:
        logger.error(f"데이터 변환 실패: {e}")
        raise ValueError(f"데이터 변환 실패: {e}") from e


def validate_data(data: pd.DataFrame) -> bool:
    """데이터 유효성 검증"""
    if data is None or data.empty:
        return False

    required_columns = ["open", "high", "low", "close", "volume"]
    if any(col not in data.columns for col in required_columns):
        return False

    if data[required_columns].isnull().any().any():
        logger.warning("데이터에 NaN 값이 포함되어 있습니다")
        return False

    return True
