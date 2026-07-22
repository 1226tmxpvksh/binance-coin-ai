"""실전 매매 설정.

기본값은 안전을 위해 DRY_RUN=True 입니다.
실전 전환 시 .env에서 DRY_RUN=false와 LIVE_TRADING_CONFIRMED=YES를 함께 설정해야 합니다.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

try:
    import yaml
except ImportError:  # PyYAML 설치 전에도 .env 기반 PAPER 모드는 실행 가능해야 합니다.
    yaml = None

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_FILE_PATH = BASE_DIR / ".env"
YAML_SETTINGS_PATH = BASE_DIR / "config" / "settings.yaml"
load_dotenv(ENV_FILE_PATH)


def _load_yaml_settings() -> dict:
    if not YAML_SETTINGS_PATH.exists():
        return {}
    if yaml is None:
        raise RuntimeError("config/settings.yaml을 사용하려면 PyYAML을 설치해야 합니다.")
    with YAML_SETTINGS_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _yaml_get(settings: dict, path: str, default):
    current = settings
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


YAML_SETTINGS = _load_yaml_settings()

API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

MODE = str(_yaml_get(YAML_SETTINGS, "mode", os.getenv("MODE", "PAPER"))).upper()

KAKAO_ENABLED = str(_yaml_get(YAML_SETTINGS, "kakao.enabled", os.getenv("KAKAO_ENABLED", "true"))).lower() == "true"
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
KAKAO_ACCESS_TOKEN = os.getenv("KAKAO_ACCESS_TOKEN", "")
KAKAO_REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN", "")

SYMBOL = str(_yaml_get(YAML_SETTINGS, "exchange.symbol", "BTCUSDT"))
INTERVAL = str(_yaml_get(YAML_SETTINGS, "exchange.timeframe", "4h"))
LEVERAGE = int(_yaml_get(YAML_SETTINGS, "exchange.leverage", os.getenv("LEVERAGE", "1")))
MAX_LEVERAGE = 3

EMA_FILTER_PERIOD = int(_yaml_get(YAML_SETTINGS, "strategy.ema_period", 200))
ATR_PERIOD = int(_yaml_get(YAML_SETTINGS, "strategy.atr_period", 14))
ATR_ENTRY_MULTIPLIER = float(_yaml_get(YAML_SETTINGS, "strategy.atr_multiplier", 1.0))
ATR_STOP_MULTIPLIER = float(_yaml_get(YAML_SETTINGS, "strategy.atr_stop", 2.0))
PIVOT_LENGTH = int(_yaml_get(YAML_SETTINGS, "strategy.pivot_length", 5))

RISK_PER_TRADE = float(_yaml_get(YAML_SETTINGS, "risk.risk_per_trade", os.getenv("RISK_PER_TRADE", "0.01")))
PAPER_BALANCE_USDT = float(os.getenv("PAPER_BALANCE_USDT", "10000"))
MIN_TRADING_BALANCE_USDT = float(os.getenv("MIN_TRADING_BALANCE_USDT", "100"))
MIN_POSITION_VALUE_USDT = float(os.getenv("MIN_POSITION_VALUE_USDT", "20"))

DAILY_MAX_LOSS_PERCENT = float(
    _yaml_get(YAML_SETTINGS, "risk.daily_loss_limit", float(os.getenv("DAILY_MAX_LOSS_PERCENT", "3")) / 100)
) * 100
EMERGENCY_STOP_LOSS_PERCENT = float(os.getenv("EMERGENCY_STOP_LOSS_PERCENT", "10"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "1"))
MAX_CONSECUTIVE_LOSSES = int(_yaml_get(YAML_SETTINGS, "risk.max_consecutive_losses", os.getenv("MAX_CONSECUTIVE_LOSSES", "5")))
COOLDOWN_HOURS_AFTER_LOSS_STREAK = int(os.getenv("COOLDOWN_HOURS_AFTER_LOSS_STREAK", "24"))

ENTRY_STOP_LIMIT_BUFFER_RATIO = float(os.getenv("ENTRY_STOP_LIMIT_BUFFER_RATIO", "0.0002"))
ENTRY_ORDER_EXPIRY_HOURS = float(os.getenv("ENTRY_ORDER_EXPIRY_HOURS", "4"))
LOOKBACK_BARS = int(os.getenv("LOOKBACK_BARS", "260"))
API_RETRY_COUNT = int(os.getenv("API_RETRY_COUNT", "3"))
API_RETRY_SLEEP_SECONDS = float(os.getenv("API_RETRY_SLEEP_SECONDS", "1.0"))
MAX_CANDLE_GAP_MULTIPLIER = float(os.getenv("MAX_CANDLE_GAP_MULTIPLIER", "1.2"))

DRY_RUN = MODE != "LIVE" or os.getenv("DRY_RUN", "true").lower() != "false"
LIVE_TRADING_CONFIRMED = os.getenv("LIVE_TRADING_CONFIRMED", "")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"
DB_PATH = BASE_DIR / "database" / "live_trading.sqlite3"
STATE_PATH = STATE_DIR / "position_state.json"


def validate_settings() -> None:
    if MODE not in {"PAPER", "LIVE"}:
        raise ValueError("MODE는 PAPER 또는 LIVE만 허용합니다.")
    if LEVERAGE < 1 or LEVERAGE > MAX_LEVERAGE:
        raise ValueError(f"레버리지는 1~{MAX_LEVERAGE} 사이여야 합니다: {LEVERAGE}")
    if not 0 < RISK_PER_TRADE <= 0.03:
        raise ValueError("실전 리스크는 0% 초과 3% 이하만 허용합니다.")
    if DAILY_MAX_LOSS_PERCENT >= EMERGENCY_STOP_LOSS_PERCENT:
        raise ValueError("일일 최대 손실은 긴급 정지 손실보다 작아야 합니다.")
    if MODE == "LIVE" and not DRY_RUN:
        if LIVE_TRADING_CONFIRMED != "YES":
            raise ValueError("실전 실행은 LIVE_TRADING_CONFIRMED=YES가 필요합니다.")
        if len(API_KEY) < 16 or len(API_SECRET) < 16:
            raise ValueError("실전 실행에는 BINANCE_API_KEY/BINANCE_API_SECRET이 필요합니다.")

