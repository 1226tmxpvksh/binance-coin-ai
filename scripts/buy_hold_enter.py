#!/usr/bin/env python3
"""현물 USDT로 BTCUSDT를 한 번 시장가 매수하고 data/buy_hold_entry.json 에 기록한다.

선물 포지션이 있거나 기록이 이미 있으면 주문하지 않는다.
선물 지갑 이체와 선물 청산은 하지 않는다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    for folder in (ROOT, ROOT / "ai_trading", ROOT / "btc_live_trading"):
        text = str(folder)
        if text not in sys.path:
            sys.path.insert(0, text)


def main() -> int:
    _bootstrap()
    from dotenv import load_dotenv

    from binance_futures_tools import futures_client_from_env
    from binance_spot_tools import SpotBuyError, spot_client_from_env, spot_free_balance, spot_market_buy_quote
    from buy_hold import (
        ENTRY_PATH,
        allocation_pct,
        base_asset,
        build_entry_record,
        enter_block_reason,
        load_entry,
        quote_to_spend,
        write_entry,
    )

    env_path = ROOT / "btc_live_trading" / ".env"
    load_dotenv(env_path)
    futures = futures_client_from_env()
    if futures is None:
        print("선물 포지션 조회에 실패했습니다. 매수하지 않습니다.")
        return 2
    try:
        rows = futures.futures_position_information()
    except Exception:
        print("선물 포지션 조회에 실패했습니다. 매수하지 않습니다.")
        return 2
    open_rows = []
    for row in rows or []:
        try:
            amt = float(row.get("positionAmt", 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if abs(amt) >= 1e-12:
            open_rows.append(row)
    blocked = enter_block_reason(
        check_ok=True,
        open_rows=open_rows,
        entry_exists=load_entry(ENTRY_PATH) is not None,
    )
    if blocked:
        print(blocked)
        return 2

    symbol = (os.getenv("AI_SYMBOL") or "BTCUSDT").upper()
    try:
        asset = base_asset(symbol)
    except ValueError as exc:
        print(str(exc))
        return 2
    del asset
    spot = spot_client_from_env()
    if spot is None:
        print("현물 클라이언트를 만들지 못했습니다. 매수하지 않습니다.")
        return 2
    free = spot_free_balance(spot, "USDT")
    if free is None:
        print("현물 USDT 잔고를 읽지 못했습니다. 매수하지 않습니다.")
        return 2
    pct = allocation_pct()
    quote = quote_to_spend(free, pct)
    if quote <= 0:
        print("현물 USDT 가용 잔고가 없습니다. 선물 지갑은 이체하지 않습니다.")
        return 2
    try:
        fill = spot_market_buy_quote(spot, symbol, quote)
    except SpotBuyError as exc:
        print(str(exc))
        return 2
    record = build_entry_record(fill=fill, allocation=pct)
    print(
        f"현물 매수 체결: qty={record['qty_btc']} price={record['price']} "
        f"quote={record['quote_usdt']} order_id={record['order_id']}"
    )
    write_entry(record, ENTRY_PATH)
    print(f"기록 파일: {ENTRY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
