#!/usr/bin/env python3
"""
비상용: Binance USDT-M 선물 계정의 **모든** 미결 포지션을 시장가(reduceOnly)로 청산한다.

사용 전 `btc_live_trading\\.env`에 `BINANCE_API_KEY` / `BINANCE_API_SECRET`이 있어야 한다.

  py -3 scripts\\emergency_exit.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bootstrap_path() -> None:
    ai = ROOT / "ai_trading"
    live = ROOT / "btc_live_trading"
    for p in (str(ROOT), str(ai), str(live)):
        if p not in sys.path:
            sys.path.insert(0, p)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("emergency_exit")
    _bootstrap_path()
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.error("python-dotenv가 필요합니다: pip install python-dotenv")
        return 1
    env_path = ROOT / "btc_live_trading" / ".env"
    if not env_path.exists():
        logger.error(".env 없음: %s", env_path)
        return 1
    load_dotenv(env_path)

    try:
        from binance_futures_tools import close_all_usdm_positions, futures_client_from_env
    except ImportError as exc:
        logger.error("binance_futures_tools 로드 실패: %s", exc)
        return 1

    client = futures_client_from_env()
    if client is None:
        logger.error("Binance 클라이언트 생성 실패(API 키 확인)")
        return 1

    results = close_all_usdm_positions(client)
    if not results:
        logger.info("청산할 열린 포지션이 없습니다.")
        return 0
    for sym, ok, msg in results:
        if ok:
            logger.info("청산 완료 %s (%s)", sym, msg)
        else:
            logger.error("청산 실패 %s (%s)", sym, msg)
    return 0 if all(r[1] for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
