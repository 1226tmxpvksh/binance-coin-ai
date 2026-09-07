"""USDT→KRW 환율 조회 (수동 .env 설정 없이 외부 시세 사용)."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_CACHE_VALUE: float | None = None
_CACHE_MONO: float = 0.0
_CACHE_TTL_SEC = 120.0
_FALLBACK_KRW = 1380.0


def _parse_coingecko(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    tether = payload.get("tether")
    if not isinstance(tether, dict):
        return None
    krw = tether.get("krw")
    try:
        v = float(krw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def fetch_usdt_krw(*, timeout: float = 12.0, force_refresh: bool = False) -> float:
    """
    CoinGecko `tether` 대비 `krw` 시세를 사용한다.
    (글로벌 Binance 공개 API에는 USDT/KRW 심볼이 없어 동일 경로를 쓰지 않는다.)
    """
    global _CACHE_VALUE, _CACHE_MONO
    now = time.monotonic()
    if (
        not force_refresh
        and _CACHE_VALUE is not None
        and (now - _CACHE_MONO) < _CACHE_TTL_SEC
    ):
        return _CACHE_VALUE

    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "tether", "vs_currencies": "krw"}
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        rate = _parse_coingecko(r.json())
        if rate is not None:
            _CACHE_VALUE = rate
            _CACHE_MONO = now
            return rate
    except requests.RequestException as exc:
        logger.warning("USDT/KRW 조회 실패(%s), 캐시·폴백 사용", type(exc).__name__)

    if _CACHE_VALUE is not None:
        logger.info("USDT/KRW: 이전 캐시 값 %.2f 사용", _CACHE_VALUE)
        return _CACHE_VALUE

    logger.warning("USDT/KRW: 폴백 고정값 %.0f 사용 (네트워크·API 점검)", _FALLBACK_KRW)
    return _FALLBACK_KRW
