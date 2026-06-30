from __future__ import annotations

import csv
import importlib
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import requests

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
LIVE_DIR = ROOT_DIR / "btc_live_trading"
BACKTEST_DIR = ROOT_DIR / "btc_day_strategy"
DATA_DIR = BASE_DIR / "data"
LEARNING_LOG_PATH = DATA_DIR / "ai_learning_logs.csv"
TRADE_LOG_PATH = DATA_DIR / "virtual_trades.jsonl"


def _bootstrap_sys_path() -> None:
    """ai_trading 외부 모듈 import를 위해 경로를 자동 보정."""
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    scan_roots = [BASE_DIR, ROOT_DIR, Path.cwd()]
    for base in scan_roots:
        cur = base
        for _ in range(4):
            candidate = cur / "btc_live_trading"
            if candidate.exists():
                if str(cur) not in sys.path:
                    sys.path.insert(0, str(cur))
                if str(candidate) not in sys.path:
                    sys.path.insert(0, str(candidate))
                day_candidate = cur / "btc_day_strategy"
                if day_candidate.exists() and str(day_candidate) not in sys.path:
                    sys.path.insert(0, str(day_candidate))
                return
            if cur.parent == cur:
                break
            cur = cur.parent


_bootstrap_sys_path()

from ai_logic.decision_engine import analyze_trade_failure, get_ai_decision, get_market_monitor_summary
from data.backtest_data import find_similar_backtest_cases, load_backtest_summary
from data.market_data import fetch_market_snapshot
from reporting import (
    build_monthly_pnl_display,
    days_left_in_month_kst,
    load_trading_stats,
    prune_stats_history,
    resolve_report_balances,
    save_trading_stats,
    summarize_ledger,
)
from risk_guard import assess_trade_risk, ensure_min_stop_gap

try:
    from binance_futures_tools import (
        fetch_futures_usdt_balance_from_env as _fetch_futures_usdt_balance_from_env,
        futures_client_from_env as _futures_client_from_env,
        futures_market_open_position as _futures_market_open_position,
        futures_open_position_rows as _futures_open_position_rows,
        market_close_symbol as _market_close_symbol,
        signed_position_amt_for_symbol as _signed_position_amt_for_symbol,
    )
except ImportError:
    _fetch_futures_usdt_balance_from_env = None  # type: ignore[assignment]
    _futures_client_from_env = None  # type: ignore[assignment]
    _futures_market_open_position = None  # type: ignore[assignment]
    _futures_open_position_rows = None  # type: ignore[assignment]
    _market_close_symbol = None  # type: ignore[assignment]
    _signed_position_amt_for_symbol = None  # type: ignore[assignment]

try:
    from btc_live_trading.fx_rates import fetch_usdt_krw as _fetch_usdt_krw
except ImportError:
    _fetch_usdt_krw = None  # type: ignore[assignment]

try:
    from btc_live_trading.strategy.risk_manager import calculate_position_size
    _POSITION_SIZE_IMPORT_ERROR = None
except Exception as exc:
    calculate_position_size = None  # type: ignore[assignment]
    _POSITION_SIZE_IMPORT_ERROR = exc

try:
    from btc_live_trading.kakao_notifier import KakaoNotifier
    from btc_live_trading.kakao_utils import (
        LOCALHOST_REDIRECT_URI,
        apply_token_response as _apply_kakao_token_response,
        capture_authorization_code_via_localhost as _capture_kakao_auth_localhost,
        clear_kakao_auth_code as _clear_kakao_auth_code,
        exchange_authorization_code as _exchange_kakao_authorization_code,
        get_access_token,
        get_redirect_uri as _get_kakao_redirect_uri,
        get_refresh_token as _get_kakao_refresh_token,
        hydrate_tokens_from_json as _hydrate_kakao_tokens,
        kakao_api_allowed as _kakao_api_allowed,
        open_kakao_auth_url as _open_kakao_auth_url,
        refresh_kakao_access_token_sync as _refresh_kakao_access_token_sync,
        refresh_access_token_request as _refresh_kakao_access_token_request,
        validate_access_token as _validate_kakao_access_token_util,
    )
except Exception:
    KakaoNotifier = None
    _apply_kakao_token_response = None  # type: ignore
    _capture_kakao_auth_localhost = None  # type: ignore
    _clear_kakao_auth_code = None  # type: ignore
    _exchange_kakao_authorization_code = None  # type: ignore
    _get_kakao_redirect_uri = None  # type: ignore
    _get_kakao_refresh_token = None  # type: ignore
    _hydrate_kakao_tokens = None  # type: ignore
    _kakao_api_allowed = None  # type: ignore
    _open_kakao_auth_url = None  # type: ignore
    _refresh_kakao_access_token_sync = None  # type: ignore
    _refresh_kakao_access_token_request = None  # type: ignore
    _validate_kakao_access_token_util = None  # type: ignore
    get_access_token = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
SINGLE_INSTANCE_LOCK_PATH = ROOT_DIR / ".coinbot.lock"
_SINGLE_INSTANCE_LOCK_HANDLE: Any | None = None
ENV_LINE_MAP: Dict[str, int] = {}
KAKAO_AUTH_LINK_LOGGED = False
KAKAO_MANUAL_RECOVERY_ATTEMPTED = False
KAKAO_AUTH_EXHAUSTED = False
KAKAO_ENV_CODE_TRIED = False
KAKAO_LOCAL_OAUTH_TRIED = False
_KAKAO_ENV_WARNED = False
STARTUP_REPORT_SENT = False
STARTUP_HEALTH_CHECK_DONE = False
STARTUP_HEALTH_CHECK_OK = False
# 재진입 허용: run_cycle이 락을 잡은 상태에서 Ctrl+C(SIGINT) 시 같은 스레드가 시그널 핸들러로
# 다시 락을 요청하면 threading.Lock은 자기 자신에게 데드락이 난다(RLock 필요).
_SHUTDOWN_LOCK = threading.RLock()
_CYCLE_LOCK = threading.Lock()
_GRACEFUL_SHUTDOWN_ONCE = threading.Event()
_TRADING_LOOP_INITIALIZED = False
_ORDER_EXECUTION_SETUP_LOGGED = False
_LAST_CYCLE_COMPLETED_MONO: float = 0.0
_LAST_EVALUATED_CANDLE_KEY: str = ""
_ORPHAN_POSITION_ALERT_SENT = False
_SIGNAL_HANDLERS_INSTALLED = False
_LEARNING_ROWS_CACHE_MTIME: float | None = None
_LEARNING_ROWS_CACHE_ROWS: List[Dict[str, str]] = []

LEARNING_FIELDNAMES = [
    "logged_at_kst",
    "trade_id",
    "symbol",
    "interval",
    "side",
    "entry_at_kst",
    "exit_at_kst",
    "entry_price",
    "exit_price",
    "price_move_pct",
    "pnl_usdt",
    "pnl_krw",
    "market_signature",
    "market_context",
    "failure_reason",
    "reflection_summary",
    "warning",
]


def _acquire_single_instance_lock() -> None:
    """프로세스 단일 실행 보장(Single Instance Lock).

    Linux(Vultr): fcntl.flock 비차단 독점 락.
    Windows(로컬): msvcrt.locking 비차단 바이트 락.
    락 획득 실패 시 카카오·매매 로직 진입 전 즉시 종료한다.
    """
    global _SINGLE_INSTANCE_LOCK_HANDLE

    lock_path = SINGLE_INSTANCE_LOCK_PATH
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+", encoding="utf-8")
    except OSError as exc:
        logger.error("Single Instance Lock 파일을 열 수 없습니다(%s): %s", lock_path, exc)
        sys.exit(1)

    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        logger.error("이미 다른 봇 인스턴스가 실행 중입니다. 다중 실행을 차단하고 종료합니다.")
        sys.exit(1)

    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    _SINGLE_INSTANCE_LOCK_HANDLE = handle


def _load_env() -> None:
    target_env = LIVE_DIR / ".env"
    if not target_env.exists():
        logger.error(".env not found at expected path: %s", target_env)
        return
    _index_env_lines(target_env)
    _diagnose_env_section_4(target_env)
    _load_env_file_safely(target_env)


def _check_project_connectivity() -> bool:
    """운영 시작 시 핵심 프로젝트 폴더의 모듈 로드 가능 여부를 확인."""
    global STARTUP_HEALTH_CHECK_DONE, STARTUP_HEALTH_CHECK_OK
    if STARTUP_HEALTH_CHECK_DONE:
        return STARTUP_HEALTH_CHECK_OK

    checks = [
        (
            "btc_live_trading",
            "연결 확인: btc_live_trading 모듈 로드 완료",
            [
                "btc_live_trading.kakao_notifier",
                "btc_live_trading.kakao_utils",
                "btc_live_trading.strategy.risk_manager",
            ],
        ),
        (
            "btc_day_strategy",
            "연결 확인: btc_day_strategy 전략 로드 완료",
            [
                "btc_day_strategy.strategy_entry",
                "btc_day_strategy.risk_manager",
                "btc_day_strategy.backtest_engine",
            ],
        ),
    ]

    all_ok = True
    rows: List[Tuple[str, str, str]] = []
    for group_name, success_message, module_names in checks:
        try:
            for module_name in module_names:
                importlib.import_module(module_name)
            logger.info(success_message)
            rows.append((group_name, "OK", f"{len(module_names)} modules loaded"))
        except Exception as exc:
            all_ok = False
            rows.append((group_name, "FAIL", f"{type(exc).__name__}: {exc}"))
            logger.error(
                "연결 확인 실패: %s 필수 모듈 로드 실패 (%s: %s)",
                group_name,
                type(exc).__name__,
                exc,
            )

    _print_connectivity_table(rows)
    STARTUP_HEALTH_CHECK_DONE = True
    STARTUP_HEALTH_CHECK_OK = all_ok
    return all_ok


def _print_connectivity_table(rows: List[Tuple[str, str, str]]) -> None:
    if not rows:
        return
    print("\n[Project Connectivity Health Check]")
    print("+------------------+--------+--------------------------+")
    print("| Folder           | Status | Detail                   |")
    print("+------------------+--------+--------------------------+")
    for folder, status, detail in rows:
        safe_detail = detail if len(detail) <= 24 else detail[:21] + "..."
        print(f"| {folder:<16} | {status:<6} | {safe_detail:<24} |")
    print("+------------------+--------+--------------------------+\n")


def _index_env_lines(env_path: Path) -> None:
    ENV_LINE_MAP.clear()
    key_pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    with open(env_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            m = key_pattern.match(line)
            if m:
                ENV_LINE_MAP[m.group(1)] = i


def _load_env_file_safely(env_path: Path) -> None:
    with open(env_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                logger.error("Malformed .env line %d: missing '='", i)
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if not key:
                logger.error("Malformed .env line %d: empty key", i)
                continue
            os.environ[key] = value


def _diagnose_env_section_4(env_path: Path) -> None:
    in_section_4 = False
    with open(env_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            stripped = line.strip()
            if stripped.startswith("# 4."):
                in_section_4 = True
                continue
            if in_section_4 and stripped.startswith("# ") and re.match(r"#\s*\d+\.", stripped):
                in_section_4 = False
            if not in_section_4:
                continue
            if "\ufffd" in line:
                logger.error("Encoding issue near section 4 at line %d: replacement character detected", i)


def _sanitize_env_value(name: str, value: str) -> str:
    raw = value or ""
    cleaned = "".join(ch for ch in raw if ch.isascii() and (ch.isprintable() or ch in "\t ")).strip()
    if cleaned != raw.strip():
        line = ENV_LINE_MAP.get(name, -1)
        line_info = f" (line {line})" if line > 0 else ""
        logger.warning(
            "Sanitized non-ASCII/invisible chars from %s%s. Keep .env values ASCII-only.",
            name,
            line_info,
        )
    return cleaned


def _env_str(name: str, default: str) -> str:
    return _sanitize_env_value(name, os.getenv(name, default))


def _env_float(name: str, default: float) -> float:
    raw = _sanitize_env_value(name, os.getenv(name, str(default)))
    try:
        return float(raw)
    except ValueError:
        line = ENV_LINE_MAP.get(name, -1)
        line_info = f" at line {line}" if line > 0 else ""
        logger.error("Invalid float for %s%s: %r. Using default=%s", name, line_info, raw, default)
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = _sanitize_env_value(name, os.getenv(name, str(default)))
    try:
        return int(raw)
    except ValueError:
        line = ENV_LINE_MAP.get(name, -1)
        line_info = f" at line {line}" if line > 0 else ""
        logger.error("Invalid int for %s%s: %r. Using default=%s", name, line_info, raw, default)
        return int(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = _sanitize_env_value(name, os.getenv(name, str(default).lower())).lower()
    return raw in {"1", "true", "yes", "on"}


def _krw_per_usdt() -> float:
    if _fetch_usdt_krw is not None:
        try:
            return float(_fetch_usdt_krw())
        except Exception as exc:
            logger.warning("USDT/KRW 자동 조회 실패(%s), 1380 폴백", type(exc).__name__)
    return 1380.0


def _fetch_binance_futures_usdt_balance() -> float | None:
    """USDT-M 선물 지갑 잔고. `binance_futures_tools.fetch_futures_usdt_balance_from_env` 단일 경로."""
    if _fetch_futures_usdt_balance_from_env is not None:
        return _fetch_futures_usdt_balance_from_env()
    logger.warning("binance_futures_tools 미로드 또는 잔고 조회 불가")
    return None


def _report_bracket_title(dry_run: bool) -> str:
    return "[가상 매매 리포트]" if dry_run else "[실전 매매 리포트]"


def _cycle_interval_seconds() -> int:
    return max(30, _env_int("AI_LOOP_SECONDS", 300))


def _wait_for_next_cycle_slot(loop_seconds: int) -> bool:
    """이전 사이클 종료 후 loop_seconds 미만이면 대기. True면 이번 턴에서 사이클 실행 가능."""
    global _LAST_CYCLE_COMPLETED_MONO
    if _LAST_CYCLE_COMPLETED_MONO <= 0:
        return True
    elapsed = time.monotonic() - _LAST_CYCLE_COMPLETED_MONO
    if elapsed >= loop_seconds:
        return True
    remaining = loop_seconds - elapsed
    logger.debug("루프 간격 미달(%.1fs 남음) — 중복 사이클 대기", remaining)
    time.sleep(remaining)
    return True


def _mark_cycle_completed() -> None:
    global _LAST_CYCLE_COMPLETED_MONO
    _LAST_CYCLE_COMPLETED_MONO = time.monotonic()


def _candle_evaluation_key(snapshot: Dict[str, Any], symbol: str, interval: str) -> str:
    open_ms = int(_safe_float(snapshot.get("candle_open_time_ms", 0)))
    if open_ms > 0:
        return f"{symbol}:{interval}:{open_ms}"
    return f"{symbol}:{interval}:{int(_safe_float(snapshot.get('price', 0)))}"


def _should_skip_duplicate_candle_eval(snapshot: Dict[str, Any], symbol: str, interval: str) -> bool:
    """동일 15m 캔들에 대한 OpenAI 재평가 방지."""
    global _LAST_EVALUATED_CANDLE_KEY
    key = _candle_evaluation_key(snapshot, symbol, interval)
    if key and key == _LAST_EVALUATED_CANDLE_KEY:
        return True
    _LAST_EVALUATED_CANDLE_KEY = key
    return False


def _load_backtest_context(
    snapshot: Dict[str, Any],
    *,
    top_n: int = 5,
) -> Tuple[Any, List[Dict[str, float]], float]:
    """백테스트 요약·유사 케이스를 1회 스캔으로 로드(히스토리 파일 중복 순회 방지)."""
    backtest_summary = load_backtest_summary(str(BACKTEST_DIR))
    similar_cases = find_similar_backtest_cases(
        current_rsi=float(snapshot["rsi"]),
        current_atr_pct=float(snapshot["atr_pct"]),
        current_ema_gap_pct=float(snapshot["ema_gap_pct"]),
        backtest_dir=str(BACKTEST_DIR),
        top_n=top_n,
    )
    similar_avg_pnl = sum(x["pnl"] for x in similar_cases) / len(similar_cases) if similar_cases else 0.0
    return backtest_summary, similar_cases, similar_avg_pnl


def _log_order_execution_setup(dry_run: bool) -> None:
    """`AI_DRY_RUN` 환경값과 실제 주문 함수 바인딩 여부를 함께 로깅한다."""
    global _ORDER_EXECUTION_SETUP_LOGGED
    if _ORDER_EXECUTION_SETUP_LOGGED:
        return
    raw = os.getenv("AI_DRY_RUN", "")
    path = "PAPER_LEDGER_ONLY" if dry_run else "BINANCE_FUTURES_MARKET"
    api_ready = (
        not dry_run
        and _futures_market_open_position is not None
        and _futures_client_from_env is not None
        and _market_close_symbol is not None
    )
    logger.info(
        "[주문실행기] AI_DRY_RUN(raw)=%r → resolved_paper_mode=%s | 경로=%s | 선물주문스택준비=%s",
        raw,
        dry_run,
        path,
        api_ready,
    )
    if not dry_run and not api_ready:
        logger.error(
            "[주문실행기] 실전으로 표시됐으나 Binance 선물 진입/청산 모듈을 쓸 수 없습니다. "
            "의존 패키지·API 키·import 경로를 확인하세요."
        )
    _ORDER_EXECUTION_SETUP_LOGGED = True


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _format_price(value: Any) -> str:
    return f"{_safe_float(value):,.2f} USDT"


def _format_krw(value: Any) -> str:
    return f"{_safe_float(value):,.0f}원"


def _is_korean_text(text: str) -> bool:
    return any("\uac00" <= ch <= "\ud7a3" for ch in text)


def _report_text(text: Any, fallback: str = "기록 없음") -> str:
    cleaned = str(text or "").replace("\n", " ").strip()
    if not cleaned:
        return fallback
    if _is_korean_text(cleaned):
        return cleaned
    return "기존 영문 기록은 한국어 리포트 적용 전 데이터라 다음 학습부터 한국어로 갱신됩니다."


def _trend_aligned(side: str, snapshot: Dict[str, Any]) -> bool:
    ema20 = _safe_float(snapshot.get("ema20", 0.0))
    ema60 = _safe_float(snapshot.get("ema60", 0.0))
    rsi = _safe_float(snapshot.get("rsi", 50.0), 50.0)
    bb = _safe_float(snapshot.get("bb_position", 0.5), 0.5)
    direction = (side or "").upper()
    if direction == "SELL":
        return ema20 < ema60 and rsi <= 50.0 and bb <= 0.55
    return ema20 > ema60 and rsi >= 50.0 and bb >= 0.45


def _technical_signal_decision(snapshot: Dict[str, Any], sample_size: int, similar_avg_pnl: float) -> Dict[str, Any]:
    rsi = _safe_float(snapshot.get("rsi", 50.0), 50.0)
    bb = _safe_float(snapshot.get("bb_position", 0.5), 0.5)
    ema_gap_pct = _safe_float(snapshot.get("ema_gap_pct", 0.0))
    atr_pct = _safe_float(snapshot.get("atr_pct", 0.0))
    ema20 = _safe_float(snapshot.get("ema20", 0.0))
    ema60 = _safe_float(snapshot.get("ema60", 0.0))

    buy_score = 0.0
    sell_score = 0.0
    buy_reasons: List[str] = []
    sell_reasons: List[str] = []

    if ema20 > ema60 and ema_gap_pct >= 0.03:
        buy_score += 0.34
        buy_reasons.append(f"EMA20이 EMA60 위({ema_gap_pct:+.2f}%)")
    elif ema20 < ema60 and ema_gap_pct <= -0.03:
        sell_score += 0.34
        sell_reasons.append(f"EMA20이 EMA60 아래({ema_gap_pct:+.2f}%)")

    if 52.0 <= rsi <= 74.0:
        buy_score += 0.26
        buy_reasons.append(f"RSI {rsi:.1f}로 상승 모멘텀")
    elif 26.0 <= rsi <= 48.0:
        sell_score += 0.26
        sell_reasons.append(f"RSI {rsi:.1f}로 하락 모멘텀")
    elif rsi > 74.0 and ema_gap_pct > 0:
        buy_score += 0.16
        buy_reasons.append(f"RSI {rsi:.1f} 과열이나 상승 추세 지속")
    elif rsi < 26.0 and ema_gap_pct < 0:
        sell_score += 0.16
        sell_reasons.append(f"RSI {rsi:.1f} 과매도이나 하락 추세 지속")

    if 0.58 <= bb <= 1.08:
        buy_score += 0.22
        buy_reasons.append(f"볼린저 위치 {bb:.2f}로 상단 돌파권")
    elif -0.08 <= bb <= 0.42:
        sell_score += 0.22
        sell_reasons.append(f"볼린저 위치 {bb:.2f}로 하단 이탈권")

    if atr_pct >= _env_float("AI_ATR_MIN_ENTRY_PCT", 0.08):
        buy_score += 0.08
        sell_score += 0.08
    if sample_size == 0 and max(buy_score, sell_score) >= 0.56:
        buy_score += 0.06
        sell_score += 0.06
    if similar_avg_pnl > 0:
        buy_score += 0.04
        sell_score += 0.04
    elif similar_avg_pnl < -0.05:
        buy_score -= 0.06
        sell_score -= 0.06

    side = "BUY" if buy_score >= sell_score else "SELL"
    score = max(buy_score, sell_score)
    reasons = buy_reasons if side == "BUY" else sell_reasons
    threshold = _env_float("AI_TECHNICAL_ENTRY_THRESHOLD", 0.60)
    if score < threshold or abs(buy_score - sell_score) < 0.12:
        return {
            "decision": "HOLD",
            "confidence": max(0.0, min(1.0, score)),
            "reason": "기술 지표 방향성이 아직 충분히 한쪽으로 모이지 않음",
            "score": score,
        }
    confidence = max(0.0, min(1.0, 0.52 + score * 0.42))
    return {
        "decision": side,
        "confidence": confidence,
        "reason": " / ".join(reasons[:3]) or "기술 지표 정렬",
        "score": score,
    }


def _ai_entry_gate(snapshot: Dict[str, Any]) -> Tuple[bool, str]:
    rsi = _safe_float(snapshot.get("rsi", 50.0), 50.0)
    bb = _safe_float(snapshot.get("bb_position", 0.5), 0.5)
    ema_gap_pct = _safe_float(snapshot.get("ema_gap_pct", 0.0))

    rsi_low = _env_float("AI_GATE_RSI_LOW", 35.0)
    rsi_high = _env_float("AI_GATE_RSI_HIGH", 65.0)
    bb_low = _env_float("AI_GATE_BB_LOW", 0.20)
    bb_high = _env_float("AI_GATE_BB_HIGH", 0.80)
    ema_gap_min = _env_float("AI_GATE_EMA_GAP_MIN_PCT", 0.03)

    reasons: List[str] = []
    if rsi <= rsi_low:
        reasons.append(f"RSI 과매도({rsi:.1f}<={rsi_low:.1f})")
    elif rsi >= rsi_high:
        reasons.append(f"RSI 과매수({rsi:.1f}>={rsi_high:.1f})")
    if bb <= bb_low:
        reasons.append(f"BB 하단권({bb:.2f}<={bb_low:.2f})")
    elif bb >= bb_high:
        reasons.append(f"BB 상단권({bb:.2f}>={bb_high:.2f})")
    if abs(ema_gap_pct) >= ema_gap_min:
        reasons.append(f"EMA 갭({ema_gap_pct:+.2f}%)")

    if reasons:
        return True, " / ".join(reasons)
    return False, "중립 구간(RSI/EMA/BB)으로 AI 호출 생략"


def _blend_ai_and_technical_decision(
    ai_decision: Dict[str, Any],
    technical_decision: Dict[str, Any],
) -> Dict[str, Any]:
    ai_side = str(ai_decision.get("decision", "HOLD")).upper()
    tech_side = str(technical_decision.get("decision", "HOLD")).upper()
    ai_conf = _safe_float(ai_decision.get("confidence", 0.0))
    tech_conf = _safe_float(technical_decision.get("confidence", 0.0))
    entry_threshold = _env_float("AI_AGGRESSIVE_CONFIDENCE_THRESHOLD", 0.62)

    if tech_side in {"BUY", "SELL"} and tech_conf >= entry_threshold:
        if ai_side == tech_side:
            return {
                "decision": tech_side,
                "confidence": min(1.0, max(ai_conf, tech_conf) + 0.08),
                "reason": f"{_report_text(ai_decision.get('reason'))} + 기술 지표 확인: {technical_decision['reason']}",
            }
        if ai_side == "HOLD" or ai_conf < 0.72:
            return {
                "decision": tech_side,
                "confidence": tech_conf,
                "reason": f"백테스트 근거가 부족해도 기술 지표가 명확함: {technical_decision['reason']}",
            }
    if ai_side in {"BUY", "SELL"} and ai_conf >= entry_threshold:
        return {
            "decision": ai_side,
            "confidence": ai_conf,
            "reason": _report_text(ai_decision.get("reason")),
        }
    return {
        "decision": "HOLD",
        "confidence": max(ai_conf, tech_conf),
        "reason": _report_text(ai_decision.get("reason"), technical_decision.get("reason", "대기")),
    }


def _atr_min_entry_pct() -> float:
    """가격 대비 ATR(%) 최소값. 이보다 낮으면 횡보장으로 진입 차단."""
    return _env_float("AI_ATR_MIN_ENTRY_PCT", 0.08)


def _apply_atr_entry_filter(decision: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    side = str(decision.get("decision", "HOLD")).upper()
    if side not in {"BUY", "SELL"}:
        return decision
    atr_pct = _safe_float(snapshot.get("atr_pct", 0.0))
    min_pct = _atr_min_entry_pct()
    if atr_pct >= min_pct:
        return decision
    out = dict(decision)
    base_reason = _report_text(decision.get("reason"), "")
    out["decision"] = "HOLD"
    out["confidence"] = min(_safe_float(out.get("confidence", 0.0)), 0.35)
    out["reason"] = (
        f"변동성 부족(Choppy Market, ATR {atr_pct:.3f}% < {min_pct:.3f}%)"
        + (f" — {base_reason}" if base_reason and base_reason != "기록 없음" else "")
    )
    return out


def _volume_min_ratio() -> float:
    return _env_float("AI_VOLUME_MIN_RATIO", 1.5)


def _apply_volume_entry_filter(decision: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    if not _env_bool("AI_VOLUME_GATE_ENABLED", True):
        return decision
    side = str(decision.get("decision", "HOLD")).upper()
    if side not in {"BUY", "SELL"}:
        return decision
    if bool(snapshot.get("volume_surge")):
        return decision
    ratio_pct = _safe_float(snapshot.get("volume_ratio_pct", 0.0))
    min_ratio = _volume_min_ratio()
    out = dict(decision)
    base_reason = _report_text(decision.get("reason"), "")
    out["decision"] = "HOLD"
    out["confidence"] = min(_safe_float(out.get("confidence", 0.0)), 0.35)
    out["reason"] = (
        f"거래량 미동반(Volume {ratio_pct:.0f}% of 7d avg, need ≥{min_ratio * 100:.0f}%)"
        + (f" — {base_reason}" if base_reason and base_reason != "기록 없음" else "")
    )
    return out


def _build_stop_loss(snapshot: Dict[str, float], decision: str) -> float:
    price = float(snapshot["price"])
    atr = float(snapshot["atr14"])
    atr_multiplier = _env_float("AI_ATR_STOP_MULTIPLIER", 1.2)
    if decision == "BUY":
        return price - (atr * atr_multiplier)
    if decision == "SELL":
        return price + (atr * atr_multiplier)
    return price


def _build_take_profit(snapshot: Dict[str, float], decision: str) -> float:
    price = float(snapshot["price"])
    atr = float(snapshot["atr14"])
    sl_mult = _env_float("AI_ATR_STOP_MULTIPLIER", 1.2)
    tp_mult = _env_float("AI_ATR_TAKE_PROFIT_MULTIPLIER", 2.25)
    min_rr = _env_float("AI_MIN_RR_RATIO", 1.75)
    min_profit_ratio = _env_float("AI_MIN_TAKE_PROFIT_RATIO", 0.012)
    if _trend_aligned(decision, snapshot):
        tp_mult += _env_float("AI_ATR_TREND_TP_BONUS", 0.25)
    stop_distance = atr * sl_mult
    tp_distance = max(atr * tp_mult, price * min_profit_ratio)
    if stop_distance > 0 and min_rr > 0:
        tp_distance = max(tp_distance, stop_distance * min_rr)
    if decision == "SELL":
        return price - tp_distance
    if decision == "BUY":
        return price + tp_distance
    return price


def _unrealized_move_pct(open_position: Dict[str, Any], snapshot: Dict[str, Any]) -> float:
    side = str(open_position.get("side", "BUY")).upper()
    entry = _safe_float(open_position.get("entry_price", 0.0))
    price = _safe_float(snapshot.get("price", 0.0))
    if entry <= 0:
        return 0.0
    if side == "SELL":
        return (entry - price) / entry * 100.0
    return (price - entry) / entry * 100.0


def _extend_winning_position(
    stats: Dict[str, Any],
    open_position: Dict[str, Any],
    snapshot: Dict[str, Any],
    now_kst: datetime,
    hold_minutes: int,
    reason: str,
) -> bool:
    side = str(open_position.get("side", "BUY")).upper()
    if not _trend_aligned(side, snapshot):
        return False
    extensions = _safe_int(open_position.get("hold_extensions", 0))
    max_extensions = _env_int("AI_MAX_HOLD_EXTENSIONS", 4)
    if extensions >= max_extensions:
        return False

    price = _safe_float(snapshot.get("price", 0.0))
    atr = _safe_float(snapshot.get("atr14", 0.0))
    if price <= 0 or atr <= 0:
        return False
    trail_multiplier = _env_float("AI_TRAILING_STOP_ATR_MULTIPLIER", 1.25)
    extend_multiplier = _env_float("AI_TAKE_PROFIT_EXTEND_ATR_MULTIPLIER", 1.6)
    min_extend_ratio = _env_float("AI_MIN_TAKE_PROFIT_RATIO", 0.012) * 0.8

    if side == "SELL":
        new_stop = min(_safe_float(open_position.get("stop_loss", price)), price + atr * trail_multiplier)
        new_take_profit = price - max(atr * extend_multiplier, price * min_extend_ratio)
    else:
        new_stop = max(_safe_float(open_position.get("stop_loss", price)), price - atr * trail_multiplier)
        new_take_profit = price + max(atr * extend_multiplier, price * min_extend_ratio)

    open_position["stop_loss"] = float(new_stop)
    open_position["take_profit"] = float(new_take_profit)
    open_position["expires_at_kst"] = (now_kst + timedelta(minutes=hold_minutes)).isoformat()
    open_position["hold_extensions"] = extensions + 1
    open_position["last_extension_reason"] = reason
    stats["open_position"] = open_position
    return True


def _position_exit_reason(
    stats: Dict[str, Any],
    open_position: Dict[str, Any],
    snapshot: Dict[str, Any],
    now_kst: datetime,
    hold_minutes: int,
) -> str | None:
    side = str(open_position.get("side", "BUY")).upper()
    price = _safe_float(snapshot.get("price", 0.0))
    stop_loss = _safe_float(open_position.get("stop_loss", 0.0))
    take_profit = _safe_float(open_position.get("take_profit", 0.0))
    move_pct = _unrealized_move_pct(open_position, snapshot)

    stop_hit = (side == "SELL" and price >= stop_loss > 0) or (side != "SELL" and 0 < price <= stop_loss)
    if stop_hit:
        return "stop_loss"

    tp_hit = (side == "SELL" and 0 < price <= take_profit) or (side != "SELL" and price >= take_profit > 0)
    if tp_hit:
        if move_pct > 0 and _extend_winning_position(stats, open_position, snapshot, now_kst, hold_minutes, "익절선 도달 후 추세 지속"):
            return None
        return "take_profit"

    scratch_min = _env_int("AI_EARLY_SCRATCH_MINUTES", 45)
    flat_pct = _env_float("AI_SCRATCH_FLAT_MOVE_PCT", 0.12)
    if scratch_min > 0 and flat_pct >= 0.0:
        mins_open = _minutes_since_position_open(open_position, now_kst)
        if mins_open >= scratch_min and abs(move_pct) < flat_pct:
            return "scratch_flat_exit"

    if _position_due(open_position, now_kst):
        if move_pct > 0 and _extend_winning_position(stats, open_position, snapshot, now_kst, hold_minutes, "시간 만기 후 추세 지속"):
            return None
        return "time_exit"
    return None


def _kakao_auth_url(rest_api_key: str) -> str:
    redirect_uri = _get_kakao_redirect_uri() if _get_kakao_redirect_uri is not None else os.getenv("KAKAO_REDIRECT_URI", "")
    if not rest_api_key or not redirect_uri:
        return ""
    encoded_redirect_uri = quote(redirect_uri, safe="")
    return (
        "https://kauth.kakao.com/oauth/authorize?"
        f"client_id={rest_api_key}&redirect_uri={encoded_redirect_uri}&response_type=code"
    )


def _validate_kakao_redirect_uri_config() -> bool:
    env_redirect_uri = _env_str("KAKAO_REDIRECT_URI", "")
    helper_redirect_uri = _get_kakao_redirect_uri() if _get_kakao_redirect_uri is not None else env_redirect_uri
    if not env_redirect_uri:
        logger.error("KAKAO_REDIRECT_URI가 비어 있습니다. 카카오 개발자 콘솔 Redirect URI와 동일하게 설정하세요.")
        return False
    if env_redirect_uri in {"your_redirect_uri_here"}:
        logger.error("KAKAO_REDIRECT_URI가 placeholder입니다. 카카오 개발자 콘솔 등록값으로 변경하세요.")
        return False
    if env_redirect_uri in {"https://example.com/oauth", "http://127.0.0.1:8765/oauth"}:
        logger.info(
            "KAKAO_REDIRECT_URI=%s — 카카오 개발자 콘솔 Redirect URI와 동일한지 확인하세요.",
            env_redirect_uri,
        )
    if not env_redirect_uri.startswith(("http://", "https://")):
        logger.error("KAKAO_REDIRECT_URI 형식이 올바르지 않습니다: %s", env_redirect_uri)
        return False
    if helper_redirect_uri != env_redirect_uri:
        logger.error(
            "KAKAO_REDIRECT_URI 불일치: env=%s, kakao_utils=%s. KOE205 방지를 위해 한 값으로 통일하세요.",
            env_redirect_uri,
            helper_redirect_uri,
        )
        return False
    return True


def _log_kakao_manual_auth_link(reason: str) -> None:
    global KAKAO_AUTH_LINK_LOGGED
    if KAKAO_AUTH_LINK_LOGGED:
        return
    rest_api_key = _env_str("KAKAO_REST_API_KEY", "")
    redirect_ok = _validate_kakao_redirect_uri_config()
    auth_url = _kakao_auth_url(rest_api_key)
    logger.error("=" * 80)
    logger.error("카카오 자동 토큰 갱신 실패: %s", reason)
    logger.error("운영 조치: 리프레시 토큰까지 만료된 상태이므로 새 인가 코드 발급이 필요합니다.")
    if auth_url:
        logger.error("1) 아래 URL을 브라우저에서 열고 카카오 로그인을 완료하세요.")
        logger.error("%s", auth_url)
        logger.error("2) 리다이렉트된 URL의 code= 뒤 값을 복사해 토큰 재발급 절차에 입력하세요.")
        logger.error("3) KOE205/KOE006: KAKAO_REDIRECT_URI와 카카오 개발자 콘솔 Redirect URI가 정확히 같은지 확인하세요.")
        redirect_uri = _get_kakao_redirect_uri() if _get_kakao_redirect_uri is not None else _env_str("KAKAO_REDIRECT_URI", "")
        if redirect_uri:
            logger.error("   등록해야 할 Redirect URI: %s", redirect_uri)
    else:
        logger.error("KAKAO_REST_API_KEY 또는 KAKAO_REDIRECT_URI가 없어 인가 URL을 만들 수 없습니다.")
    if not redirect_ok:
        logger.error("현재 redirect_uri 설정 검증에 실패했습니다. URI를 먼저 수정한 뒤 새 인가 코드를 발급하세요.")
    logger.error("=" * 80)
    logger.error(
        "systemd/Vultr 등 비대화형 환경에서는 journalctl에 코드를 붙여넣을 수 없습니다."
    )
    logger.error("대신 아래 한 줄로 URL + 인증 입력 + 로그 팔로우가 한 번에 됩니다:")
    logger.error("  bash ~/Coin/scripts/coinbot_watch.sh")
    logger.error("또는 수동 절차:")
    logger.error("  sudo systemctl stop coinbot.service")
    logger.error("  cd ~/Coin && source ~/venv/bin/activate && python ai_trading/main_ai.py")
    logger.error("  (터미널에 뜨는 URL로 인증 → 코드 입력 → 완료 후 Ctrl+C)")
    logger.error("  sudo systemctl start coinbot.service")
    logger.error("또는 .env에 KAKAO_AUTH_CODE=<인가코드> 를 1회 설정 후 restart (성공 시 자동 저장됨).")
    KAKAO_AUTH_LINK_LOGGED = True


def _mark_kakao_auth_exhausted(reason: str) -> None:
    """리프레시 만료 등 재인증 전까지 카카오 API 재시도·로그 스팸 방지."""
    global KAKAO_AUTH_EXHAUSTED
    if KAKAO_AUTH_EXHAUSTED:
        return
    KAKAO_AUTH_EXHAUSTED = True
    _log_kakao_manual_auth_link(reason)


def _clear_kakao_auth_exhausted() -> None:
    global KAKAO_AUTH_EXHAUSTED, KAKAO_AUTH_LINK_LOGGED, KAKAO_MANUAL_RECOVERY_ATTEMPTED
    global KAKAO_ENV_CODE_TRIED, KAKAO_LOCAL_OAUTH_TRIED
    KAKAO_AUTH_EXHAUSTED = False
    KAKAO_AUTH_LINK_LOGGED = False
    KAKAO_MANUAL_RECOVERY_ATTEMPTED = False
    KAKAO_ENV_CODE_TRIED = False
    KAKAO_LOCAL_OAUTH_TRIED = False


def _kakao_redirect_supports_localhost(redirect_uri: str) -> bool:
    uri = (redirect_uri or "").strip().lower()
    return "127.0.0.1" in uri or "localhost" in uri


def _try_kakao_local_oauth_auto(rest_api_key: str) -> str:
    """Windows 등 대화형 환경: localhost 콜백으로 브라우저 OAuth 자동 완료."""
    global KAKAO_LOCAL_OAUTH_TRIED
    if (
        KAKAO_LOCAL_OAUTH_TRIED
        or KAKAO_AUTH_EXHAUSTED
        or _capture_kakao_auth_localhost is None
        or not rest_api_key
        or not _env_bool("AI_AUTO_KAKAO_OAUTH", True)
    ):
        return ""
    if not _stdin_is_interactive():
        return ""
    redirect_uri = _get_kakao_redirect_uri() if _get_kakao_redirect_uri is not None else _env_str("KAKAO_REDIRECT_URI", "")
    if not _kakao_redirect_supports_localhost(redirect_uri):
        return ""
    KAKAO_LOCAL_OAUTH_TRIED = True
    logger.info("카카오 localhost OAuth 자동 인증 시도 (브라우저가 열립니다)...")
    try:
        code = _capture_kakao_auth_localhost(rest_api_key, redirect_uri=redirect_uri, open_browser=True)
    except Exception as exc:
        logger.error("카카오 자동 OAuth 실패: %s", exc)
        return ""
    if not code:
        return ""
    return _exchange_kakao_auth_code(rest_api_key, redirect_uri, code)


def _stdin_is_interactive() -> bool:
    if _env_bool("AI_FORCE_INTERACTIVE_KAKAO", False):
        return True
    if _env_bool("AI_NON_INTERACTIVE", False):
        return False
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _exchange_kakao_auth_code(rest_api_key: str, redirect_uri: str, auth_code: str) -> str:
    if (
        _exchange_kakao_authorization_code is None
        or _apply_kakao_token_response is None
        or not rest_api_key
        or not auth_code
    ):
        return ""
    try:
        token_data = _exchange_kakao_authorization_code(rest_api_key, redirect_uri, auth_code)
        access_token = _apply_kakao_token_response(token_data)
        if access_token:
            _clear_kakao_auth_exhausted()
            if _clear_kakao_auth_code is not None:
                _clear_kakao_auth_code()
            logger.info("카카오 토큰 복구 완료 (.env / kakao_code.json 저장됨)")
            return access_token
        logger.error("카카오 토큰 응답에 access_token이 없습니다.")
        return ""
    except Exception as exc:
        error_code, error_description = _extract_kakao_error(exc)
        if error_code == "KOE205" or "KOE205" in error_description:
            logger.error("카카오 인증 실패(KOE205): KAKAO_REDIRECT_URI와 카카오 개발자 콘솔 Redirect URI가 다릅니다.")
        elif error_code == "invalid_grant" and "authorization code" in error_description.lower():
            logger.error(
                "카카오 인가 코드 만료/이미 사용됨 — 1회용 코드입니다. "
                "인증 URL에서 새 코드를 발급받아 다시 입력하세요."
            )
            if _clear_kakao_auth_code is not None:
                _clear_kakao_auth_code()
        elif error_code == "KOE006" or "KOE006" in error_description:
            redirect_uri = _get_kakao_redirect_uri() if _get_kakao_redirect_uri else _env_str("KAKAO_REDIRECT_URI", "")
            logger.error(
                "카카오 KOE006: Redirect URI 미등록. developers.kakao.com 콘솔 → coin 앱 → "
                "앱 > 플랫폼 키 > REST API 키 > 리다이렉트 URI 에 아래 값을 추가하세요: %s",
                redirect_uri or "(비어 있음)",
            )
        elif error_code:
            logger.error("카카오 인증 실패: %s (%s)", error_code, error_description or type(exc).__name__)
        else:
            logger.error("카카오 인증 실패: %s", type(exc).__name__)
        return ""


def _extract_kakao_error(exc: Exception) -> Tuple[str, str]:
    response = getattr(exc, "response", None)
    if response is None:
        return "", ""
    try:
        error_payload = response.json()
        return (
            str(error_payload.get("error") or "").strip(),
            str(error_payload.get("error_description") or "").strip(),
        )
    except Exception:
        return "", str(getattr(response, "text", "") or "").strip()


def _manual_kakao_authorization_recovery(rest_api_key: str, reason: str) -> str:
    global KAKAO_MANUAL_RECOVERY_ATTEMPTED
    if KAKAO_MANUAL_RECOVERY_ATTEMPTED:
        return ""
    KAKAO_MANUAL_RECOVERY_ATTEMPTED = True

    if (
        _exchange_kakao_authorization_code is None
        or _apply_kakao_token_response is None
        or not rest_api_key
    ):
        logger.error("카카오 수동 인증을 실행할 수 없습니다. KAKAO_REST_API_KEY와 kakao_utils import 상태를 확인하세요.")
        return ""

    redirect_ok = _validate_kakao_redirect_uri_config()
    redirect_uri = _get_kakao_redirect_uri() if _get_kakao_redirect_uri is not None else os.getenv("KAKAO_REDIRECT_URI", "")
    auth_url = _kakao_auth_url(rest_api_key)

    env_code = _env_str("KAKAO_AUTH_CODE", "").strip()
    if env_code:
        return _try_kakao_auth_code_from_env(rest_api_key)

    _log_kakao_manual_auth_link(reason)

    if not _stdin_is_interactive():
        logger.warning("비대화형 실행(systemd): input() 대기 없이 매매 루프를 계속합니다 (카카오 알림만 비활성).")
        return ""

    print("\n" + "=" * 80)
    print("리프레시 토큰이 만료되었습니다. 아래 URL에서 새 인가 코드를 발급받아 입력해주세요.")
    print("=" * 80)
    print(f"사유: {reason}")
    print(f"현재 KAKAO_REDIRECT_URI: {redirect_uri or '(비어 있음)'}")
    if redirect_ok:
        print("KOE205 사전 점검: redirect_uri 형식과 로컬 설정 일치 여부 확인 완료")
    else:
        print("경고: KAKAO_REDIRECT_URI 설정을 먼저 확인하세요. 카카오 개발자 콘솔 등록값과 다르면 KOE205가 발생합니다.")
    if not auth_url:
        print("\n인가 URL을 생성할 수 없습니다. KAKAO_REST_API_KEY 또는 KAKAO_REDIRECT_URI를 확인하세요.")
        print("=" * 80)
        return ""
    print("\n인가 URL:")
    print(auth_url)
    print("\n브라우저 인증 후 리다이렉트 URL의 code= 뒤 값을 붙여넣으세요.")
    print("입력하지 않고 Enter를 누르면 인증을 건너뜁니다 (Safety First 게이트가 매매를 차단합니다).")
    print("서버(systemd)에서는 서비스를 멈추고 'python ai_trading/main_ai.py'를 직접 실행해 대화형 인증을 진행하세요.")

    try:
        auth_code = input("인가 코드(code): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n카카오 수동 인증 입력이 취소되었습니다. 카카오 알림 없이 루프를 계속합니다.")
        return ""

    if not auth_code:
        print("인가 코드가 입력되지 않았습니다. 카카오 알림 없이 루프를 계속합니다.")
        return ""

    return _exchange_kakao_auth_code(rest_api_key, redirect_uri, auth_code)


def _extract_kakao_code(raw: str) -> str:
    """인가코드만 넣든, 전체 리다이렉트 URL을 넣든 code= 값만 추출."""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return ""
    if "code=" in raw:
        from urllib.parse import urlparse, parse_qs

        query = urlparse(raw).query or raw
        parsed = parse_qs(query)
        if parsed.get("code"):
            return str(parsed["code"][0]).strip()
        return raw.split("code=", 1)[1].split("&", 1)[0].strip()
    return raw


def _try_kakao_auth_code_from_env(rest_api_key: str) -> str:
    global KAKAO_ENV_CODE_TRIED
    if KAKAO_AUTH_EXHAUSTED:
        return ""
    code = _extract_kakao_code(_env_str("KAKAO_AUTH_CODE", ""))
    if not code:
        return ""
    if KAKAO_ENV_CODE_TRIED:
        return ""
    KAKAO_ENV_CODE_TRIED = True
    redirect_uri = _get_kakao_redirect_uri() if _get_kakao_redirect_uri is not None else os.getenv("KAKAO_REDIRECT_URI", "")
    logger.info("KAKAO_AUTH_CODE 환경변수로 자동 토큰 교환 시도")
    return _exchange_kakao_auth_code(rest_api_key, redirect_uri, code)


def _kakao_alerts_enabled() -> bool:
    """KAKAO_ALERTS_ENABLED=false 면 재인증 전까지 카카오 관련 호출·로그를 모두 끔.

    환경 화이트리스트(hostname=example1, project=/home/bot2/Coin) 불일치 시
    .env 설정과 무관하게 항상 False — 유령 봇·WSL·백업 폴더 차단.
    """
    global _KAKAO_ENV_WARNED
    if _kakao_api_allowed is not None and not _kakao_api_allowed():
        if not _KAKAO_ENV_WARNED:
            logger.warning(
                "카카오 환경 화이트리스트 불일치 — 카카오 전 기능 비활성. "
                "hostname=example1, project=/home/bot2/Coin 에서만 허용. "
                "인증: bash ~/Coin/scripts/coinbot_watch.sh"
            )
            _KAKAO_ENV_WARNED = True
        return False
    if _kakao_api_allowed is None and os.name == "nt":
        if not _KAKAO_ENV_WARNED:
            logger.warning(
                "Windows 로컬 PC — 카카오 전 기능 비활성. "
                "인증·알림은 Vultr에서 bash ~/Coin/scripts/coinbot_watch.sh 로만 하세요."
            )
            _KAKAO_ENV_WARNED = True
        return False
    return _env_bool("KAKAO_ALERTS_ENABLED", True)


def _interactive_kakao_auth_until_done(rest_api_key: str) -> str:
    """대화형 가동 로직(No Auth, No Start + But Interactive).

    봇을 일시 중지(Pause)하고 터미널에 카카오 로그인 URL을 출력한 뒤,
    운영자가 인가 코드를 입력해 인증이 성공할 때까지 input()으로 기다린다.
    성공 시 깨끗한 새 마스터 열쇠가 원자적 저장 + 저장 후 검증(✅)을 거쳐
    kakao_code.json/.env에 안착된 뒤 access_token을 반환한다.
    'skip' 입력 시에만 인증 없이 빠져나간다(이후 Safety First 게이트가 매매를 차단).
    """
    if (
        not rest_api_key
        or _exchange_kakao_authorization_code is None
        or _apply_kakao_token_response is None
    ):
        return ""
    redirect_uri = _get_kakao_redirect_uri() if _get_kakao_redirect_uri is not None else _env_str("KAKAO_REDIRECT_URI", "")
    auth_url = _kakao_auth_url(rest_api_key)
    if not auth_url:
        logger.error("KAKAO_REST_API_KEY/KAKAO_REDIRECT_URI가 없어 인가 URL을 만들 수 없습니다.")
        return ""

    print("\n" + "=" * 72)
    print("[카카오 대화형 인증] 마스터 열쇠가 없거나 만료되어 봇을 일시 중지했습니다.")
    print("인증이 끝나야 매매 엔진이 가동됩니다. (Safety First)")
    print("-" * 72)
    print("1) 아래 URL을 브라우저에서 열고 카카오 로그인을 완료하세요:")
    print(f"   {auth_url}")
    print("2) 이동된 주소창의 전체 URL(또는 code= 뒤 값)을 아래에 붙여넣으세요.")
    print("   인증 없이 건너뛰려면 'skip' 입력 (매매는 차단된 상태로 유지됩니다)")
    print("=" * 72)
    if _open_kakao_auth_url is not None:
        _open_kakao_auth_url(auth_url)
    else:
        try:
            import webbrowser

            webbrowser.open(auth_url)
        except Exception:
            pass

    while True:
        try:
            raw = input("코드 또는 URL 붙여넣기 (skip=건너뛰기): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n카카오 인증 입력이 중단되었습니다. 매매는 차단된 상태로 유지됩니다.")
            return ""
        if raw.lower() == "skip":
            print("인증을 건너뜁니다. 매매는 Safety First 게이트에 의해 차단됩니다.")
            return ""
        code = _extract_kakao_code(raw)
        if not code:
            print("입력에서 인가 코드를 찾지 못했습니다. 전체 URL 또는 code= 뒤 값을 다시 붙여넣으세요.")
            continue
        access = _exchange_kakao_auth_code(rest_api_key, redirect_uri, code)
        if access:
            print("[OK] 카카오 인증 완료 - 새 마스터 열쇠가 저장.검증되었습니다. 매매 엔진을 가동합니다.")
            return access
        print("토큰 발급에 실패했습니다. (인가 코드는 1회용입니다) 새 코드를 발급받아 다시 입력하세요.")
        print(f"   {auth_url}")


def _bootstrap_kakao_tokens() -> None:
    """기동 시 마스터 열쇠 확보: env코드 → 기존토큰검증 → 리프레시 → 대화형 인증(input 대기).

    대화형 환경에서는 인증이 성공할 때까지 봇을 일시 중지하고 터미널에서
    인가 코드를 직접 받는다. 인증·저장·검증이 끝나야 매매 루프로 진입한다.
    """
    if KakaoNotifier is None or _hydrate_kakao_tokens is None:
        return
    if not _kakao_alerts_enabled():
        logger.info("카카오 알림 비활성화(KAKAO_ALERTS_ENABLED=false) — 토큰 갱신/알림을 건너뜁니다.")
        return
    if KAKAO_AUTH_EXHAUSTED:
        return
    # 1) 마스터 열쇠 저장소(kakao_code.json) 읽기
    _hydrate_kakao_tokens()
    rest_api_key = _env_str("KAKAO_REST_API_KEY", "")
    if not rest_api_key:
        return

    # 2) .env의 1회용 인가 코드가 있으면 우선 사용
    if _try_kakao_auth_code_from_env(rest_api_key):
        return

    # 3) 기존 액세스 토큰이 유효하면 그대로 가동
    existing = get_access_token().strip() if get_access_token is not None else ""
    if existing and _validate_kakao_access_token(existing):
        logger.info("카카오 액세스 토큰 유효 — 재인증 불필요")
        return

    interactive = _stdin_is_interactive()

    redirect_uri = _get_kakao_redirect_uri() if _get_kakao_redirect_uri is not None else _env_str("KAKAO_REDIRECT_URI", "")
    if interactive and _kakao_redirect_supports_localhost(redirect_uri):
        if _try_kakao_local_oauth_auto(rest_api_key):
            return

    # 4) 리프레시 토큰으로 자동 갱신(회전 시 원자적 저장+검증은 kakao_utils가 보장)
    refresh_token = _get_kakao_refresh_token().strip() if _get_kakao_refresh_token else ""
    if refresh_token:
        if _refresh_kakao_access_token(refresh_token, rest_api_key, mark_exhausted_on_expire=not interactive):
            return

    # 5) 여기까지 왔다면 마스터 열쇠가 없거나 invalid_grant 상태.
    if interactive:
        # 대화형: 봇을 일시 중지하고 터미널에서 인가 코드를 직접 받아 인증을 끝낸다.
        logger.error("카카오 마스터 열쇠 만료/없음 — 봇을 일시 중지하고 대화형 인증을 시작합니다.")
        if _interactive_kakao_auth_until_done(rest_api_key):
            return
        # 운영자가 명시적으로 skip한 경우에만 여기 도달
        if not KAKAO_AUTH_EXHAUSTED:
            _mark_kakao_auth_exhausted("운영자가 대화형 인증을 건너뛰었습니다.")
        return

    # 비대화형(systemd): input() 불가 — 조치 방법을 ERROR로 남기고 게이트가 매매를 차단한다.
    if not KAKAO_AUTH_EXHAUSTED:
        _mark_kakao_auth_exhausted("리프레시 토큰이 만료되었거나 유효하지 않습니다.")


def _validate_kakao_access_token(access_token: str) -> bool:
    if not access_token:
        return False
    if KAKAO_AUTH_EXHAUSTED:
        return False
    if _validate_kakao_access_token_util is not None:
        return _validate_kakao_access_token_util(access_token)
    try:
        response = requests.get(
            "https://kapi.kakao.com/v1/user/access_token_info",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=8,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def _refresh_kakao_access_token(
    refresh_token: str,
    rest_api_key: str,
    *,
    mark_exhausted_on_expire: bool = True,
) -> str:
    if not refresh_token or not rest_api_key or _refresh_kakao_access_token_sync is None:
        return ""
    try:
        access_token = _refresh_kakao_access_token_sync(rest_api_key, refresh_token).strip()
        if access_token:
            _clear_kakao_auth_exhausted()
        return access_token
    except Exception as exc:
        error_code, error_description = _extract_kakao_error(exc)
        if error_code in {"expired_or_invalid_refresh_token", "invalid_grant"}:
            if mark_exhausted_on_expire:
                _mark_kakao_auth_exhausted("리프레시 토큰이 만료되었거나 유효하지 않습니다.")
            else:
                logger.info("카카오 리프레시 토큰 만료 — 브라우저 재인증을 진행합니다.")
        elif not KAKAO_AUTH_LINK_LOGGED:
            if error_code:
                logger.warning("카카오 리프레시 토큰 갱신 실패: %s (%s)", error_code, error_description or type(exc).__name__)
            else:
                logger.warning("카카오 리프레시 토큰 갱신 실패: %s", type(exc).__name__)
        return ""


def _ensure_kakao_access_token(*, show_auth_link: bool = True) -> str:
    if (
        KakaoNotifier is None
        or _hydrate_kakao_tokens is None
        or get_access_token is None
        or _get_kakao_refresh_token is None
    ):
        return ""

    if not _kakao_alerts_enabled():
        return ""

    if KAKAO_AUTH_EXHAUSTED:
        return ""

    _hydrate_kakao_tokens()
    rest_api_key = _env_str("KAKAO_REST_API_KEY", "")

    from_env = _try_kakao_auth_code_from_env(rest_api_key)
    if from_env:
        return from_env

    # 검증 우선: 기존 액세스 토큰이 아직 유효하면 그대로 사용한다.
    # (매 알림마다 refresh를 호출하면 마스터 열쇠 회전이 과도하게 일어나 위험하다.
    #  따라서 회전은 액세스 토큰이 실제로 만료된 6시간 주기에만 발생하도록 한다.)
    access_token = get_access_token().strip()
    if access_token and _validate_kakao_access_token(access_token):
        _clear_kakao_auth_exhausted()
        return access_token

    # 액세스 토큰이 만료/무효일 때만 refresh_token으로 갱신(여기서만 회전 발생).
    refresh_token = _get_kakao_refresh_token().strip()
    if refresh_token and rest_api_key:
        refreshed = _refresh_kakao_access_token(refresh_token, rest_api_key)
        if refreshed:
            return refreshed
        if KAKAO_AUTH_EXHAUSTED:
            if show_auth_link:
                recovered = _manual_kakao_authorization_recovery(
                    rest_api_key, "리프레시 토큰이 만료되었거나 유효하지 않습니다."
                )
                if recovered:
                    return recovered
            return ""
        # 네트워크 등 일시적 갱신 실패 — exhausted 로 표시하지 않고 다음 사이클 재시도
        logger.warning("카카오 토큰 갱신 일시 실패 — 다음 사이클에서 재시도합니다.")
        return ""

    if show_auth_link:
        recovered = _manual_kakao_authorization_recovery(
            rest_api_key, "사용 가능한 액세스 토큰/리프레시 토큰이 없습니다."
        )
        if recovered:
            return recovered
    if not refresh_token and not KAKAO_AUTH_EXHAUSTED:
        _mark_kakao_auth_exhausted("사용 가능한 액세스 토큰/리프레시 토큰이 없습니다.")
    return ""


def _notify_kakao(title: str, body: str) -> bool:
    """카카오 알림 전송. 실패해도 매매 루프에는 영향 없음(다음 사이클 재시도)."""
    try:
        if KakaoNotifier is None or _hydrate_kakao_tokens is None or get_access_token is None:
            return False
        if not _kakao_alerts_enabled():
            return False
        _hydrate_kakao_tokens()
        rest = _env_str("KAKAO_REST_API_KEY", "")
        if not rest:
            return False
        refresh = _get_kakao_refresh_token().strip() if _get_kakao_refresh_token else ""
        notifier = KakaoNotifier(
            access_token=get_access_token().strip(),
            enabled=True,
            rest_api_key=rest,
            refresh_token=refresh,
            prompt_on_refresh_failure=False,
        )
        ok = notifier.send_message(title, body)
        if not ok:
            logger.warning("카카오 알림 전송 실패(다음 사이클 재시도): %s", title[:60])
        return ok
    except Exception as exc:
        logger.error("카카오 알림 전송 중 예외 발생(매매에는 영향 없음): %s", type(exc).__name__)
        return False


def _kakao_auth_ready() -> bool:
    """Safety First 게이트: 매매를 진행해도 되는 카카오 인증 상태인지 확인한다.

    카카오 알림은 시스템 생존 신호(Heartbeat)이므로 매매 엔진의 '전제 조건'이다.
    - KAKAO_ALERTS_ENABLED=false 이면 운영자가 알림을 의도적으로 끈 것이므로 통과시킨다.
    - 그 외에는 유효한 카카오 액세스 토큰을 확보하지 못하면 False(매매 차단)를 반환한다.
    - 점검 중 예외가 나도 호출자에게 전파하지 않고 False(안전 측)로 처리한다.
    """
    if not _kakao_alerts_enabled():
        return True
    if KakaoNotifier is None or _hydrate_kakao_tokens is None or get_access_token is None:
        logger.error("카카오 모듈 로드 실패 — 인증 확인 불가로 매매를 차단합니다.")
        return False
    try:
        access = _ensure_kakao_access_token(show_auth_link=True)
    except Exception as exc:
        logger.error("카카오 인증 확인 중 예외 — 매매를 차단합니다: %s", type(exc).__name__)
        return False
    return bool(access)


def _decision_emoji(decision: str, risk_blocked: bool) -> str:
    if risk_blocked:
        return "🚫"
    if decision == "BUY":
        return "🚀"
    if decision == "HOLD":
        return "⏸️"
    return "📉"


def _console_decision_emoji(decision: str) -> str:
    d = (decision or "HOLD").upper()
    if d == "BUY":
        return "🟢"
    if d == "SELL":
        return "🔴"
    return "⚪"


def _symbol_base(symbol: str) -> str:
    s = str(symbol or "BTCUSDT").upper()
    for suffix in ("USDT", "BUSD", "USDC"):
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def _format_opened_at_kst(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return "—"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        else:
            dt = dt.astimezone(KST)
        return dt.strftime("%H:%M")
    except (TypeError, ValueError):
        return text


def _format_open_position_console(open_position: Dict[str, Any]) -> str:
    symbol = str(open_position.get("symbol", "BTCUSDT"))
    base = _symbol_base(symbol)
    size = _safe_float(open_position.get("position_size", 0.0))
    entry = _safe_float(open_position.get("entry_price", 0.0))
    opened = _format_opened_at_kst(open_position.get("opened_at_kst", ""))
    side = str(open_position.get("side", "")).upper()
    side_tag = f" [{side}]" if side else ""
    return (
        f"{size:g} {base}{side_tag} "
        f"(진입가: {entry:,.2f} / 진입시간: {opened})"
    )


def _format_cycle_dashboard(report: Dict[str, Any]) -> str:
    market = report.get("market") if isinstance(report.get("market"), dict) else {}
    symbol = str(market.get("symbol", "BTCUSDT"))
    decision = str(report.get("decision", "HOLD")).upper()
    confidence_pct = _safe_float(report.get("confidence", 0.0)) * 100.0
    reason = _report_text(report.get("reason", ""))
    tokens_saved = _safe_int(report.get("estimated_tokens_saved", 0))

    price = _safe_float(market.get("price", 0.0))
    rsi = _safe_float(market.get("rsi", 0.0))
    ema_gap = _safe_float(market.get("ema_gap_pct", 0.0))
    bb = _safe_float(market.get("bb_position", 0.5))
    atr_pct = _safe_float(market.get("atr_pct", 0.0))
    atr_min = _atr_min_entry_pct()
    atr_ok = atr_pct >= atr_min
    vol_ratio_pct = _safe_float(market.get("volume_ratio_pct", 0.0))
    vol_surge = bool(market.get("volume_surge"))
    vol_min_pct = _volume_min_ratio() * 100.0

    dry_run = bool(report.get("dry_run", True))
    balance_krw = _safe_float(report.get("paper_balance_krw", 0.0))
    pnl_disp = report.get("pnl_display") if isinstance(report.get("pnl_display"), dict) else {}
    recovering = bool(pnl_disp.get("principal_recovering"))
    if not dry_run and report.get("binance_futures_wallet_usdt") is not None:
        balance_usdt = _safe_float(report.get("binance_futures_wallet_usdt"))
        balance_label = "실잔고(USDT)"
    else:
        balance_usdt = _safe_float(report.get("paper_balance_usdt", 0.0))
        balance_label = "가상 잔고(USDT)" if dry_run else "잔고(USDT)"

    open_position = report.get("open_position")
    if isinstance(open_position, dict) and open_position:
        position_line = _format_open_position_console(open_position)
    else:
        position_line = "없음"

    decision_emoji = _console_decision_emoji(decision)
    lines = [
        "",
        "=" * 50,
        "[AI 매매 판단]",
        f"- 최종 결정: {decision_emoji} {decision} (신뢰도: {confidence_pct:.0f}%)",
        f"- 판단 사유: {reason}",
        f"- AI 절감 토큰: {tokens_saved:,}",
        "",
        f"[시장 지표 ({symbol})]",
        f"- 현재 가격: {price:,.2f}",
        f"- 핵심 지표: RSI {rsi:.1f} / EMA 갭 {ema_gap:+.2f}% / 볼린저 위치 {bb:.2f}",
        f"- 변동성(ATR): {atr_pct:.3f}% {'✓ 추세 가능' if atr_ok else f'✗ 횡보 (<{atr_min:.3f}%)'}",
        f"- 거래량: {vol_ratio_pct:.0f}% of 7d avg {'✓ 급증' if vol_surge else f'✗ 미달 (<{vol_min_pct:.0f}%)'}",
        "",
        "[현재 포지션 및 잔고]",
        f"- {balance_label}: {balance_usdt:,.2f}" + (f" (≈ {balance_krw:,.0f}원)" if balance_krw > 0 else ""),
        f"- 보유 포지션: {position_line}",
    ]
    if recovering:
        shortfall = _safe_float(pnl_disp.get("shortfall_krw", 0.0))
        lines.append(f"- 원금 상태: 📌 복구 중 ({shortfall:,.0f}원 부족 · 실질 월손익 0원 표기)")
    lines.append("=" * 50)
    return "\n".join(lines)


def _record_daily_decision_stats(
    stats: Dict[str, Any],
    *,
    decision: str,
    risk_allowed: bool,
    now_kst: datetime,
) -> None:
    today = now_kst.strftime("%Y-%m-%d")
    if str(stats.get("daily_decision_date", "")) != today:
        stats["daily_decision_date"] = today
        stats["daily_decision_count"] = 0
        stats["daily_entry_count"] = 0
    stats["daily_decision_count"] = _safe_int(stats.get("daily_decision_count", 0)) + 1
    if str(decision).upper() in {"BUY", "SELL"} and risk_allowed:
        stats["daily_entry_count"] = _safe_int(stats.get("daily_entry_count", 0)) + 1


def _today_decision_counters(stats: Dict[str, Any] | None = None) -> Tuple[int, int]:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if stats is None:
        stats = load_trading_stats()
    if str(stats.get("daily_decision_date", "")) != today:
        return 0, 0
    return _safe_int(stats.get("daily_decision_count", 0)), _safe_int(stats.get("daily_entry_count", 0))


def _data_path(name: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / name


def _initial_balances(krw_per_usdt: float) -> Tuple[float, float]:
    initial_krw = _env_float("AI_VIRTUAL_INITIAL_KRW", 500_000.0)
    default_usdt = initial_krw / krw_per_usdt if krw_per_usdt > 0 else 362.0
    initial_usdt = _env_float("AI_VIRTUAL_INITIAL_USDT", default_usdt)
    return initial_krw, initial_usdt


def _snapshot_bucket(value: float, step: float) -> str:
    if step <= 0:
        return f"{value:.2f}"
    bucket = round(value / step) * step
    return f"{bucket:.2f}"


def _market_signature(snapshot: Dict[str, Any], side: str = "") -> str:
    rsi = _safe_float(snapshot.get("rsi", 50.0))
    bb = _safe_float(snapshot.get("bb_position", 0.5))
    atr_pct = _safe_float(snapshot.get("atr_pct", 0.0))
    ema_gap_pct = _safe_float(snapshot.get("ema_gap_pct", 0.0))
    ema20 = _safe_float(snapshot.get("ema20", 0.0))
    ema60 = _safe_float(snapshot.get("ema60", 0.0))
    trend = "bull" if ema20 > ema60 else "bear" if ema20 < ema60 else "flat"
    if bb >= 0.85:
        bb_zone = "upper"
    elif bb <= 0.15:
        bb_zone = "lower"
    else:
        bb_zone = "mid"
    return (
        f"side={side or 'NA'}|trend={trend}|rsi={_snapshot_bucket(rsi, 5)}|"
        f"bb={bb_zone}|atr_pct={_snapshot_bucket(atr_pct, 0.2)}|"
        f"ema_gap_pct={_snapshot_bucket(ema_gap_pct, 0.2)}"
    )


def _learning_log_max_rows() -> int:
    raw = _sanitize_env_value("AI_LEARNING_LOG_MAX_ROWS", os.getenv("AI_LEARNING_LOG_MAX_ROWS", "5000"))
    try:
        return max(50, int(float(raw)))
    except ValueError:
        return 5000


def _invalidate_learning_rows_cache() -> None:
    global _LEARNING_ROWS_CACHE_MTIME
    _LEARNING_ROWS_CACHE_MTIME = None


def _load_learning_rows() -> List[Dict[str, str]]:
    """학습 CSV: mtime 기준 캐시 + 최대 행 수(꼬리만)로 메모리·디스크 부하 완화."""
    global _LEARNING_ROWS_CACHE_MTIME, _LEARNING_ROWS_CACHE_ROWS
    if not LEARNING_LOG_PATH.exists():
        _LEARNING_ROWS_CACHE_MTIME = None
        _LEARNING_ROWS_CACHE_ROWS = []
        return []
    try:
        mtime = LEARNING_LOG_PATH.stat().st_mtime
    except OSError:
        return []
    if mtime == _LEARNING_ROWS_CACHE_MTIME and _LEARNING_ROWS_CACHE_ROWS:
        return _LEARNING_ROWS_CACHE_ROWS
    try:
        with open(LEARNING_LOG_PATH, "r", encoding="utf-8-sig", newline="") as f:
            rows = [row for row in csv.DictReader(f) if row]
    except Exception:
        return []
    cap = _learning_log_max_rows()
    if len(rows) > cap:
        rows = rows[-cap:]
    _LEARNING_ROWS_CACHE_MTIME = mtime
    _LEARNING_ROWS_CACHE_ROWS = rows
    return rows


def _combined_learning_text(row: Dict[str, str]) -> str:
    """교훈 내용 중심 비교 텍스트. 시그니처가 달라도 같은 일반론이면 중복으로 간주."""
    return " || ".join(
        s for s in (
            str(row.get("failure_reason", "")).strip(),
            str(row.get("reflection_summary", "")).strip(),
            str(row.get("warning", "")).strip(),
        ) if s
    ).strip()


def _dedupe_learning_entry(entry: Dict[str, str]) -> Tuple[bool, float]:
    candidate = _combined_learning_text(entry)
    best_ratio = 0.0
    for row in _load_learning_rows():
        ratio = SequenceMatcher(None, candidate, _combined_learning_text(row)).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
    return best_ratio >= 0.8, best_ratio


def _append_learning_entry(entry: Dict[str, str]) -> Tuple[bool, float]:
    duplicate, best_ratio = _dedupe_learning_entry(entry)
    if duplicate:
        return False, best_ratio
    LEARNING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = LEARNING_LOG_PATH.exists()
    with open(LEARNING_LOG_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEARNING_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(entry)
    _invalidate_learning_rows_cache()
    cap = _learning_log_max_rows()
    try:
        with open(LEARNING_LOG_PATH, "r", encoding="utf-8-sig", newline="") as f:
            row_count = sum(1 for _ in csv.DictReader(f))
        if row_count > int(cap * 1.1):
            _prune_learning_log(cap)
    except OSError:
        pass
    return True, best_ratio


def _prune_learning_log(max_rows: int | None = None) -> int:
    """ai_learning_logs.csv 꼬리만 유지(오래된 행 삭제). 반환: 삭제한 행 수."""
    if not LEARNING_LOG_PATH.exists():
        return 0
    cap = max_rows if max_rows is not None else _learning_log_max_rows()
    try:
        with open(LEARNING_LOG_PATH, "r", encoding="utf-8-sig", newline="") as f:
            rows = [row for row in csv.DictReader(f) if row]
    except OSError:
        return 0
    if len(rows) <= cap:
        return 0
    keep = rows[-cap:]
    try:
        with open(LEARNING_LOG_PATH, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LEARNING_FIELDNAMES)
            writer.writeheader()
            writer.writerows(keep)
    except OSError:
        return 0
    _invalidate_learning_rows_cache()
    return len(rows) - len(keep)


def _trade_log_max_lines() -> int:
    raw = _sanitize_env_value("AI_TRADE_LOG_MAX_LINES", os.getenv("AI_TRADE_LOG_MAX_LINES", "2000"))
    try:
        return max(100, int(float(raw)))
    except ValueError:
        return 2000


def _prune_trade_log(max_lines: int | None = None) -> int:
    """virtual_trades.jsonl 꼬리만 유지(오래된 라인 삭제). 반환: 삭제한 라인 수."""
    if not TRADE_LOG_PATH.exists():
        return 0
    cap = max_lines if max_lines is not None else _trade_log_max_lines()
    try:
        with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return 0
    if len(lines) <= cap:
        return 0
    keep = lines[-cap:]
    try:
        with open(TRADE_LOG_PATH, "w", encoding="utf-8") as f:
            f.writelines(keep)
    except OSError:
        return 0
    return len(lines) - len(keep)


def _append_trade_event(event: Dict[str, Any]) -> None:
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    # 무한 증가 방지: 상한의 1.1배 초과 시에만 트림(매 append 전체 재작성 부담 완화)
    cap = _trade_log_max_lines()
    try:
        if TRADE_LOG_PATH.stat().st_size > 0:
            with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
            if line_count > int(cap * 1.1):
                _prune_trade_log(cap)
    except OSError:
        pass


def _find_closest_failure_memory(snapshot: Dict[str, Any]) -> Tuple[str, str, float]:
    rows = _load_learning_rows()
    if not rows:
        return "", "", 0.0
    current_signature = _market_signature(snapshot)
    best_row: Dict[str, str] | None = None
    best_ratio = 0.0
    for row in rows:
        compare_text = row.get("market_signature", "")
        ratio = SequenceMatcher(None, current_signature, compare_text).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_row = row
    min_sim = _env_float("AI_FAILURE_MEMORY_MIN_SIM", 0.75)
    if best_row is None or best_ratio < min_sim:
        return "", "", 0.0
    memory = (
        f"similarity={best_ratio:.2f}\n"
        f"market_signature={best_row.get('market_signature', '')}\n"
        f"failure_reason={best_row.get('failure_reason', '')}\n"
        f"warning={best_row.get('warning', '')}\n"
        f"reflection={best_row.get('reflection_summary', '')}"
    )
    return memory, best_row.get("reflection_summary", ""), best_ratio


def _top_recent_loss_reflections(limit: int) -> str:
    """pnl_usdt < 0 인 행 중 최근 N건의 반성 요약(프롬프트 상단용)."""
    if limit <= 0:
        return ""
    rows = _load_learning_rows()
    dated: List[Tuple[datetime, Dict[str, str]]] = []
    for row in rows:
        if _safe_float(row.get("pnl_usdt", 0.0)) >= 0.0:
            continue
        raw_t = str(row.get("logged_at_kst", "")).strip()
        try:
            dt = datetime.fromisoformat(raw_t)
        except ValueError:
            continue
        dated.append((dt, row))
    dated.sort(key=lambda x: x[0], reverse=True)
    lines: List[str] = []
    seen_refs: List[str] = []
    for dt, row in dated:
        ref = str(row.get("reflection_summary", "")).strip()
        if not ref:
            continue
        if any(SequenceMatcher(None, ref, prev).ratio() >= 0.7 for prev in seen_refs):
            continue
        seen_refs.append(ref)
        tid = str(row.get("trade_id", "")).strip()
        warn = str(row.get("warning", "")).strip()
        label = f"{tid} " if tid else ""
        line = f"- [{dt.isoformat()}] {label}{ref}"
        if warn:
            line += f" → 규칙: {warn}"
        lines.append(line)
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def _apply_failure_similarity_guard(
    decision: Dict[str, Any],
    similarity: float,
    snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """과거 실패와 시그니처 유사도가 높을 때 최종 결정을 보수적으로 조정."""
    if similarity <= 0.0:
        return decision
    if snapshot is not None:
        rsi = _safe_float(snapshot.get("rsi", 50.0), 50.0)
        if rsi <= 35.0 or rsi >= 65.0:
            return decision
    strong = _env_float("AI_FAILURE_SIM_STRONG", 0.92)
    moderate = _env_float("AI_FAILURE_SIM_MODERATE", 0.85)
    strong_mult = _env_float("AI_FAILURE_SIM_CONF_MULT_STRONG", 0.15)
    mod_mult = _env_float("AI_FAILURE_SIM_CONF_MULT_MODERATE", 0.45)
    cap = _env_float("AI_FAILURE_SIM_CONF_CAP", 0.22)

    out = dict(decision)
    base_reason = str(out.get("reason", ""))
    conf = _safe_float(out.get("confidence", 0.0))

    if similarity >= strong:
        out["decision"] = "HOLD"
        out["confidence"] = min(conf * strong_mult, cap)
        out["reason"] = f"[유사 과거실패 sim≥{strong:.2f}] {base_reason}"
        return out
    if similarity >= moderate:
        out["confidence"] = conf * mod_mult
        out["reason"] = f"[유사 과거실패 sim≥{moderate:.2f}] {base_reason}"
    return out


def _paper_trade_id(now_kst: datetime) -> str:
    return f"PAPER-{now_kst.strftime('%Y%m%d%H%M%S')}"


def _position_due(open_position: Dict[str, Any], now_kst: datetime) -> bool:
    expires_at_raw = str(open_position.get("expires_at_kst", "")).strip()
    if not expires_at_raw:
        return False
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except ValueError:
        return False
    return now_kst >= expires_at


def _minutes_since_position_open(open_position: Dict[str, Any], now_kst: datetime) -> float:
    raw = str(open_position.get("opened_at_kst", "")).strip()
    if not raw:
        return 0.0
    try:
        opened = datetime.fromisoformat(raw)
    except ValueError:
        return 0.0
    return max(0.0, (now_kst - opened).total_seconds() / 60.0)


def _close_position(
    *,
    stats: Dict[str, Any],
    open_position: Dict[str, Any],
    snapshot: Dict[str, Any],
    now_kst: datetime,
    krw_per_usdt: float,
    model: str,
    exit_reason: str = "time_exit",
    dry_run: bool = True,
) -> Dict[str, Any]:
    sym_ex = str(open_position.get("symbol", "")).strip()
    if (
        not dry_run
        and sym_ex
        and _futures_client_from_env is not None
        and _market_close_symbol is not None
    ):
        client = _futures_client_from_env()
        if client is not None:
            ok, msg = _market_close_symbol(client, sym_ex)
            if not ok:
                logger.error("실전 청산 실패 %s: %s", sym_ex, msg)
                _notify_kakao(
                    "🚨 실전 청산 실패 알림",
                    f"{sym_ex} 시장가 청산 실패: {msg}. 거래소 포지션·원장을 자동으로 비우지 않았습니다.",
                )
                return {
                    "close_event": None,
                    "reflection_result": {
                        "stored": False,
                        "similarity": 0.0,
                        "summary": "",
                        "warning": "",
                    },
                    "exchange_close_failed": True,
                }

    side = str(open_position.get("side", "HOLD")).upper()
    entry_price = _safe_float(open_position.get("entry_price", 0.0))
    exit_price = _safe_float(snapshot.get("price", 0.0))
    size = _safe_float(open_position.get("position_size", 0.0))
    if side == "SELL":
        pnl_usdt = (entry_price - exit_price) * size
        price_move_pct = ((entry_price - exit_price) / entry_price * 100.0) if entry_price else 0.0
    else:
        pnl_usdt = (exit_price - entry_price) * size
        price_move_pct = ((exit_price - entry_price) / entry_price * 100.0) if entry_price else 0.0
    pnl_krw = pnl_usdt * krw_per_usdt
    stats["virtual_balance_usdt"] = _safe_float(stats.get("virtual_balance_usdt", 0.0)) + pnl_usdt
    stats["virtual_balance_krw"] = _safe_float(stats.get("virtual_balance_krw", 0.0)) + pnl_krw
    stats["total_realized_pnl_usdt"] = _safe_float(stats.get("total_realized_pnl_usdt", 0.0)) + pnl_usdt
    stats["total_realized_pnl_krw"] = _safe_float(stats.get("total_realized_pnl_krw", 0.0)) + pnl_krw
    stats["monthly_realized_pnl_usdt"] = _safe_float(stats.get("monthly_realized_pnl_usdt", 0.0)) + pnl_usdt
    stats["monthly_realized_pnl_krw"] = _safe_float(stats.get("monthly_realized_pnl_krw", 0.0)) + pnl_krw
    stats["trade_count"] = _safe_int(stats.get("trade_count", 0)) + 1
    stats["monthly_trade_count"] = _safe_int(stats.get("monthly_trade_count", 0)) + 1
    if pnl_usdt >= 0:
        stats["win_count"] = _safe_int(stats.get("win_count", 0)) + 1
        stats["monthly_win_count"] = _safe_int(stats.get("monthly_win_count", 0)) + 1
    else:
        stats["loss_count"] = _safe_int(stats.get("loss_count", 0)) + 1
        stats["monthly_loss_count"] = _safe_int(stats.get("monthly_loss_count", 0)) + 1
    stats["last_trade_closed_at_kst"] = now_kst.isoformat()
    stats["cumulative_profit_krw"] = _safe_float(stats.get("total_realized_pnl_krw", 0.0))
    stats["open_position"] = None
    close_event = {
        "event": "close",
        "logged_at_kst": now_kst.isoformat(),
        "trade_id": open_position.get("trade_id", ""),
        "symbol": open_position.get("symbol", ""),
        "interval": open_position.get("interval", ""),
        "side": side,
        "entry_at_kst": open_position.get("opened_at_kst", ""),
        "exit_at_kst": now_kst.isoformat(),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "position_size": size,
        "pnl_usdt": pnl_usdt,
        "pnl_krw": pnl_krw,
        "price_move_pct": price_move_pct,
        "exit_reason": exit_reason,
    }
    _append_trade_event(close_event)
    entry_snapshot = open_position.get("entry_snapshot", {}) if isinstance(open_position.get("entry_snapshot"), dict) else {}
    reflection_result = {
        "stored": False,
        "similarity": 0.0,
        "summary": "",
    }
    if pnl_usdt < 0:
        trade_context = {
            "trade_id": open_position.get("trade_id", ""),
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "position_size": size,
            "pnl_usdt": pnl_usdt,
            "pnl_krw": pnl_krw,
            "price_move_pct": price_move_pct,
            "decision_reason": open_position.get("decision_reason", ""),
            "market_signature": _market_signature(entry_snapshot, side),
        }
        reflection = analyze_trade_failure(entry_snapshot, snapshot, trade_context, model=model)
        entry = {
            "logged_at_kst": now_kst.isoformat(),
            "trade_id": str(open_position.get("trade_id", "")),
            "symbol": str(open_position.get("symbol", "")),
            "interval": str(open_position.get("interval", "")),
            "side": side,
            "entry_at_kst": str(open_position.get("opened_at_kst", "")),
            "exit_at_kst": now_kst.isoformat(),
            "entry_price": f"{entry_price:.6f}",
            "exit_price": f"{exit_price:.6f}",
            "price_move_pct": f"{price_move_pct:.4f}",
            "pnl_usdt": f"{pnl_usdt:.6f}",
            "pnl_krw": f"{pnl_krw:.2f}",
            "market_signature": _market_signature(entry_snapshot, side),
            "market_context": reflection.get("market_context", ""),
            "failure_reason": reflection.get("failure_reason", ""),
            "reflection_summary": reflection.get("reflection_summary", ""),
            "warning": reflection.get("warning", ""),
        }
        stored, similarity = _append_learning_entry(entry)
        stats["last_reflection_summary"] = reflection.get("reflection_summary", "")
        if stored:
            stats["unique_failure_count"] = _safe_int(stats.get("unique_failure_count", 0)) + 1
        reflection_result = {
            "stored": stored,
            "similarity": similarity,
            "summary": reflection.get("reflection_summary", ""),
            "warning": reflection.get("warning", ""),
        }
    return {"close_event": close_event, "reflection_result": reflection_result}


def _live_entry_price_qty(order: Dict[str, Any], fallback_price: float, fallback_qty: float) -> Tuple[float, float]:
    entry_px = float(fallback_price)
    try:
        ap = order.get("avgPrice") or order.get("price")
        if ap is not None and str(ap).strip():
            v = float(ap)
            if v > 0:
                entry_px = v
    except (TypeError, ValueError):
        pass
    qty = float(fallback_qty)
    try:
        exq = order.get("executedQty")
        if exq is not None and str(exq).strip():
            v = float(exq)
            if v > 0:
                qty = v
    except (TypeError, ValueError):
        pass
    return entry_px, qty


def _open_position(
    *,
    stats: Dict[str, Any],
    symbol: str,
    interval: str,
    decision: Dict[str, Any],
    snapshot: Dict[str, Any],
    stop_loss: float,
    position_size: float,
    leverage: int,
    similar_avg_pnl: float,
    now_kst: datetime,
    hold_minutes: int,
    memory_summary: str,
    dry_run: bool = True,
) -> Dict[str, Any] | None:
    entry_side = str(decision["decision"]).upper()
    snap_price = float(snapshot["price"])

    if dry_run:
        trade_id = _paper_trade_id(now_kst)
        position = {
            "trade_id": trade_id,
            "order_mode": "paper",
            "symbol": symbol,
            "interval": interval,
            "side": entry_side,
            "opened_at_kst": now_kst.isoformat(),
            "expires_at_kst": (now_kst + timedelta(minutes=hold_minutes)).isoformat(),
            "entry_price": snap_price,
            "stop_loss": float(stop_loss),
            "take_profit": float(_build_take_profit(snapshot, decision["decision"])),
            "position_size": float(position_size),
            "leverage": leverage,
            "notional_usdt": snap_price * float(position_size),
            "decision_reason": str(decision["reason"]),
            "confidence": float(decision["confidence"]),
            "similar_avg_pnl": float(similar_avg_pnl),
            "failure_memory_summary": memory_summary,
            "hold_extensions": 0,
            "entry_snapshot": dict(snapshot),
        }
        stats["open_position"] = position
        _append_trade_event(
            {
                "event": "open",
                "logged_at_kst": now_kst.isoformat(),
                "trade_id": trade_id,
                "order_mode": "paper",
                "symbol": symbol,
                "interval": interval,
                "side": entry_side,
                "entry_price": snap_price,
                "stop_loss": float(stop_loss),
                "take_profit": float(position["take_profit"]),
                "position_size": float(position_size),
                "leverage": leverage,
                "reason": str(decision["reason"]),
                "confidence": float(decision["confidence"]),
                "similar_avg_pnl": float(similar_avg_pnl),
            }
        )
        return position

    if _futures_market_open_position is None or _futures_client_from_env is None:
        logger.error("실전 진입: binance_futures_tools 미로드")
        _notify_kakao(
            "🚨 실전 주문 실패 알림",
            f"{symbol}: 선물 주문 함수를 불러올 수 없습니다(import 확인).",
        )
        return None

    client = _futures_client_from_env()
    if client is None:
        logger.error("실전 진입: Binance 클라이언트 생성 실패(API 키)")
        _notify_kakao(
            "🚨 실전 주문 실패 알림",
            f"{symbol}: BINANCE_API_KEY / BINANCE_API_SECRET 을 확인하세요.",
        )
        return None

    try:
        from binance.exceptions import BinanceAPIException

        order = _futures_market_open_position(
            client,
            symbol,
            entry_side=entry_side,
            quantity=float(position_size),
            leverage=int(leverage),
        )
    except BinanceAPIException as exc:
        code = getattr(exc, "code", "")
        msg = getattr(exc, "message", str(exc))
        logger.exception("실전 진입 BinanceAPIException")
        _notify_kakao(
            "🚨 실전 주문 실패 알림",
            f"{symbol} {entry_side} 실패 code={code} msg={msg}",
        )
        return None
    except Exception as exc:
        logger.exception("실전 진입 예외")
        _notify_kakao(
            "🚨 실전 주문 실패 알림",
            f"{symbol} {entry_side} 실패: {type(exc).__name__}: {exc}",
        )
        return None

    oid = order.get("orderId")
    trade_id = str(oid) if oid is not None else ""
    entry_px, qty_eff = _live_entry_price_qty(order, snap_price, position_size)
    tp = float(_build_take_profit(snapshot, decision["decision"]))
    position = {
        "trade_id": trade_id,
        "order_mode": "live",
        "binance_order_id": trade_id,
        "symbol": symbol,
        "interval": interval,
        "side": entry_side,
        "opened_at_kst": now_kst.isoformat(),
        "expires_at_kst": (now_kst + timedelta(minutes=hold_minutes)).isoformat(),
        "entry_price": entry_px,
        "stop_loss": float(stop_loss),
        "take_profit": tp,
        "position_size": qty_eff,
        "leverage": leverage,
        "notional_usdt": entry_px * qty_eff,
        "decision_reason": str(decision["reason"]),
        "confidence": float(decision["confidence"]),
        "similar_avg_pnl": float(similar_avg_pnl),
        "failure_memory_summary": memory_summary,
        "hold_extensions": 0,
        "entry_snapshot": dict(snapshot),
    }
    stats["open_position"] = position
    _append_trade_event(
        {
            "event": "open",
            "logged_at_kst": now_kst.isoformat(),
            "trade_id": trade_id,
            "order_mode": "live",
            "binance_order_id": trade_id,
            "symbol": symbol,
            "interval": interval,
            "side": entry_side,
            "entry_price": entry_px,
            "stop_loss": float(stop_loss),
            "take_profit": tp,
            "position_size": qty_eff,
            "leverage": leverage,
            "reason": str(decision["reason"]),
            "confidence": float(decision["confidence"]),
            "similar_avg_pnl": float(similar_avg_pnl),
        }
    )
    logger.info(
        "실전 진입 접수 trade_id=%s symbol=%s side=%s qty=%s",
        trade_id,
        symbol,
        entry_side,
        qty_eff,
    )
    return position


def _should_send_periodic_report(stats: Dict[str, Any], now_kst: datetime, interval_minutes: int) -> bool:
    if interval_minutes <= 0:
        return True
    raw = str(stats.get("last_report_at_kst", "")).strip()
    if not raw:
        return True
    try:
        last_report = datetime.fromisoformat(raw)
    except ValueError:
        return True
    return now_kst - last_report >= timedelta(minutes=interval_minutes)


def _build_kakao_message(
    *,
    report: Dict[str, Any],
    stats: Dict[str, Any],
    learning_summary: str,
    close_info: Dict[str, Any] | None,
    open_position: Dict[str, Any] | None,
    dry_run: bool,
    live_futures_usdt: float | None,
    krw_per_usdt: float,
) -> str:
    ledger = summarize_ledger(stats)
    rb_krw, rb_usdt, rb_pct = resolve_report_balances(
        stats,
        dry_run=dry_run,
        live_futures_usdt=live_futures_usdt,
        krw_per_usdt=krw_per_usdt,
    )
    pnl_display = build_monthly_pnl_display(stats, balance_krw=rb_krw)
    sign = "+" if rb_pct >= 0 else ""
    total_count, attempt_count = _today_decision_counters(stats)
    market = report.get("market", {})
    close_event = close_info.get("close_event", {}) if close_info else {}
    bracket = _report_bracket_title(dry_run)
    bal_label = "가상 잔고/가상 수익률" if dry_run else "현재 잔고/누적 수익률"
    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"{_decision_emoji(report['decision'], not bool(report['risk_allowed']))} {bracket}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📌 최종 판단: {report['decision']} / 신뢰도 {report['confidence']:.2f}",
        f"💬 판단 사유: {_report_text(report['reason'])}",
        f"🧭 모니터링 요약({report.get('monitor_model', 'gpt-4o-mini')}): {_report_text(report.get('monitor_summary', ''), '시장 요약 없음')}",
        "",
        "🟢 [매매 알림]",
        f"코인: {market.get('symbol', 'BTCUSDT')} ({market.get('interval', '')})",
        f"현재 가격: {_format_price(market.get('price', 0.0))}",
        f"매수/매도 이유: {_report_text(report['reason'])}",
        f"기술 지표: RSI {_safe_float(market.get('rsi', 0.0)):.1f} / BB {_safe_float(market.get('bb_position', 0.0)):.2f} / EMA갭 {_safe_float(market.get('ema_gap_pct', 0.0)):+.2f}%",
        f"거래량: {_safe_float(market.get('volume_ratio_pct', 0.0)):.0f}% of 7d avg"
        + (" (급증 ✓)" if market.get("volume_surge") else " (미동반)"),
        "",
        "🔴 [매도 알림]",
        (
            f"청산 시각: {close_event.get('exit_at_kst', '')} / 사유: {close_event.get('exit_reason', '')} / "
            f"확정 수익: {_format_krw(close_event.get('pnl_krw', 0.0))} ({_safe_float(close_event.get('pnl_usdt', 0.0)):+.4f} USDT)"
            if close_event
            else "이번 사이클 청산 없음"
        ),
        "",
        "🧠 [학습 상태]",
        f"누적 유니크 실패 사례: {ledger.unique_failure_count}건",
        f"최근 개선점: {_report_text(ledger.last_reflection_summary, '아직 기록된 반성 없음')}",
        f"유사 실패 경고: {_report_text(learning_summary, '현재 유사한 실패 사례 없음')}",
        f"오늘 판단/진입: {total_count}회 / {attempt_count}회",
        "",
        "📊 [원장]",
        f"{bal_label}: {rb_krw:,.0f}원 ({sign}{rb_pct:.2f}%) / {rb_usdt:,.2f} USDT",
        pnl_display.monthly_line,
        pnl_display.net_line,
        f"AI 게이트 절감 호출: {_safe_int(stats.get('ai_calls_saved_by_gate', 0))}회 / 예상 절감 토큰: {_safe_int(stats.get('estimated_tokens_saved', 0)):,}",
        f"월 목표 100,000원까지 남은 기간: D-{days_left_in_month_kst()}일",
    ]
    if open_position:
        lines.extend(
            [
                "",
                "📍 [보유 포지션]",
                f"방향: {open_position.get('side', '')} / 진입가: {_format_price(open_position.get('entry_price', 0.0))}",
                f"손절: {_format_price(open_position.get('stop_loss', 0.0))} / 익절: {_format_price(open_position.get('take_profit', 0.0))}",
                f"만기: {open_position.get('expires_at_kst', '')} / 연장 {open_position.get('hold_extensions', 0)}회",
            ]
        )
    if close_info and close_info.get("reflection_result", {}).get("summary"):
        lines.extend(["", f"📝 반성 요약: {_report_text(close_info['reflection_result']['summary'])}"])
    return "\n".join(lines)


def _send_startup_report() -> None:
    global STARTUP_REPORT_SENT
    if STARTUP_REPORT_SENT:
        return

    _load_env()
    if _hydrate_kakao_tokens is not None:
        _hydrate_kakao_tokens()
    health_ok = _check_project_connectivity()

    krw_per_usdt = _krw_per_usdt()
    initial_krw, initial_usdt = _initial_balances(krw_per_usdt)
    stats = load_trading_stats(
        initial_balance_krw=initial_krw,
        initial_balance_usdt=initial_usdt,
    )
    dry_run = _env_bool("AI_DRY_RUN", True)
    _log_order_execution_setup(dry_run)
    live_usdt = None if dry_run else _fetch_binance_futures_usdt_balance()
    rb_krw, rb_usdt, rb_pct = resolve_report_balances(
        stats, dry_run=dry_run, live_futures_usdt=live_usdt, krw_per_usdt=krw_per_usdt
    )
    started_at = datetime.now(KST)
    model = _env_str("OPENAI_ENTRY_MODEL", _env_str("AI_ENTRY_MODEL", _env_str("OPENAI_MODEL", "gpt-4o")))
    mode = "가상 매매(DRY RUN)" if dry_run else "실거래 모드"
    loop_seconds = max(30, _env_int("AI_LOOP_SECONDS", 300))
    bracket = _report_bracket_title(dry_run)
    bal_line = (
        f"현재 가상 잔고/가상 수익률: {rb_krw:,.0f}원 ({rb_pct:+.2f}%) / {rb_usdt:,.4f} USDT"
        if dry_run
        else (
            f"현재 잔고/누적 수익률: {rb_krw:,.0f}원 ({rb_pct:+.2f}%) / {rb_usdt:,.4f} USDT (바이낸스 USDT-M 선물 지갑)"
            if live_usdt is not None
            else f"현재 잔고/누적 수익률: 바이낸스 조회 실패 — 원장 기준 {rb_krw:,.0f}원 ({rb_pct:+.2f}%) / {rb_usdt:,.4f} USDT"
        )
    )

    body = "\n".join(
        [
            "━━━━━━━━━━━━━━━━━━━━",
            f"🚀 {bracket} 운영 시작 보고",
            "━━━━━━━━━━━━━━━━━━━━",
            f"시스템 가동 시각: {started_at.strftime('%Y-%m-%d %H:%M:%S KST')}",
            bal_line,
            f"적용 모델: {model}",
            f"매매 모드: {mode}",
            f"감시 주기 설정: {loop_seconds}초",
            f"프로젝트 연결 상태: {'정상' if health_ok else '점검 필요'}",
            "",
            "시스템이 정상적으로 기동되었으며, 5분 주기로 시장 감시를 시작합니다.",
        ]
    )
    _notify_kakao(f"🚀 {bracket} 운영 시작", body)
    STARTUP_REPORT_SENT = True


def _synthetic_open_position_from_exchange(
    row: Dict[str, Any],
    *,
    symbol: str,
    interval: str,
    now_kst: datetime,
    hold_minutes: int,
) -> Dict[str, Any]:
    amt = _safe_float(row.get("positionAmt"), 0.0)
    side = "BUY" if amt > 0 else "SELL"
    entry = _safe_float(row.get("entryPrice"), 0.0)
    qty = abs(amt)
    return {
        "trade_id": f"RECOVERED-{now_kst.strftime('%Y%m%d%H%M%S')}",
        "symbol": symbol,
        "interval": interval,
        "side": side,
        "opened_at_kst": now_kst.isoformat(),
        "expires_at_kst": (now_kst + timedelta(minutes=hold_minutes)).isoformat(),
        "entry_price": float(entry),
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "position_size": float(qty),
        "leverage": _safe_int(row.get("leverage"), 0),
        "notional_usdt": float(entry) * float(qty),
        "decision_reason": "거래소 잔여 포지션 동기화(비정상 종료 추정)",
        "confidence": 1.0,
        "similar_avg_pnl": 0.0,
        "failure_memory_summary": "",
        "hold_extensions": 0,
        "entry_snapshot": {},
        "recovered_from_exchange": True,
    }


def _reconcile_ledger_with_exchange(
    stats: Dict[str, Any],
    *,
    dry_run: bool,
    symbol: str,
    interval: str,
    now_kst: datetime,
    hold_minutes: int,
) -> bool:
    """거래소 포지션과 `open_position` 불일치를 정리한다. 변경 시 True."""
    global _ORPHAN_POSITION_ALERT_SENT
    if dry_run or _futures_client_from_env is None or _signed_position_amt_for_symbol is None:
        return False
    client = _futures_client_from_env()
    if client is None:
        return False
    try:
        amt = float(_signed_position_amt_for_symbol(client, symbol))
    except Exception:
        return False
    ledger = stats.get("open_position") if isinstance(stats.get("open_position"), dict) else None
    changed = False
    if ledger and abs(amt) < 1e-12:
        stats["open_position"] = None
        changed = True
        logger.info("원장 open_position 제거: 거래소에 동일 심볼 포지션 없음")
    elif not ledger and abs(amt) >= 1e-12 and _futures_open_position_rows is not None:
        row = next(
            (r for r in _futures_open_position_rows(client) if str(r.get("symbol", "")).upper() == symbol.upper()),
            None,
        )
        if row is not None:
            stats["open_position"] = _synthetic_open_position_from_exchange(
                row, symbol=symbol, interval=interval, now_kst=now_kst, hold_minutes=hold_minutes
            )
            changed = True
            if not _ORPHAN_POSITION_ALERT_SENT:
                _ORPHAN_POSITION_ALERT_SENT = True
                _notify_kakao(
                    "⚠️ 미청산 포지션 발견",
                    f"{symbol}: 거래소에 포지션이 남아 있으나 원장에는 없었습니다. "
                    f"이번 루프부터 복구된 포지션으로 감시·청산 로직을 적용합니다.",
                )
    return changed


def _graceful_shutdown_work(trigger: Any) -> None:
    """SIGINT/SIGTERM/KeyboardInterrupt 시 호출. 호출자가 `_SHUTDOWN_LOCK`을 잡은 상태여야 한다."""
    if _GRACEFUL_SHUTDOWN_ONCE.is_set():
        return
    _GRACEFUL_SHUTDOWN_ONCE.set()
    _load_env()
    if _hydrate_kakao_tokens is not None:
        _hydrate_kakao_tokens()
    dry_run = _env_bool("AI_DRY_RUN", True)
    symbol = _env_str("AI_SYMBOL", "BTCUSDT")
    krw_per_usdt = _krw_per_usdt()
    initial_krw, initial_usdt = _initial_balances(krw_per_usdt)
    stats = load_trading_stats(
        initial_balance_krw=initial_krw,
        initial_balance_usdt=initial_usdt,
    )
    ledger = stats.get("open_position") if isinstance(stats.get("open_position"), dict) else None
    had_ledger = ledger is not None
    ex_amt = 0.0
    client = None
    if not dry_run and _futures_client_from_env is not None and _signed_position_amt_for_symbol is not None:
        client = _futures_client_from_env()
        if client is not None:
            try:
                ex_amt = float(_signed_position_amt_for_symbol(client, symbol))
            except Exception:
                ex_amt = 0.0
    need_exchange_close = (not dry_run) and client is not None and abs(ex_amt) >= 1e-12
    if not need_exchange_close and not had_ledger:
        logger.info("종료 정리(%s): 청산할 포지션 없음", trigger)
        return

    if need_exchange_close and _market_close_symbol is None:
        logger.error("종료 정리(%s): 청산 모듈을 불러올 수 없습니다.", trigger)
        _notify_kakao(
            "⚠️ 긴급 청산 실패",
            f"{symbol}: 청산 유틸리티를 불러올 수 없습니다. `scripts/emergency_exit.py`를 실행하세요. 트리거: {trigger}",
        )
    elif need_exchange_close:
        ok, msg = _market_close_symbol(client, symbol)
        if ok:
            logger.info("종료 정리(%s): 거래소 시장가 청산 완료 %s", trigger, msg)
            _notify_kakao(
                "프로그램 종료로 인해 포지션을 긴급 청산했습니다.",
                f"{symbol} 청산 완료 ({msg}). 트리거: {trigger}",
            )
            stats["open_position"] = None
            save_trading_stats(stats)
        else:
            logger.error("종료 정리(%s): 거래소 청산 실패 %s", trigger, msg)
            _notify_kakao(
                "⚠️ 긴급 청산 실패",
                f"{symbol} 시장가 청산에 실패했습니다({msg}). "
                f"`scripts/emergency_exit.py` 실행 또는 바이낸스에서 수동 확인하세요. 트리거: {trigger}",
            )
    elif dry_run and had_ledger:
        stats["open_position"] = None
        save_trading_stats(stats)
        logger.info("종료 정리(%s): 가상 포지션 원장 제거", trigger)
        _notify_kakao(
            "프로그램 종료로 인해 포지션을 긴급 청산했습니다.",
            f"가상 매매 원장의 오픈 포지션을 종료 처리했습니다. 트리거: {trigger}",
        )
    elif had_ledger and abs(ex_amt) < 1e-12:
        stats["open_position"] = None
        save_trading_stats(stats)
        logger.info("종료 정리(%s): 원장만 정리(거래소 무포지션)", trigger)


def _shutdown_signal_handler(signum: int, frame: Any) -> None:
    logger.warning("종료 신호 수신: %s", signum)
    try:
        with _SHUTDOWN_LOCK:
            _graceful_shutdown_work(signum)
    except Exception:
        logger.exception("우아한 종료 처리 중 예외")
    finally:
        # Windows 등에서 시그널 핸들러 안의 sys.exit가 무시되는 경우가 있어 즉시 종료 보장
        os._exit(0)


def install_shutdown_handlers() -> None:
    """메인 스레드에서 한 번만 호출한다."""
    global _SIGNAL_HANDLERS_INSTALLED
    if _SIGNAL_HANDLERS_INSTALLED:
        return
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _shutdown_signal_handler)
        except ValueError as exc:
            logger.debug("signal %s 등록 생략: %s", name, exc)
    _SIGNAL_HANDLERS_INSTALLED = True


def _is_weekend_trading_blocked(now_kst: datetime) -> bool:
    if _env_bool("AI_TRADE_ON_WEEKENDS", False):
        return False
    return now_kst.weekday() in (5, 6)


def _run_weekend_monitor_cycle(
    *,
    stats: Dict[str, Any],
    snapshot: Dict[str, Any],
    symbol: str,
    interval: str,
    dry_run: bool,
    now_kst: datetime,
    krw_per_usdt: float,
    open_position: Dict[str, Any] | None,
    close_info: Dict[str, Any] | None,
    entry_model: str,
    monitor_model: str,
    status_report_minutes: int,
) -> Dict[str, Any]:
    """토·일: 신규 진입·AI 분석 없이 보유 포지션 청산 감시만 수행."""
    live_wallet_usdt: float | None = None
    if not dry_run:
        live_wallet_usdt = _fetch_binance_futures_usdt_balance()
    save_trading_stats(stats)
    ledger = summarize_ledger(stats)
    rb_krw, rb_usdt, rb_pct = resolve_report_balances(
        stats,
        dry_run=dry_run,
        live_futures_usdt=live_wallet_usdt,
        krw_per_usdt=krw_per_usdt,
    )
    weekend_reason = "주말 휴장 중 (보유 포지션 청산만 감시)"
    if close_info:
        weekend_reason = f"{weekend_reason} — 이번 사이클 청산 완료"
    final_decision = {"decision": "HOLD", "reason": weekend_reason, "confidence": 1.0}
    report = {
        "decision": final_decision["decision"],
        "reason": final_decision["reason"],
        "confidence": float(final_decision["confidence"]),
        "market": snapshot,
        "backtest_sample_size": 0,
        "similar_avg_pnl": 0.0,
        "risk_allowed": True,
        "risk_ratio": 0.0,
        "dry_run": dry_run,
        "paper_balance_krw": rb_krw,
        "paper_balance_usdt": rb_usdt,
        "paper_profit_pct": rb_pct,
        "binance_futures_wallet_usdt": live_wallet_usdt if not dry_run else None,
        "unique_failure_count": ledger.unique_failure_count,
        "last_reflection_summary": ledger.last_reflection_summary,
        "failure_memory_similarity": 0.0,
        "open_position": open_position,
        "close_event": close_info["close_event"] if close_info else None,
        "raw_model_decision": final_decision,
        "adjusted_decision": final_decision,
        "technical_decision": final_decision,
        "entry_gate_open": False,
        "entry_gate_reason": weekend_reason,
        "entry_model": entry_model,
        "monitor_model": monitor_model,
        "estimated_tokens_saved": _safe_int(stats.get("estimated_tokens_saved", 0)),
        "weekend_mode": True,
    }
    if close_info:
        _notify_kakao(
            f"📊 {_report_bracket_title(dry_run)} 주말 청산",
            f"{symbol} ({interval}) 포지션 청산: {close_info.get('close_event', {}).get('exit_reason', '')}",
        )
    elif open_position and _should_send_periodic_report(stats, now_kst, status_report_minutes):
        _notify_kakao(
            f"📊 {_report_bracket_title(dry_run)} 주말 감시",
            f"{weekend_reason}\n보유: {open_position.get('side', '')} @ {_format_price(open_position.get('entry_price', 0.0))}",
        )
        stats["last_report_at_kst"] = now_kst.isoformat()
        save_trading_stats(stats)
    return report


def run_cycle() -> Dict[str, Any]:
    if not _CYCLE_LOCK.acquire(blocking=False):
        logger.warning("이전 매매 사이클이 아직 실행 중 — 중복 [AI 매매 판단] 실행을 건너뜁니다.")
        return {
            "decision": "HOLD",
            "reason": "이전 사이클 실행 중 — 중복 스케줄 건너뜀",
            "confidence": 1.0,
            "cycle_skipped": True,
        }
    try:
        with _SHUTDOWN_LOCK:
            return _run_cycle_impl()
    finally:
        _CYCLE_LOCK.release()


def _run_cycle_impl() -> Dict[str, Any]:
    _load_env()
    if _hydrate_kakao_tokens is not None:
        _hydrate_kakao_tokens()
    if calculate_position_size is None:
        raise RuntimeError("btc_live_trading.strategy.risk_manager 모듈을 로드할 수 없습니다.") from _POSITION_SIZE_IMPORT_ERROR

    symbol = _env_str("AI_SYMBOL", "BTCUSDT")
    interval = _env_str("AI_TIMEFRAME", "15m")
    leverage = _env_int("AI_LEVERAGE", 3)
    risk_per_trade = _env_float("AI_RISK_PER_TRADE", 0.015)
    dry_run = _env_bool("AI_DRY_RUN", True)
    _log_order_execution_setup(dry_run)
    entry_model = _env_str("OPENAI_ENTRY_MODEL", _env_str("AI_ENTRY_MODEL", _env_str("OPENAI_MODEL", "gpt-4o")))
    monitor_model = _env_str("OPENAI_MONITOR_MODEL", _env_str("AI_MONITOR_MODEL", "gpt-4o-mini"))
    krw_per_usdt = _krw_per_usdt()
    hold_minutes = _env_int("AI_PAPER_HOLD_MINUTES", 60)
    status_report_minutes = _env_int("AI_STATUS_REPORT_MINUTES", 60)
    initial_krw, initial_usdt = _initial_balances(krw_per_usdt)
    stats = load_trading_stats(
        initial_balance_krw=initial_krw,
        initial_balance_usdt=initial_usdt,
    )
    stats["run_count"] = _safe_int(stats.get("run_count", 0)) + 1
    stats["estimated_daily_server_cost_krw"] = _env_float(
        "AI_DAILY_SERVER_COST_KRW",
        _env_float("AI_EST_DAILY_SERVER_COST_KRW", _safe_float(stats.get("estimated_daily_server_cost_krw", 0.0))),
    )
    stats["monthly_subscription_cost_krw"] = _env_float(
        "AI_MONTHLY_SUBSCRIPTION_COST_KRW",
        _safe_float(stats.get("monthly_subscription_cost_krw", 0.0)),
    )
    now_kst = datetime.now(KST)
    stats["last_cycle_at_kst"] = now_kst.isoformat()
    if _reconcile_ledger_with_exchange(
        stats,
        dry_run=dry_run,
        symbol=symbol,
        interval=interval,
        now_kst=now_kst,
        hold_minutes=hold_minutes,
    ):
        save_trading_stats(stats)

    snapshot = fetch_market_snapshot(symbol=symbol, interval=interval)

    close_info: Dict[str, Any] | None = None
    open_position = stats.get("open_position") if isinstance(stats.get("open_position"), dict) else None
    if open_position:
        exit_reason = _position_exit_reason(stats, open_position, snapshot, now_kst, hold_minutes)
        open_position = stats.get("open_position") if isinstance(stats.get("open_position"), dict) else open_position
    else:
        exit_reason = None
    if open_position and exit_reason:
        close_info = _close_position(
            stats=stats,
            open_position=open_position,
            snapshot=snapshot,
            now_kst=now_kst,
            krw_per_usdt=krw_per_usdt,
            model=entry_model,
            exit_reason=exit_reason,
            dry_run=dry_run,
        )
        if close_info.get("exchange_close_failed"):
            open_position = stats.get("open_position") if isinstance(stats.get("open_position"), dict) else open_position
        else:
            open_position = None

    if _is_weekend_trading_blocked(now_kst):
        return _run_weekend_monitor_cycle(
            stats=stats,
            snapshot=snapshot,
            symbol=symbol,
            interval=interval,
            dry_run=dry_run,
            now_kst=now_kst,
            krw_per_usdt=krw_per_usdt,
            open_position=open_position if isinstance(open_position, dict) else None,
            close_info=close_info,
            entry_model=entry_model,
            monitor_model=monitor_model,
            status_report_minutes=status_report_minutes,
        )

    backtest_summary, similar_cases, similar_avg_pnl = _load_backtest_context(snapshot)
    gate_open, gate_reason = _ai_entry_gate(snapshot)
    memory_block, memory_summary, memory_similarity = _find_closest_failure_memory(snapshot)
    context_text = backtest_summary.to_context_text() + f"\nSimilar-case mean pnl: {similar_avg_pnl:.4f}\n"
    duplicate_candle = _should_skip_duplicate_candle_eval(snapshot, symbol, interval)
    if duplicate_candle:
        model_ai_decision = {
            "decision": "HOLD",
            "reason": "동일 캔들 구간 — 중복 AI 평가 생략",
            "confidence": 1.0,
        }
        stats["ai_calls_saved_by_gate"] = _safe_int(stats.get("ai_calls_saved_by_gate", 0)) + 1
    elif gate_open:
        reflection_digest = _top_recent_loss_reflections(_env_int("AI_REFLECTION_DIGEST_COUNT", 5))
        model_ai_decision = get_ai_decision(
            context_text,
            snapshot,
            similar_cases,
            model=entry_model,
            failure_memory=memory_block,
            reflection_digest=reflection_digest,
        )
        stats["ai_entry_calls"] = _safe_int(stats.get("ai_entry_calls", 0)) + 1
    else:
        model_ai_decision = {
            "decision": "HOLD",
            "reason": f"{gate_reason} - 로컬 게이트에서 HOLD",
            "confidence": 1.0,
        }
        stats["ai_calls_saved_by_gate"] = _safe_int(stats.get("ai_calls_saved_by_gate", 0)) + 1
        estimated_saved = _env_int("AI_EST_TOKENS_PER_ENTRY_CALL", 900)
        stats["estimated_tokens_saved"] = _safe_int(stats.get("estimated_tokens_saved", 0)) + max(0, estimated_saved)
    technical_decision = _technical_signal_decision(snapshot, backtest_summary.sample_size, similar_avg_pnl)
    raw_ai_decision = _blend_ai_and_technical_decision(model_ai_decision, technical_decision)
    raw_ai_decision = _apply_failure_similarity_guard(raw_ai_decision, memory_similarity, snapshot)
    raw_ai_decision = _apply_atr_entry_filter(raw_ai_decision, snapshot)
    raw_ai_decision = _apply_volume_entry_filter(raw_ai_decision, snapshot)

    live_wallet_usdt: float | None = None
    if not dry_run:
        live_wallet_usdt = _fetch_binance_futures_usdt_balance()
    if not dry_run and live_wallet_usdt is not None and live_wallet_usdt > 0:
        account_balance_usdt = float(live_wallet_usdt)
    else:
        if not dry_run:
            logger.warning("실전 모드에서 바이낸스 선물 USDT 잔고 조회 실패 — 원장 가상 잔고로 포지션 크기를 산출합니다.")
        account_balance_usdt = _safe_float(stats.get("virtual_balance_usdt", initial_usdt), initial_usdt)
    stop_loss = _build_stop_loss(snapshot, raw_ai_decision["decision"])
    stop_loss, gap_adjusted = ensure_min_stop_gap(
        entry_price=float(snapshot["price"]),
        stop_loss_price=stop_loss,
        side=raw_ai_decision["decision"],
        min_gap_ratio=0.005,
    )
    if gap_adjusted:
        logger.info("변동성 부족으로 손절 이격을 최소 0.5%%로 보정했습니다.")
    position_size = calculate_position_size(
        account_balance=account_balance_usdt,
        entry_price=float(snapshot["price"]),
        stop_loss=stop_loss,
        risk_per_trade=risk_per_trade,
        leverage=leverage,
    )
    if raw_ai_decision["decision"] in {"BUY", "SELL"} and position_size > 0 and not open_position:
        risk_check = assess_trade_risk(
            account_balance=account_balance_usdt,
            entry_price=float(snapshot["price"]),
            stop_loss_price=stop_loss,
            proposed_position_size=position_size,
            max_risk_ratio=_env_float("AI_MAX_RISK_RATIO", 0.025),
            on_block=lambda text, _d=dry_run: _notify_kakao(f"⚠️ {_report_bracket_title(_d)} 리스크 경고", text),
        )
    else:
        risk_check = {"allowed": True, "risk_ratio": 0.0, "reason": "진입 없음"}

    final_decision = dict(raw_ai_decision)
    if position_size <= 0 and raw_ai_decision["decision"] in {"BUY", "SELL"}:
        final_decision = {
            "decision": "HOLD",
            "reason": "손절 이격이 너무 좁아 포지션 산출 불가(변동성·최소 갭)",
            "confidence": 1.0,
        }
    elif not risk_check["allowed"] and raw_ai_decision["decision"] in {"BUY", "SELL"}:
        final_decision = {
            "decision": "HOLD",
            "reason": f"리스크 차단: {risk_check['reason']}",
            "confidence": 1.0,
        }
    elif open_position:
        pos_label = "가상 포지션" if dry_run else "포지션"
        final_decision = {
            "decision": "HOLD",
            "reason": f"기존 {pos_label} 보유 중 ({open_position.get('side', '')})",
            "confidence": float(raw_ai_decision.get("confidence", 0.0)),
        }

    opened_position: Dict[str, Any] | None = None
    if final_decision["decision"] in {"BUY", "SELL"} and not open_position:
        opened_position = _open_position(
            stats=stats,
            symbol=symbol,
            interval=interval,
            decision=final_decision,
            snapshot=snapshot,
            stop_loss=stop_loss,
            position_size=position_size,
            leverage=leverage,
            similar_avg_pnl=similar_avg_pnl,
            now_kst=now_kst,
            hold_minutes=hold_minutes,
            memory_summary=memory_summary,
            dry_run=dry_run,
        )
        if opened_position is not None:
            open_position = opened_position
        elif not dry_run:
            final_decision = {
                "decision": "HOLD",
                "reason": "실전 거래소 진입 주문 실패(카카오 알림 참고)",
                "confidence": 1.0,
            }

    save_trading_stats(stats)
    ledger = summarize_ledger(stats)
    rb_krw, rb_usdt, rb_pct = resolve_report_balances(
        stats,
        dry_run=dry_run,
        live_futures_usdt=live_wallet_usdt,
        krw_per_usdt=krw_per_usdt,
    )
    stats.pop("last_openai_api_usage", None)
    pnl_display = build_monthly_pnl_display(stats, balance_krw=rb_krw)
    report = {
        "decision": final_decision["decision"],
        "reason": final_decision["reason"],
        "confidence": float(final_decision["confidence"]),
        "market": snapshot,
        "backtest_sample_size": backtest_summary.sample_size,
        "similar_avg_pnl": similar_avg_pnl,
        "risk_allowed": bool(risk_check["allowed"]),
        "risk_ratio": float(risk_check["risk_ratio"]),
        "dry_run": dry_run,
        "paper_balance_krw": rb_krw,
        "paper_balance_usdt": rb_usdt,
        "paper_profit_pct": rb_pct,
        "binance_futures_wallet_usdt": live_wallet_usdt if not dry_run else None,
        "unique_failure_count": ledger.unique_failure_count,
        "last_reflection_summary": ledger.last_reflection_summary,
        "failure_memory_similarity": memory_similarity,
        "open_position": open_position,
        "close_event": close_info["close_event"] if close_info else None,
        "raw_model_decision": model_ai_decision,
        "adjusted_decision": raw_ai_decision,
        "technical_decision": technical_decision,
        "entry_gate_open": gate_open,
        "entry_gate_reason": gate_reason,
        "entry_model": entry_model,
        "monitor_model": monitor_model,
        "estimated_tokens_saved": _safe_int(stats.get("estimated_tokens_saved", 0)),
        "pnl_display": {
            "principal_recovering": pnl_display.principal_recovering,
            "status_label": pnl_display.status_label,
            "shortfall_krw": pnl_display.shortfall_krw,
            "monthly_realized_raw_krw": pnl_display.monthly_realized_raw_krw,
        },
    }
    _record_daily_decision_stats(
        stats,
        decision=str(report["decision"]),
        risk_allowed=bool(report["risk_allowed"]),
        now_kst=now_kst,
    )

    send_report = bool(opened_position or close_info or _should_send_periodic_report(stats, now_kst, status_report_minutes))
    if send_report:
        monitor_summary = get_market_monitor_summary(snapshot, model=monitor_model)
        stats["ai_monitor_calls"] = _safe_int(stats.get("ai_monitor_calls", 0)) + 1
        report["monitor_summary"] = monitor_summary.get("summary", "")
        msg = _build_kakao_message(
            report=report,
            stats=stats,
            learning_summary=memory_summary,
            close_info=close_info,
            open_position=open_position,
            dry_run=dry_run,
            live_futures_usdt=live_wallet_usdt,
            krw_per_usdt=krw_per_usdt,
        )
        _notify_kakao(f"📊 {_report_bracket_title(dry_run)} AI Self-Learning Engine", msg)
        stats["last_report_at_kst"] = now_kst.isoformat()
        save_trading_stats(stats)
    return report


def run_once() -> Dict[str, Any]:
    return run_cycle()


def _initialize_trading_loop_once(*, send_startup_report: bool = True) -> None:
    """프로세스당 1회만: env·핸들러·카카오 부트스트랩·연결 점검·시작 보고.

    run_forever / __main__ 양쪽에서 호출해도 Job·초기화가 중복 등록되지 않는다.
    """
    global _TRADING_LOOP_INITIALIZED
    if _TRADING_LOOP_INITIALIZED:
        return

    _load_env()
    install_shutdown_handlers()
    prune_stats_history()
    _prune_trade_log()
    _prune_learning_log()
    _check_project_connectivity()
    try:
        _bootstrap_kakao_tokens()
    except Exception as exc:
        logger.error("카카오 토큰 부트스트랩 실패(매매는 계속 진행): %s", type(exc).__name__)
    if send_startup_report:
        try:
            _send_startup_report()
        except Exception as exc:
            logger.error("운영 시작 보고 실패(매매는 계속 진행): %s", type(exc).__name__)
    _TRADING_LOOP_INITIALIZED = True


def run_forever() -> None:
    _initialize_trading_loop_once()
    loop_seconds = _cycle_interval_seconds()
    while True:
        cycle_started = time.time()
        try:
            _wait_for_next_cycle_slot(loop_seconds)
            # Safety First:
            # 매매 로직(차트 분석·주문)을 통째로 건너뛴다.
            if not _kakao_auth_ready():
                logger.error(
                    "카카오 인증 실패 — 매매 로직을 실행하지 않습니다. "
                    "SSH에서 bash ~/Coin/scripts/coinbot_watch.sh 를 실행하면 URL+입력 후 로그를 볼 수 있습니다."
                )
            else:
                result = run_cycle()
                if not result.get("cycle_skipped"):
                    print(_format_cycle_dashboard(result))
                    _mark_cycle_completed()
        except KeyboardInterrupt:
            try:
                with _SHUTDOWN_LOCK:
                    _graceful_shutdown_work("KeyboardInterrupt")
            except Exception:
                logger.exception("KeyboardInterrupt 종료 정리 중 예외")
            raise SystemExit(0) from None
        except Exception as exc:
            logger.exception("AI paper loop error: %s", exc)
            _br = _report_bracket_title(_env_bool("AI_DRY_RUN", True))
            _notify_kakao(f"❌ {_br} 오류", f"루프 오류 발생: {type(exc).__name__}")
        elapsed = time.time() - cycle_started
        sleep_for = max(1.0, loop_seconds - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    _acquire_single_instance_lock()
    _initialize_trading_loop_once()
    if _env_bool("AI_RUN_ONCE", False):
        try:
            if not _kakao_auth_ready():
                logger.error(
                    "카카오 인증 실패 — 매매 로직을 실행하지 않습니다. "
                    "봇을 터미널에서 직접 실행하면 대화형 인증이 진행됩니다."
                )
                raise SystemExit(1)
            print(_format_cycle_dashboard(run_cycle()))
            _mark_cycle_completed()
        except KeyboardInterrupt:
            try:
                with _SHUTDOWN_LOCK:
                    _graceful_shutdown_work("KeyboardInterrupt")
            except Exception:
                logger.exception("KeyboardInterrupt 종료 정리 중 예외")
            raise SystemExit(0) from None
    else:
        run_forever()
