from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
LIVE_DIR = ROOT_DIR / "btc_live_trading"
BACKTEST_DIR = ROOT_DIR / "btc_day_strategy"
DATA_DIR = BASE_DIR / "data"
DECISION_LOG_PATH = DATA_DIR / "ai_decisions.log"
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
                return
            if cur.parent == cur:
                break
            cur = cur.parent


_bootstrap_sys_path()

from ai_logic.decision_engine import analyze_trade_failure, get_ai_decision
from data.backtest_data import find_similar_backtest_cases, load_backtest_summary
from data.market_data import fetch_market_snapshot
from reporting import days_left_in_month_kst, load_trading_stats, save_trading_stats, summarize_ledger
from risk_guard import assess_trade_risk, ensure_min_stop_gap
from btc_live_trading.strategy.risk_manager import calculate_position_size

try:
    from btc_live_trading.kakao_notifier import KakaoNotifier
    from btc_live_trading.kakao_utils import get_access_token, hydrate_tokens_from_json as _hydrate_kakao_tokens
except Exception:
    KakaoNotifier = None
    _hydrate_kakao_tokens = None  # type: ignore
    get_access_token = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
ENV_LINE_MAP: Dict[str, int] = {}
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


def _load_env() -> None:
    explicit_env = Path(r"C:\Users\1226t\Desktop\Coin\btc_live_trading\.env")
    target_env = explicit_env if explicit_env.exists() else (LIVE_DIR / ".env")
    if not target_env.exists():
        logger.error(".env not found at expected path: %s", target_env)
        return
    _index_env_lines(target_env)
    _diagnose_env_section_4(target_env)
    _load_env_file_safely(target_env)


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
            os.environ.setdefault(key, value)


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


def _build_stop_loss(snapshot: Dict[str, float], decision: str) -> float:
    price = float(snapshot["price"])
    atr = float(snapshot["atr14"])
    atr_multiplier = _env_float("AI_ATR_STOP_MULTIPLIER", 1.8)
    if decision == "BUY":
        return price - (atr * atr_multiplier)
    if decision == "SELL":
        return price + (atr * atr_multiplier)
    return price


def _notify_kakao(title: str, body: str) -> None:
    if KakaoNotifier is None or _hydrate_kakao_tokens is None or get_access_token is None:
        return
    _hydrate_kakao_tokens()
    access = get_access_token()
    rest = os.getenv("KAKAO_REST_API_KEY", "").strip()
    if not access or not rest:
        return
    notifier = KakaoNotifier(access_token=access, enabled=True, rest_api_key=rest)
    notifier.send_message(title, body)


def _decision_emoji(decision: str, risk_blocked: bool) -> str:
    if risk_blocked:
        return "🚫"
    if decision == "BUY":
        return "🚀"
    if decision == "HOLD":
        return "⏸️"
    return "📉"


def _normalize_ai_log_file(log_path: Path, header: str) -> None:
    if not log_path.exists():
        return
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    except Exception:
        return
    if not lines:
        return
    structured = [ln for ln in lines if "|" in ln and not ln.startswith("[")]
    legacy = [ln for ln in lines if ln not in structured and ln != header]
    structured_data = [ln for ln in structured if ln != header]
    normalized = [header] + legacy + structured_data
    if lines == normalized:
        return
    with open(log_path, "w", encoding="utf-8") as f:
        for ln in normalized:
            f.write(ln + "\n")


def _append_structured_ai_log(
    *,
    phase: str,
    timestamp: datetime,
    snapshot: Dict[str, float],
    decision: str,
    confidence: float,
    risk_allowed: bool,
    reason: str,
) -> None:
    DECISION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = "ts_kst|phase|decision|confidence|rsi|bb_position|ema_bull|risk_allowed|entry_attempt|reason"
    _normalize_ai_log_file(DECISION_LOG_PATH, header)
    ema_bull = float(snapshot.get("ema20", 0.0)) > float(snapshot.get("ema60", 0.0))
    entry_attempt = decision in {"BUY", "SELL"} and risk_allowed
    clean_reason = str(reason).replace("\n", " ").replace("|", "/").strip()
    row = (
        f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}|{phase}|{decision}|{confidence:.2f}|"
        f"{float(snapshot.get('rsi', 0.0)):.2f}|{float(snapshot.get('bb_position', 0.0)):.3f}|"
        f"{'Y' if ema_bull else 'N'}|{'Y' if risk_allowed else 'N'}|"
        f"{'Y' if entry_attempt else 'N'}|{clean_reason}"
    )
    need_header = (not DECISION_LOG_PATH.exists()) or (DECISION_LOG_PATH.stat().st_size == 0)
    with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
        if need_header:
            f.write(header + "\n")
        f.write(row + "\n")


def _today_decision_counters() -> Tuple[int, int]:
    if not DECISION_LOG_PATH.exists():
        return 0, 0
    today = datetime.now(KST).strftime("%Y-%m-%d")
    total = 0
    attempts = 0
    try:
        with open(DECISION_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("ts_kst|"):
                    continue
                parts = line.split("|", 9)
                if len(parts) < 9:
                    continue
                ts, phase, _, _, _, _, _, _, entry_attempt = parts[:9]
                if not ts.startswith(today) or phase != "FINAL":
                    continue
                total += 1
                if entry_attempt == "Y":
                    attempts += 1
    except Exception:
        return 0, 0
    return total, attempts


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


def _load_learning_rows() -> List[Dict[str, str]]:
    if not LEARNING_LOG_PATH.exists():
        return []
    try:
        with open(LEARNING_LOG_PATH, "r", encoding="utf-8-sig", newline="") as f:
            return [row for row in csv.DictReader(f) if row]
    except Exception:
        return []


def _combined_learning_text(row: Dict[str, str]) -> str:
    return f"{row.get('market_signature', '')} || {row.get('failure_reason', '')}".strip()


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
    return True, best_ratio


def _append_trade_event(event: Dict[str, Any]) -> None:
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


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
    if best_row is None or best_ratio < 0.55:
        return "", "", 0.0
    memory = (
        f"similarity={best_ratio:.2f}\n"
        f"market_signature={best_row.get('market_signature', '')}\n"
        f"failure_reason={best_row.get('failure_reason', '')}\n"
        f"warning={best_row.get('warning', '')}\n"
        f"reflection={best_row.get('reflection_summary', '')}"
    )
    return memory, best_row.get("reflection_summary", ""), best_ratio


def _next_trade_id(now_kst: datetime) -> str:
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


def _close_position(
    *,
    stats: Dict[str, Any],
    open_position: Dict[str, Any],
    snapshot: Dict[str, Any],
    now_kst: datetime,
    krw_per_usdt: float,
    model: str,
) -> Dict[str, Any]:
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
) -> Dict[str, Any]:
    trade_id = _next_trade_id(now_kst)
    position = {
        "trade_id": trade_id,
        "symbol": symbol,
        "interval": interval,
        "side": decision["decision"],
        "opened_at_kst": now_kst.isoformat(),
        "expires_at_kst": (now_kst + timedelta(minutes=hold_minutes)).isoformat(),
        "entry_price": float(snapshot["price"]),
        "stop_loss": float(stop_loss),
        "position_size": float(position_size),
        "leverage": leverage,
        "notional_usdt": float(snapshot["price"]) * float(position_size),
        "decision_reason": str(decision["reason"]),
        "confidence": float(decision["confidence"]),
        "similar_avg_pnl": float(similar_avg_pnl),
        "failure_memory_summary": memory_summary,
        "entry_snapshot": dict(snapshot),
    }
    stats["open_position"] = position
    _append_trade_event(
        {
            "event": "open",
            "logged_at_kst": now_kst.isoformat(),
            "trade_id": trade_id,
            "symbol": symbol,
            "interval": interval,
            "side": decision["decision"],
            "entry_price": float(snapshot["price"]),
            "stop_loss": float(stop_loss),
            "position_size": float(position_size),
            "leverage": leverage,
            "reason": str(decision["reason"]),
            "confidence": float(decision["confidence"]),
            "similar_avg_pnl": float(similar_avg_pnl),
        }
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
) -> str:
    ledger = summarize_ledger(stats)
    sign = "+" if ledger.total_profit_pct >= 0 else ""
    total_count, attempt_count = _today_decision_counters()
    lines = [
        f"{_decision_emoji(report['decision'], not bool(report['risk_allowed']))} [AI 단타] {report['decision']} ({report['confidence']:.2f})",
        f"- 사유: {report['reason']}",
        f"- [현재 잔고/수익률]: {ledger.balance_krw:,.0f}원 ({sign}{ledger.total_profit_pct:.2f}%) / {ledger.balance_usdt:,.2f} USDT",
        f"- [AI 학습 상태]: 누적 유니크 실패 사례 {ledger.unique_failure_count}건 학습 완료",
        f"- [최근 반성]: {ledger.last_reflection_summary or '아직 기록된 반성 없음'}",
        f"- 유사 실패 경고: {learning_summary or '현재 유사한 실패 사례 없음'}",
        f"- 오늘 총 AI 판단 횟수: {total_count}회 / 진입 시도: {attempt_count}회",
        f"- 이번 달 남은 기간: D-{days_left_in_month_kst()}일",
    ]
    if close_info:
        close_event = close_info.get("close_event", {})
        lines.append(
            f"- 최근 청산: {close_event.get('side', '')} {close_event.get('pnl_krw', 0.0):,.0f}원 / {close_event.get('pnl_usdt', 0.0):,.3f} USDT"
        )
        reflection = close_info.get("reflection_result", {})
        if reflection.get("summary"):
            lines.append(f"- 반성 요약: {reflection['summary']}")
    if open_position:
        lines.append(
            f"- 보유 포지션: {open_position.get('side', '')} @ {float(open_position.get('entry_price', 0.0)):.2f} "
            f"(만기 {open_position.get('expires_at_kst', '')})"
        )
    return "\n".join(lines)


def run_cycle() -> Dict[str, Any]:
    _load_env()
    if _hydrate_kakao_tokens is not None:
        _hydrate_kakao_tokens()

    symbol = _env_str("AI_SYMBOL", "BTCUSDT")
    interval = _env_str("AI_TIMEFRAME", "5m")
    leverage = _env_int("AI_LEVERAGE", 3)
    risk_per_trade = _env_float("AI_RISK_PER_TRADE", 0.01)
    dry_run = _env_bool("AI_DRY_RUN", True)
    model = _env_str("OPENAI_MODEL", "gpt-4o")
    krw_per_usdt = _env_float("KRW_PER_USDT", 1380.0)
    hold_minutes = _env_int("AI_PAPER_HOLD_MINUTES", 15)
    status_report_minutes = _env_int("AI_STATUS_REPORT_MINUTES", 60)
    initial_krw, initial_usdt = _initial_balances(krw_per_usdt)
    stats = load_trading_stats(
        initial_balance_krw=initial_krw,
        initial_balance_usdt=initial_usdt,
    )
    stats["run_count"] = _safe_int(stats.get("run_count", 0)) + 1
    now_kst = datetime.now(KST)
    stats["last_cycle_at_kst"] = now_kst.isoformat()

    backtest_summary = load_backtest_summary(str(BACKTEST_DIR))
    snapshot = fetch_market_snapshot(symbol=symbol, interval=interval, limit=250)
    similar_cases = find_similar_backtest_cases(
        current_rsi=float(snapshot["rsi"]),
        current_atr_pct=float(snapshot["atr_pct"]),
        current_ema_gap_pct=float(snapshot["ema_gap_pct"]),
        backtest_dir=str(BACKTEST_DIR),
        top_n=5,
    )
    similar_avg_pnl = sum(x["pnl"] for x in similar_cases) / len(similar_cases) if similar_cases else 0.0

    close_info: Dict[str, Any] | None = None
    open_position = stats.get("open_position") if isinstance(stats.get("open_position"), dict) else None
    if open_position and _position_due(open_position, now_kst):
        close_info = _close_position(
            stats=stats,
            open_position=open_position,
            snapshot=snapshot,
            now_kst=now_kst,
            krw_per_usdt=krw_per_usdt,
            model=model,
        )
        open_position = None

    memory_block, memory_summary, memory_similarity = _find_closest_failure_memory(snapshot)
    context_text = backtest_summary.to_context_text() + f"\nSimilar-case mean pnl: {similar_avg_pnl:.4f}\n"
    raw_ai_decision = get_ai_decision(
        context_text,
        snapshot,
        similar_cases,
        model=model,
        failure_memory=memory_block,
    )
    _append_structured_ai_log(
        phase="RAW",
        timestamp=now_kst,
        snapshot=snapshot,
        decision=raw_ai_decision["decision"],
        confidence=float(raw_ai_decision["confidence"]),
        risk_allowed=True,
        reason=raw_ai_decision["reason"],
    )

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
            max_risk_ratio=0.01,
            on_block=lambda text: _notify_kakao("⚠️ AI 리스크 경고", text),
        )
    else:
        risk_check = {"allowed": True, "risk_ratio": 0.0, "reason": "가상 진입 없음"}

    final_decision = dict(raw_ai_decision)
    if position_size <= 0 and raw_ai_decision["decision"] in {"BUY", "SELL"}:
        final_decision = {"decision": "HOLD", "reason": "변동성 부족으로 인한 대기", "confidence": 1.0}
    elif not risk_check["allowed"] and raw_ai_decision["decision"] in {"BUY", "SELL"}:
        final_decision = {
            "decision": "HOLD",
            "reason": f"리스크 차단: {risk_check['reason']}",
            "confidence": 1.0,
        }
    elif open_position:
        final_decision = {
            "decision": "HOLD",
            "reason": f"기존 가상 포지션 보유 중 ({open_position.get('side', '')})",
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
        )
        open_position = opened_position

    save_trading_stats(stats)
    ledger = summarize_ledger(stats)
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
        "paper_balance_krw": ledger.balance_krw,
        "paper_balance_usdt": ledger.balance_usdt,
        "paper_profit_pct": ledger.total_profit_pct,
        "unique_failure_count": ledger.unique_failure_count,
        "last_reflection_summary": ledger.last_reflection_summary,
        "failure_memory_similarity": memory_similarity,
        "open_position": open_position,
        "close_event": close_info["close_event"] if close_info else None,
        "raw_model_decision": raw_ai_decision,
    }
    _append_structured_ai_log(
        phase="FINAL",
        timestamp=now_kst,
        snapshot=snapshot,
        decision=report["decision"],
        confidence=float(report["confidence"]),
        risk_allowed=bool(report["risk_allowed"]),
        reason=report["reason"],
    )

    send_report = bool(opened_position or close_info or _should_send_periodic_report(stats, now_kst, status_report_minutes))
    if send_report:
        msg = _build_kakao_message(
            report=report,
            stats=stats,
            learning_summary=memory_summary,
            close_info=close_info,
            open_position=open_position,
        )
        _notify_kakao("📊 AI Self-Learning Paper Engine", msg)
        stats["last_report_at_kst"] = now_kst.isoformat()
        save_trading_stats(stats)
    return report


def run_once() -> Dict[str, Any]:
    return run_cycle()


def run_forever() -> None:
    _load_env()
    loop_seconds = max(30, _env_int("AI_LOOP_SECONDS", 300))
    while True:
        cycle_started = time.time()
        try:
            result = run_cycle()
            print(json.dumps(result, ensure_ascii=False))
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.exception("AI paper loop error: %s", exc)
            _notify_kakao("❌ AI Paper Engine 오류", f"루프 오류 발생: {type(exc).__name__}")
        elapsed = time.time() - cycle_started
        sleep_for = max(1.0, loop_seconds - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    _load_env()
    if _env_bool("AI_RUN_ONCE", False):
        print(json.dumps(run_cycle(), ensure_ascii=False))
    else:
        run_forever()
