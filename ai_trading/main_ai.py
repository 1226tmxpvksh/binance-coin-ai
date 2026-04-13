from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
LIVE_DIR = ROOT_DIR / "btc_live_trading"
BACKTEST_DIR = ROOT_DIR / "btc_day_strategy"


def _bootstrap_sys_path() -> None:
    """ai_trading 외부 모듈 import를 위해 경로를 자동 보정."""
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

from data.backtest_data import find_similar_backtest_cases, load_backtest_summary
from data.market_data import fetch_market_snapshot
from ai_logic.decision_engine import get_ai_decision
from reporting import build_goal_report, days_left_in_month_kst
from risk_guard import assess_trade_risk, ensure_min_stop_gap
from strategy.risk_manager import calculate_position_size

try:
    from kakao_notifier import KakaoNotifier
except Exception:
    KakaoNotifier = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
ENV_LINE_MAP: Dict[str, int] = {}


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
    """
    '# 4.' 운영 설정 섹션에서 인코딩 문제가 의심되는 라인을 라인번호와 함께 로그.
    """
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
    if KakaoNotifier is None:
        return
    access = os.getenv("KAKAO_ACCESS_TOKEN", "")
    rest = os.getenv("KAKAO_REST_API_KEY", "")
    if not access:
        return
    notifier = KakaoNotifier(access_token=access, enabled=True, rest_api_key=rest)
    notifier.send_message(title, body)


def _append_ai_decision_log(entry: str) -> None:
    log_path = BASE_DIR / "data" / "ai_decisions.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry + "\n")


def _decision_emoji(decision: str, risk_blocked: bool) -> str:
    if risk_blocked:
        return "🚫"
    if decision == "BUY":
        return "🚀"
    if decision == "HOLD":
        return "⏸️"
    return "📉"


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
    log_path = BASE_DIR / "data" / "ai_decisions.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "ts_kst|phase|decision|confidence|rsi|bb_position|ema_bull|risk_allowed|entry_attempt|reason"
    )
    _normalize_ai_log_file(log_path, header)
    ema_bull = float(snapshot.get("ema20", 0.0)) > float(snapshot.get("ema60", 0.0))
    entry_attempt = decision in {"BUY", "SELL"} and risk_allowed
    clean_reason = str(reason).replace("\n", " ").replace("|", "/").strip()
    row = (
        f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}|{phase}|{decision}|{confidence:.2f}|"
        f"{float(snapshot.get('rsi', 0.0)):.2f}|{float(snapshot.get('bb_position', 0.0)):.3f}|"
        f"{'Y' if ema_bull else 'N'}|{'Y' if risk_allowed else 'N'}|"
        f"{'Y' if entry_attempt else 'N'}|{clean_reason}"
    )
    need_header = (not log_path.exists()) or (log_path.stat().st_size == 0)
    with open(log_path, "a", encoding="utf-8") as f:
        if need_header:
            f.write(header + "\n")
        f.write(row + "\n")


def _normalize_ai_log_file(log_path: Path, header: str) -> None:
    """
    로그 헤더를 파일 첫 줄에 정확히 1회만 유지.
    기존 자유형 로그는 보존하되 데이터 라인 뒤로 재배치.
    """
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


def _today_decision_counters() -> Tuple[int, int]:
    log_path = BASE_DIR / "data" / "ai_decisions.log"
    if not log_path.exists():
        return 0, 0
    today = datetime.now(KST).strftime("%Y-%m-%d")
    total = 0
    attempts = 0
    try:
        with open(log_path, "r", encoding="utf-8") as f:
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


def run_once() -> Dict[str, Any]:
    _load_env()

    symbol = _env_str("AI_SYMBOL", "BTCUSDT")
    interval = _env_str("AI_TIMEFRAME", "5m")
    account_balance = _env_float("AI_ACCOUNT_BALANCE_USDT", 1000.0)
    leverage = _env_int("AI_LEVERAGE", 3)
    risk_per_trade = _env_float("AI_RISK_PER_TRADE", 0.01)
    dry_run = _env_bool("AI_DRY_RUN", True)
    model = _env_str("OPENAI_MODEL", "gpt-4o")
    monthly_profit_krw = _env_float("AI_MONTHLY_PROFIT_KRW", 0.0)
    krw_per_usdt = _env_float("KRW_PER_USDT", 1350.0)

    backtest_summary = load_backtest_summary(str(BACKTEST_DIR))
    snapshot = fetch_market_snapshot(symbol=symbol, interval=interval, limit=250)
    similar_cases = find_similar_backtest_cases(
        current_rsi=float(snapshot["rsi"]),
        current_atr_pct=float(snapshot["atr_pct"]),
        current_ema_gap_pct=float(snapshot["ema_gap_pct"]),
        backtest_dir=str(BACKTEST_DIR),
        top_n=5,
    )
    similar_avg_pnl = (
        sum(x["pnl"] for x in similar_cases) / len(similar_cases) if similar_cases else 0.0
    )
    context_text = (
        backtest_summary.to_context_text()
        + f"\nSimilar-case mean pnl: {similar_avg_pnl:.4f}\n"
    )

    ai_decision = get_ai_decision(context_text, snapshot, similar_cases, model=model)
    now_kst = datetime.now(KST)
    _append_structured_ai_log(
        phase="RAW",
        timestamp=now_kst,
        snapshot=snapshot,
        decision=ai_decision["decision"],
        confidence=float(ai_decision["confidence"]),
        risk_allowed=True,
        reason=ai_decision["reason"],
    )

    stop_loss = _build_stop_loss(snapshot, ai_decision["decision"])
    stop_loss, gap_adjusted = ensure_min_stop_gap(
        entry_price=float(snapshot["price"]),
        stop_loss_price=stop_loss,
        side=ai_decision["decision"],
        min_gap_ratio=0.005,
    )
    if gap_adjusted:
        logger.info("변동성 부족으로 손절 이격을 최소 0.5%%로 보정했습니다.")
    position_size = calculate_position_size(
        account_balance=account_balance,
        entry_price=float(snapshot["price"]),
        stop_loss=stop_loss,
        risk_per_trade=risk_per_trade,
        leverage=leverage,
    )
    if position_size <= 0:
        wait_reason = "변동성 부족으로 인한 대기"
        logger.info(wait_reason)
        ai_decision = {"decision": "HOLD", "reason": wait_reason, "confidence": 1.0}
    risk_check = assess_trade_risk(
        account_balance=account_balance,
        entry_price=float(snapshot["price"]),
        stop_loss_price=stop_loss,
        proposed_position_size=position_size,
        max_risk_ratio=0.01,
        on_block=lambda text: _notify_kakao("⚠️ AI 리스크 경고", text),
    )
    if not risk_check["allowed"]:
        ai_decision = {
            "decision": "HOLD",
            "reason": f"리스크 차단: {risk_check['reason']}",
            "confidence": 1.0,
        }

    if dry_run and ai_decision["decision"] in {"BUY", "SELL"}:
        expected_profit_usdt = position_size * float(snapshot["price"]) * (similar_avg_pnl / 100.0)
        expected_profit_krw = expected_profit_usdt * krw_per_usdt
    else:
        expected_profit_krw = monthly_profit_krw
    goal = build_goal_report(realized_profit_krw=expected_profit_krw, monthly_target_krw=100_000)

    report = {
        "decision": ai_decision["decision"],
        "reason": ai_decision["reason"],
        "confidence": ai_decision["confidence"],
        "market": snapshot,
        "backtest_sample_size": backtest_summary.sample_size,
        "similar_avg_pnl": similar_avg_pnl,
        "risk_allowed": risk_check["allowed"],
        "risk_ratio": risk_check["risk_ratio"],
        "monthly_goal": {
            "target_krw": goal.monthly_target_krw,
            "realized_krw": goal.realized_profit_krw,
            "achievement_rate": goal.achievement_rate,
            "status": goal.status,
        },
        "dry_run": dry_run,
        "expected_profit_krw": expected_profit_krw,
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
    today_total, today_attempts = _today_decision_counters()

    dry_run_line = ""
    if dry_run:
        dry_run_line = f"\n- Dry-run: 실제였다면 예상 수익은 {expected_profit_krw:,.0f}원"
        logger.info("Dry-run 모드: 실제였다면 예상 수익은 %s원", f"{expected_profit_krw:,.0f}")

    risk_blocked = not bool(report["risk_allowed"])
    emoji = _decision_emoji(report["decision"], risk_blocked)
    msg = (
        f"{emoji} [AI 단타] {report['decision']} ({report['confidence']:.2f})\n"
        f"- 사유: {report['reason']}\n"
        f"- 유사구간 평균 PnL: {report['similar_avg_pnl']:.4f}\n"
        f"- 월 목표 달성률: {goal.achievement_rate:.1f}% ({goal.realized_profit_krw:,.0f}/{goal.monthly_target_krw:,.0f} KRW)\n"
        f"- 리스크 체크: {'통과' if report['risk_allowed'] else '차단'} ({report['risk_ratio']*100:.2f}%)\n"
        f"- 이번 달 남은 기간: D-{days_left_in_month_kst()}일\n"
        f"- 오늘 총 AI 판단 횟수: {today_total}회 / 실제 진입 시도: {today_attempts}회"
        f"{dry_run_line}"
    )
    _notify_kakao("📊 AI 단타 리포트", msg)
    return report


if __name__ == "__main__":
    result = run_once()
    print(result)
