"""
TRADING_MODE=scalping 일 때 사용하는 설정.
"""

import os
from dotenv import load_dotenv

load_dotenv()

try:
    from kakao_utils import hydrate_tokens_from_json

    hydrate_tokens_from_json()
except Exception:
    pass

API_KEY = os.getenv("BINANCE_API_KEY", os.getenv("API_KEY", ""))
API_SECRET = os.getenv("BINANCE_API_SECRET", os.getenv("API_SECRET", ""))

if not API_KEY or not API_SECRET:
    raise ValueError("BINANCE_API_KEY / BINANCE_API_SECRET을 .env에 설정하세요.")

if len(API_KEY) < 16 or len(API_SECRET) < 16:
    raise ValueError("API 키 길이가 비정상입니다.")

SCALPING_SYMBOLS = os.getenv("SCALPING_SYMBOLS", "BTCUSDT,ETHUSDT").replace(
    " ", ""
).split(",")
SCALPING_SYMBOLS = [s for s in SCALPING_SYMBOLS if s]

TIMEFRAME = os.getenv("SCALPING_TIMEFRAME", "5m")
LEVERAGE = int(os.getenv("SCALPING_LEVERAGE", "3"))
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))

RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
BB_PERIOD = int(os.getenv("BB_PERIOD", "20"))
BB_STD = float(os.getenv("BB_STD", "2.0"))

STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "5.0"))
TP_PROFIT_KRW = float(os.getenv("TP_PROFIT_KRW", "10000"))
MONTHLY_TARGET_KRW = float(os.getenv("MONTHLY_TARGET_KRW", "100000"))
KRW_PER_USDT = float(os.getenv("KRW_PER_USDT", "1350"))

RISK_PER_TRADE = float(os.getenv("SCALPING_RISK_PER_TRADE", "0.01"))
MIN_POSITION_SIZE_USDT = float(os.getenv("MIN_POSITION_SIZE_USDT", "20"))
MIN_TRADING_BALANCE_USDT = float(os.getenv("SCALPING_MIN_BALANCE_USDT", "50"))

DAILY_MAX_LOSS_PERCENT = float(os.getenv("SCALPING_DAILY_MAX_LOSS_PCT", "10"))
EMERGENCY_STOP_LOSS_PERCENT = float(os.getenv("SCALPING_EMERGENCY_PCT", "15"))
MAX_TRADES_PER_DAY = int(os.getenv("SCALPING_MAX_TRADES_PER_DAY", "30"))

USE_AI_CONFIRM = os.getenv("USE_AI_CONFIRM", "true").lower() == "true"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

REQUIRE_USER_CONFIRM = os.getenv("REQUIRE_USER_CONFIRM", "false").lower() == "true"
CONFIRM_TIMEOUT_SECONDS = int(os.getenv("CONFIRM_TIMEOUT_SECONDS", "60"))

DRY_RUN = os.getenv("SCALPING_DRY_RUN", os.getenv("DRY_RUN", "true")).lower() == "true"
LOG_LEVEL = os.getenv("SCALPING_LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("SCALPING_LOG_FILE", "scalping_live.log")

KAKAO_ENABLED = os.getenv("SCALPING_KAKAO_ENABLED", "true").lower() == "true"
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
KAKAO_ACCESS_TOKEN = os.getenv("KAKAO_ACCESS_TOKEN", "")

if KAKAO_ENABLED and not KAKAO_ACCESS_TOKEN:
    KAKAO_ENABLED = False

SCALPING_LIVE_CONFIRMED_ENV = "SCALPING_LIVE_CONFIRMED"
SCALPING_LIVE_CONFIRMED_VALUE = "YES"

if DAILY_MAX_LOSS_PERCENT >= EMERGENCY_STOP_LOSS_PERCENT:
    raise ValueError("일일 최대 손실%%는 긴급 정지%%보다 작아야 합니다.")

if KRW_PER_USDT <= 0:
    raise ValueError("KRW_PER_USDT는 0보다 커야 합니다.")
