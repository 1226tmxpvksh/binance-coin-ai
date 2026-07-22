"""BTC ATR EMA200 실전 매매 프로그램 진입점."""

from __future__ import annotations

import logging
import sys

from config import settings
from database.trade_db import TradeDatabase
from exchange.binance_futures import BinanceFuturesClient
from execution.engine import LiveTradingEngine
from monitor.safety import SafetyManager
from monitor.state_store import StateStore
from notification.kakao_notifier import KakaoNotifier, SilentNotifier
from scheduler import Scheduler


def setup_logging() -> None:
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    for filename in ["system.log", "strategy.log", "trade.log", "error.log"]:
        handler = logging.FileHandler(settings.LOG_DIR / filename, encoding="utf-8")
        handler.setFormatter(formatter)
        if filename == "error.log":
            handler.setLevel(logging.ERROR)
        else:
            handler.setLevel(level)
        root.addHandler(handler)


def build_notifier():
    if not settings.KAKAO_ENABLED:
        return SilentNotifier()
    if not settings.KAKAO_ACCESS_TOKEN and not settings.KAKAO_REFRESH_TOKEN:
        logging.getLogger(__name__).warning("카카오 토큰이 없어 알림을 비활성화합니다.")
        return SilentNotifier()
    return KakaoNotifier(
        access_token=settings.KAKAO_ACCESS_TOKEN,
        enabled=True,
        rest_api_key=settings.KAKAO_REST_API_KEY,
        refresh_token=settings.KAKAO_REFRESH_TOKEN,
        env_path=settings.ENV_FILE_PATH,
    )


def initialize_engine() -> LiveTradingEngine:
    settings.validate_settings()
    exchange = BinanceFuturesClient(
        api_key=settings.API_KEY,
        api_secret=settings.API_SECRET,
        symbol=settings.SYMBOL,
        dry_run=settings.DRY_RUN,
        paper_balance=settings.PAPER_BALANCE_USDT,
        retry_count=settings.API_RETRY_COUNT,
        retry_sleep_seconds=settings.API_RETRY_SLEEP_SECONDS,
    )
    return LiveTradingEngine(
        exchange=exchange,
        state_store=StateStore(settings.STATE_PATH),
        safety=SafetyManager(
            daily_max_loss_percent=settings.DAILY_MAX_LOSS_PERCENT,
            max_trades_per_day=settings.MAX_TRADES_PER_DAY,
            max_consecutive_losses=settings.MAX_CONSECUTIVE_LOSSES,
            cooldown_hours=settings.COOLDOWN_HOURS_AFTER_LOSS_STREAK,
        ),
        database=TradeDatabase(settings.DB_PATH),
        notifier=build_notifier(),
        config=settings,
    )


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("BTC ATR EMA200 LIVE TRADING START")
    logger.info("DRY_RUN=%s SYMBOL=%s INTERVAL=%s", settings.DRY_RUN, settings.SYMBOL, settings.INTERVAL)
    logger.info("=" * 60)

    engine = initialize_engine()
    engine.start()
    Scheduler(engine).run_forever()


if __name__ == "__main__":
    print("=" * 60)
    print("BTC ATR EMA200 LIVE TRADING")
    print(f"DRY_RUN: {settings.DRY_RUN}")
    if settings.DRY_RUN:
        print("테스트 모드입니다. 실제 주문은 실행되지 않습니다.")
    else:
        print("실전 모드입니다. 실제 주문이 실행됩니다.")
        print("계속하려면 .env에 LIVE_TRADING_CONFIRMED=YES가 필요합니다.")
    print("=" * 60)
    main()

