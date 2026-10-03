"""
Binance 현물 시장가 매수.

선물 주문, 레버리지, 청산, 지갑 이체는 이 모듈에 두지 않는다.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


class SpotBuyError(RuntimeError):
    """주문을 보내지 않았거나, 체결 수량을 확인하지 못한 경우."""


def _sanitize_api_value(value: str) -> str:
    raw = value or ""
    return "".join(ch for ch in raw if ch.isascii() and (ch.isprintable() or ch in "\t ")).strip()


def spot_client_from_env() -> Any | None:
    key = _sanitize_api_value(os.getenv("BINANCE_API_KEY", os.getenv("API_KEY", "")))
    secret = _sanitize_api_value(os.getenv("BINANCE_API_SECRET", os.getenv("API_SECRET", "")))
    if not key or not secret:
        logger.warning("BINANCE API 키가 없어 현물 클라이언트를 만들 수 없습니다.")
        return None
    try:
        from binance.client import Client

        return Client(key, secret)
    except ImportError:
        logger.warning("python-binance 미설치")
        return None


def spot_free_balance(client: Any, asset: str) -> float | None:
    """현물 지갑 가용 잔고. 조회 실패 시 None."""
    try:
        row = client.get_asset_balance(asset=asset.upper())
    except Exception as exc:
        logger.warning("현물 잔고 조회 실패: %s", type(exc).__name__)
        return None
    if not isinstance(row, dict):
        return None
    try:
        return max(0.0, float(row.get("free") or 0))
    except (TypeError, ValueError):
        return None


def spot_base_qty(client: Any, asset: str = "BTC") -> float | None:
    """현물 free + locked. 조회 실패 시 None."""
    try:
        row = client.get_asset_balance(asset=asset.upper())
    except Exception as exc:
        logger.warning("현물 수량 조회 실패: %s", type(exc).__name__)
        return None
    if not isinstance(row, dict):
        return None
    try:
        free = float(row.get("free") or 0)
        locked = float(row.get("locked") or 0)
    except (TypeError, ValueError):
        return None
    return max(0.0, free) + max(0.0, locked)


def _symbol_info(client: Any, symbol: str) -> Dict[str, Any]:
    info = client.get_symbol_info(symbol)
    if not isinstance(info, dict):
        raise SpotBuyError(f"현물 심볼 정보를 읽지 못했습니다: {symbol}")
    return info


def _min_notional(info: Dict[str, Any]) -> float:
    for filt in info.get("filters") or []:
        if not isinstance(filt, dict):
            continue
        if str(filt.get("filterType")) in {"MIN_NOTIONAL", "NOTIONAL"}:
            try:
                return max(0.0, float(filt.get("minNotional") or 0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _quote_precision(info: Dict[str, Any]) -> int:
    raw = info.get("quoteAssetPrecision", info.get("quotePrecision", 2))
    try:
        precision = int(raw)
    except (TypeError, ValueError):
        precision = 2
    return min(8, max(0, precision))


def floor_quote(amount: float, precision: int) -> float:
    if amount <= 0:
        return 0.0
    factor = 10 ** precision
    return math.floor(amount * factor + 1e-9) / factor


def spot_market_buy_quote(client: Any, symbol: str, quote_usdt: float) -> Dict[str, Any]:
    """USDT 금액으로 현물 시장가 매수. 최소 주문금액 미만이면 주문을 보내지 않는다."""
    if quote_usdt <= 0:
        raise SpotBuyError("매수 금액이 0 이하입니다.")
    info = _symbol_info(client, symbol)
    minimum = _min_notional(info)
    quote = floor_quote(quote_usdt, _quote_precision(info))
    if quote <= 0 or (minimum > 0 and quote < minimum):
        raise SpotBuyError(f"최소 주문금액 미달: {quote} < {minimum}")
    order = client.order_market_buy(
        symbol=symbol,
        quoteOrderQty=f"{quote:.{_quote_precision(info)}f}",
        newOrderRespType="FULL",
    )
    if not isinstance(order, dict):
        raise SpotBuyError("현물 주문 응답이 없습니다.")
    try:
        qty = float(order.get("executedQty") or 0)
        spent = float(order.get("cummulativeQuoteQty") or 0)
    except (TypeError, ValueError) as exc:
        raise SpotBuyError("체결 수량을 읽지 못했습니다.") from exc
    if qty <= 0 or spent <= 0:
        raise SpotBuyError("체결 수량이 없습니다. 진입 기록을 쓰지 않습니다.")
    return {
        "symbol": symbol,
        "price": spent / qty,
        "qty_btc": qty,
        "quote_usdt": spent,
        "order_id": str(order.get("orderId") or ""),
    }
