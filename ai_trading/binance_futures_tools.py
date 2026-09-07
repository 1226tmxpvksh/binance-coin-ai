"""
USDT-M 선물: 포지션 조회·시장가 청산·전체 청산.
`main_ai` 종료 핸들러 및 `scripts/emergency_exit.py`에서 공용으로 사용한다.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from binance.exceptions import BinanceAPIException
except ImportError:
    BinanceAPIException = Exception  # type: ignore[misc, assignment]


def _sanitize_api_value(value: str) -> str:
    raw = value or ""
    return "".join(ch for ch in raw if ch.isascii() and (ch.isprintable() or ch in "\t ")).strip()


def futures_client_from_env() -> Any | None:
    key = _sanitize_api_value(os.getenv("BINANCE_API_KEY", os.getenv("API_KEY", "")))
    secret = _sanitize_api_value(os.getenv("BINANCE_API_SECRET", os.getenv("API_SECRET", "")))
    if not key or not secret:
        logger.warning("BINANCE API 키가 없어 선물 클라이언트를 만들 수 없습니다.")
        return None
    try:
        from binance.client import Client

        return Client(key, secret)
    except ImportError:
        logger.warning("python-binance 미설치")
        return None


def futures_wallet_usdt_balance(client: Any) -> float | None:
    """USDT-M 선물 지갑 USDT 잔고 (`futures_account_balance`)."""
    try:
        account = client.futures_account_balance()
        for row in account or []:
            if str(row.get("asset", "")).upper() == "USDT":
                bal = float(row.get("balance", 0) or 0)
                return bal if bal >= 0 else None
    except Exception as exc:
        logger.warning("futures_account_balance 조회 실패: %s", type(exc).__name__)
    return None


def fetch_futures_usdt_balance_from_env() -> float | None:
    """`.env` 키로 클라이언트 생성 후 USDT 잔고만 반환 (`main_ai` 등 단일 진입점)."""
    client = futures_client_from_env()
    if client is None:
        return None
    return futures_wallet_usdt_balance(client)


def _lot_step_size(client: Any, symbol: str) -> float:
    try:
        info = client.futures_exchange_info()
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        return float(f.get("stepSize", "0.001"))
    except Exception as exc:
        logger.warning("LOT_SIZE 조회 실패(%s), step=0.001 가정", type(exc).__name__)
    return 0.001


def round_quantity_to_step(client: Any, symbol: str, quantity: float) -> float:
    if quantity <= 0:
        return 0.0
    step = _lot_step_size(client, symbol)
    if step <= 0:
        return quantity
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 8
    n = math.floor(quantity / step + 1e-12) * step
    return float(f"{n:.{decimals}f}")


def futures_dual_side_position(client: Any) -> bool:
    try:
        r = client.futures_get_position_mode()
        return bool(r.get("dualSidePosition"))
    except Exception:
        return False


def futures_open_position_rows(client: Any) -> List[Dict[str, Any]]:
    """positionAmt != 0 인 행만 반환."""
    try:
        rows = client.futures_position_information()
    except Exception as exc:
        logger.error("futures_position_information 실패: %s", type(exc).__name__)
        return []
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        try:
            amt = float(row.get("positionAmt", 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if abs(amt) >= 1e-12:
            out.append(row)
    return out


def signed_position_amt_for_symbol(client: Any, symbol: str) -> float:
    """원웨이: 심볼당 합계 positionAmt. 헤지: LONG·SHORT 합산(롱+, 숏-)."""
    sym = symbol.upper()
    total = 0.0
    for row in futures_open_position_rows(client):
        if str(row.get("symbol", "")).upper() != sym:
            continue
        try:
            total += float(row.get("positionAmt", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _futures_market_reduce(
    client: Any,
    symbol: str,
    *,
    side: str,
    quantity: float,
    position_side: str | None,
) -> None:
    params: Dict[str, Any] = {
        "symbol": symbol.upper(),
        "side": side,
        "type": "MARKET",
        "quantity": quantity,
        "reduceOnly": True,
    }
    if position_side in ("LONG", "SHORT"):
        params["positionSide"] = position_side
    client.futures_create_order(**params)


def market_close_symbol(client: Any, symbol: str) -> Tuple[bool, str]:
    """해당 심볼의 열린 USDT-M 포지션을 모두 시장가로 줄인다."""
    sym = symbol.upper()
    dual = futures_dual_side_position(client)
    rows = [r for r in futures_open_position_rows(client) if str(r.get("symbol", "")).upper() == sym]
    if not rows:
        return True, "no_position"

    msgs: List[str] = []
    ok_all = True

    if dual:
        for row in rows:
            try:
                amt = float(row.get("positionAmt", 0) or 0)
            except (TypeError, ValueError):
                amt = 0.0
            if abs(amt) < 1e-12:
                continue
            ps = str(row.get("positionSide", "BOTH")).upper()
            qty = round_quantity_to_step(client, sym, abs(amt))
            if qty <= 0:
                ok_all = False
                msgs.append(f"{ps}:qty0")
                continue
            try:
                if ps == "LONG" and amt > 0:
                    _futures_market_reduce(client, sym, side="SELL", quantity=qty, position_side="LONG")
                    msgs.append(f"LONG:{qty}")
                elif ps == "SHORT" and amt < 0:
                    _futures_market_reduce(client, sym, side="BUY", quantity=qty, position_side="SHORT")
                    msgs.append(f"SHORT:{qty}")
                else:
                    logger.warning("헤지 포지션 행 스킵 %s ps=%s amt=%s", sym, ps, amt)
            except Exception as exc:
                logger.exception("헤지 청산 실패 %s: %s", sym, exc)
                ok_all = False
                msgs.append(f"fail:{type(exc).__name__}")
        return ok_all, ";".join(msgs) or "dual_ok"

    net = 0.0
    for row in rows:
        try:
            net += float(row.get("positionAmt", 0) or 0)
        except (TypeError, ValueError):
            continue
    return market_close_signed_position(client, sym, net)


def market_close_signed_position(
    client: Any,
    symbol: str,
    signed_amt: float,
) -> Tuple[bool, str]:
    """원웨이 전용: signed_amt > 0 롱→SELL, < 0 숏→BUY."""
    sym = symbol.upper()
    if abs(signed_amt) < 1e-12:
        return True, "no_position"
    qty = round_quantity_to_step(client, sym, abs(signed_amt))
    if qty <= 0:
        return False, "qty_rounded_zero"
    side = "SELL" if signed_amt > 0 else "BUY"
    try:
        _futures_market_reduce(client, sym, side=side, quantity=qty, position_side=None)
        return True, f"{side}x{qty}"
    except Exception as exc:
        logger.exception("시장가 청산 실패 %s: %s", sym, exc)
        return False, type(exc).__name__


def ensure_symbol_leverage(client: Any, symbol: str, leverage: int) -> None:
    lev = int(max(1, min(125, leverage)))
    try:
        client.futures_change_leverage(symbol=symbol.upper(), leverage=lev)
    except Exception as exc:
        logger.warning("futures_change_leverage(%s, %s) 스킵: %s", symbol, lev, type(exc).__name__)


def futures_market_open_position(
    client: Any,
    symbol: str,
    *,
    entry_side: str,
    quantity: float,
    leverage: int,
) -> Dict[str, Any]:
    """
    USDT-M 신규 진입(시장가). entry_side는 전략 신호와 동일: BUY=롱, SELL=숏.
    원웨이: BUY/SELL 만 전달. 헤지: positionSide LONG/SHORT 추가.
    """
    sym = symbol.upper()
    es = str(entry_side).upper()
    if es not in ("BUY", "SELL"):
        raise ValueError(f"entry_side must be BUY or SELL, got {entry_side!r}")

    qty = round_quantity_to_step(client, sym, float(quantity))
    if qty <= 0:
        raise ValueError("유효 수량이 없습니다(step 반올림 후 0)")

    ensure_symbol_leverage(client, sym, leverage)

    dual = futures_dual_side_position(client)
    params: Dict[str, Any] = {
        "symbol": sym,
        "side": es,
        "type": "MARKET",
        "quantity": qty,
        "reduceOnly": False,
    }
    if dual:
        params["positionSide"] = "LONG" if es == "BUY" else "SHORT"

    try:
        order = client.futures_create_order(**params)
    except BinanceAPIException:
        raise
    if not isinstance(order, dict):
        raise RuntimeError("futures_create_order 비정상 응답")
    return order


def market_buy_symbol(
    client: Any,
    symbol: str,
    quantity: float,
    *,
    leverage: int,
) -> Dict[str, Any]:
    """롱 진입용 편의 래퍼(BUY 시장가)."""
    return futures_market_open_position(
        client, symbol, entry_side="BUY", quantity=quantity, leverage=leverage
    )


def market_sell_symbol_open_short(
    client: Any,
    symbol: str,
    quantity: float,
    *,
    leverage: int,
) -> Dict[str, Any]:
    """숏 진입용 편의 래퍼(SELL 시장가)."""
    return futures_market_open_position(
        client, symbol, entry_side="SELL", quantity=quantity, leverage=leverage
    )


def close_all_usdm_positions(client: Any) -> List[Tuple[str, bool, str]]:
    """열린 모든 USDT-M 포지션을 시장가로 줄인다. 심볼·성공·메시지 목록."""
    results: List[Tuple[str, bool, str]] = []
    seen: set[str] = set()
    for row in futures_open_position_rows(client):
        sym = str(row.get("symbol", "")).upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        ok, msg = market_close_symbol(client, sym)
        results.append((sym, ok, msg))
    return results
