"""
데이터 로드 모듈
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
    """
    바이낸스 선물 시장에서 과거 데이터를 가져옴
    
    Args:
        client: 바이낸스 클라이언트 인스턴스
        symbol: 거래 심볼 (예: "BTCUSDT")
        interval: 시간 간격 (예: "1d")
        limit: 가져올 캔들 개수
        start_date: 시작 날짜 (옵션, "2020-01-01" 형식)
        end_date: 종료 날짜 (옵션, "2024-12-31" 형식)
    
    Returns:
        pd.DataFrame: OHLCV 데이터
    
    Raises:
        RuntimeError: 데이터 가져오기 실패 시
        ValueError: 데이터 변환 실패 시
    """
    try:
        # API 호출 파라미터 구성
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
            error_msg = f"심볼 {symbol}에 대한 데이터를 가져오지 못했습니다"
            logger.error(error_msg)
            raise ValueError(error_msg)
            
    except Exception as e:
        error_msg = f"{symbol}의 과거 데이터 가져오기 실패: {e}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    
    # 데이터 변환
    try:
        data = pd.DataFrame(klines, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ])
        
        # 날짜 변환
        data["timestamp"] = pd.to_datetime(data["timestamp"], unit="ms")
        
        # 필요한 컬럼만 선택 및 타입 변환
        data = data[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        data[["open", "high", "low", "close", "volume"]] = \
            data[["open", "high", "low", "close", "volume"]].astype(float)
        
        # 인덱스 설정
        data.set_index("timestamp", inplace=True)
        
        logger.info(f"{symbol} 데이터 로드 완료: {len(data)}개 캔들")
        return data
        
    except Exception as e:
        error_msg = f"{symbol} 데이터 변환 실패: {e}"
        logger.error(error_msg)
        raise ValueError(error_msg)


def validate_data(data: pd.DataFrame) -> bool:
    """
    데이터 유효성 검증
    
    Args:
        data: 검증할 DataFrame
    
    Returns:
        bool: 유효한 데이터인 경우 True
    """
    if data is None or data.empty:
        logger.error("데이터가 비어있습니다")
        return False
    
    required_columns = ["open", "high", "low", "close", "volume"]
    missing_columns = [col for col in required_columns if col not in data.columns]
    
    if missing_columns:
        logger.error(f"필수 컬럼 누락: {missing_columns}")
        return False
    
    # NaN 값 확인
    if data[required_columns].isnull().any().any():
        logger.warning("데이터에 NaN 값이 포함되어 있습니다")
        return False
    
    return True
