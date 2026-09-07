"""OpenAI API 호출·게이트 절감 현황 리포트.

usage:
  py -3 scripts/ai_cost_report.py
  py -3 scripts/ai_cost_report.py --days 14
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
AI_DATA = ROOT / "ai_trading" / "data"
STATS_PATH = AI_DATA / "trading_stats.json"
LEARNING_LOG_PATH = AI_DATA / "ai_learning_logs.csv"
ENV_PATH = ROOT / "btc_live_trading" / ".env"
KST = timezone(timedelta(hours=9))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_float(name: str) -> float | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _learning_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))
    except OSError:
        return 0


def _day_rows(stats: Dict[str, Any], days: int) -> List[Tuple[str, Dict[str, int]]]:
    by_day = stats.get("ai_calls_by_day")
    if not isinstance(by_day, dict):
        by_day = {}
    today = datetime.now(KST).date()
    out: List[Tuple[str, Dict[str, int]]] = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        raw = by_day.get(day) if isinstance(by_day.get(day), dict) else {}
        out.append(
            (
                day,
                {
                    "entry": _safe_int(raw.get("entry", 0)),
                    "monitor": _safe_int(raw.get("monitor", 0)),
                    "saved_gate": _safe_int(raw.get("saved_gate", 0)),
                    "saved_repeat": _safe_int(raw.get("saved_repeat", 0)),
                },
            )
        )
    return out


def _fmt_row(cols: List[str], widths: List[int]) -> str:
    return " | ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(cols, widths)))


def _estimate_cost_usd(
    entry_calls: int,
    monitor_calls: int,
    *,
    tokens_per_entry: int,
    tokens_per_monitor: int,
    price_in: float,
    price_out: float,
    input_ratio: float,
) -> Tuple[float, float, float]:
    """대략 토큰·USD 추정. 반환: (input_tokens, output_tokens, usd)."""
    total_tokens = entry_calls * max(0, tokens_per_entry) + monitor_calls * max(0, tokens_per_monitor)
    ratio = min(1.0, max(0.0, input_ratio))
    input_tokens = total_tokens * ratio
    output_tokens = total_tokens * (1.0 - ratio)
    usd = (input_tokens / 1000.0) * price_in + (output_tokens / 1000.0) * price_out
    return input_tokens, output_tokens, usd


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 호출·게이트 절감 비용 리포트")
    parser.add_argument("--days", type=int, default=7, help="최근 N일 (기본 7)")
    parser.add_argument("--stats", type=Path, default=STATS_PATH, help="trading_stats.json 경로")
    parser.add_argument("--learning", type=Path, default=LEARNING_LOG_PATH, help="ai_learning_logs.csv 경로")
    args = parser.parse_args()
    days = max(1, int(args.days))

    _load_dotenv(ENV_PATH)
    stats = _load_stats(args.stats)
    learning_n = _learning_row_count(args.learning)

    entry = _safe_int(stats.get("ai_entry_calls", 0))
    monitor = _safe_int(stats.get("ai_monitor_calls", 0))
    saved_gate = _safe_int(stats.get("ai_calls_saved_by_gate", 0))
    saved_repeat = _safe_int(stats.get("ai_calls_saved_by_repeat_failure", 0))
    tokens_saved = _safe_int(stats.get("estimated_tokens_saved", 0))
    period = str(stats.get("period", "(없음)"))

    actual = entry + monitor
    denom = actual + saved_gate
    save_ratio = (saved_gate / denom * 100.0) if denom > 0 else 0.0

    print("=" * 72)
    print("OpenAI API 호출·게이트 절감 리포트")
    print("=" * 72)
    print(f"stats: {args.stats}")
    print(f"learning log: {args.learning} ({learning_n} rows)")
    print(f"period (원장): {period}")
    print()

    print("[기간 누적 카운터]")
    print(f"  AI 진입 호출(ai_entry_calls)          : {entry:,}")
    print(f"  AI 모니터 호출(ai_monitor_calls)      : {monitor:,}")
    print(f"  게이트 절감(ai_calls_saved_by_gate)   : {saved_gate:,}")
    print(f"  반복실패 차단(saved_by_repeat_failure): {saved_repeat:,}")
    print(f"  예상 절감 토큰(estimated_tokens_saved): {tokens_saved:,}")
    print(f"  절감 비율 (절감 / (실제+절감))        : {save_ratio:.1f}%")
    print()

    day_rows = _day_rows(stats, days)
    headers = ["날짜(KST)", "진입", "모니터", "게이트절감", "반복실패차단"]
    widths = [12, 8, 8, 10, 12]
    print(f"[최근 {days}일 일별 호출]")
    print(_fmt_row(headers, widths))
    print("-" * (sum(widths) + 3 * (len(widths) - 1)))
    sum_entry = sum_mon = sum_gate = sum_rep = 0
    has_any_day = False
    for day, row in day_rows:
        if any(row[k] for k in ("entry", "monitor", "saved_gate", "saved_repeat")):
            has_any_day = True
        sum_entry += row["entry"]
        sum_mon += row["monitor"]
        sum_gate += row["saved_gate"]
        sum_rep += row["saved_repeat"]
        print(
            _fmt_row(
                [
                    day,
                    str(row["entry"]),
                    str(row["monitor"]),
                    str(row["saved_gate"]),
                    str(row["saved_repeat"]),
                ],
                widths,
            )
        )
    print("-" * (sum(widths) + 3 * (len(widths) - 1)))
    print(
        _fmt_row(
            [f"{days}일 합계", str(sum_entry), str(sum_mon), str(sum_gate), str(sum_rep)],
            widths,
        )
    )
    if not has_any_day:
        print()
        print(
            "안내: ai_calls_by_day 일별 이력이 아직 없습니다. "
            "봇이 새 카운터를 쌓기 시작하면 일별 표가 채워집니다. "
            "그동안은 위 기간 누적 카운터를 참고하세요."
        )
    print()

    price_in = _env_float("OPENAI_PRICE_PER_1K_INPUT")
    price_out = _env_float("OPENAI_PRICE_PER_1K_OUTPUT")
    tokens_per_entry = _env_int("AI_EST_TOKENS_PER_ENTRY_CALL", 900)
    tokens_per_monitor = _env_int("AI_EST_TOKENS_PER_MONITOR_CALL", 400)
    input_ratio = _env_float("AI_EST_INPUT_TOKEN_RATIO")
    if input_ratio is None:
        input_ratio = 0.75

    print("[비용 추정치]")
    if price_in is None or price_out is None:
        print("  가격 미설정 - 호출 횟수만 표시합니다.")
        print("  .env 에 다음을 추가하세요:")
        print("    OPENAI_PRICE_PER_1K_INPUT=0.0025   # USD / 1K input tokens (예시)")
        print("    OPENAI_PRICE_PER_1K_OUTPUT=0.01    # USD / 1K output tokens (예시)")
        print(f"  이번 기간 실제 호출: 진입 {entry:,} + 모니터 {monitor:,} = {actual:,}")
    else:
        in_tok, out_tok, usd = _estimate_cost_usd(
            entry,
            monitor,
            tokens_per_entry=tokens_per_entry,
            tokens_per_monitor=tokens_per_monitor,
            price_in=price_in,
            price_out=price_out,
            input_ratio=float(input_ratio),
        )
        saved_in, saved_out, saved_usd = _estimate_cost_usd(
            saved_gate,  # 절감된 진입 호출 근사
            0,
            tokens_per_entry=tokens_per_entry,
            tokens_per_monitor=tokens_per_monitor,
            price_in=price_in,
            price_out=price_out,
            input_ratio=float(input_ratio),
        )
        print(f"  단가: input ${price_in}/1K · output ${price_out}/1K")
        print(f"  가정: entry≈{tokens_per_entry} tok, monitor≈{tokens_per_monitor} tok, input비율={input_ratio:.0%}")
        print(f"  실제 호출 추정 토큰: in {in_tok:,.0f} / out {out_tok:,.0f}")
        print(f"  실제 호출 추정 비용: ${usd:.4f}")
        print(f"  게이트 절감 추정 비용: ${saved_usd:.4f} (in {saved_in:,.0f} / out {saved_out:,.0f})")
        print(f"  그중 반복실패 차단 분: {saved_repeat:,}회")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
