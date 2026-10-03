#!/usr/bin/env python3
"""선물 포지션이 없을 때만 TRADING_STRATEGY=buy_hold 로 바꾼다.

주문과 청산은 하지 않는다.
"""

from __future__ import annotations

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
    from buy_hold import replace_trading_strategy_line, switch_block_reason

    env_path = ROOT / "btc_live_trading" / ".env"
    load_dotenv(env_path)
    client = futures_client_from_env()
    if client is None:
        print("선물 포지션 조회에 실패했습니다. 전환하지 않습니다.")
        return 2
    try:
        rows = client.futures_position_information()
    except Exception:
        print("선물 포지션 조회에 실패했습니다. 전환하지 않습니다.")
        return 2
    open_rows = []
    for row in rows or []:
        try:
            amt = float(row.get("positionAmt", 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if abs(amt) >= 1e-12:
            open_rows.append(row)
    blocked = switch_block_reason(check_ok=True, open_rows=open_rows)
    if blocked:
        print(blocked)
        return 2
    ok, message = replace_trading_strategy_line("buy_hold", path=env_path)
    if not ok:
        print(message)
        return 2
    print(f"TRADING_STRATEGY=buy_hold ({message}). 현물 매수는 하지 않았습니다.")
    print("첫 매수는 scripts/buy_hold_enter.py 를 직접 실행할 때 나갑니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
