from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Dict


@dataclass
class SubscriptionGoalReport:
    monthly_target_krw: int
    realized_profit_krw: float
    achievement_rate: float
    status: str


KST = timezone(timedelta(hours=9))


def _stats_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "trading_stats.json"


def _history_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "history"


def _current_period(now_kst: datetime) -> str:
    return now_kst.strftime("%Y-%m")


def _monthly_reset_cutoff(now_kst: datetime) -> datetime:
    return now_kst.replace(day=1, hour=9, minute=0, second=0, microsecond=0)


def _backup_stats(stats: Dict[str, Any], *, backup_period: str) -> None:
    history_dir = _history_dir()
    history_dir.mkdir(parents=True, exist_ok=True)
    backup_path = history_dir / f"trading_stats_{backup_period}.json"
    payload = dict(stats)
    payload["backed_up_at_kst"] = datetime.now(KST).isoformat()
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _maybe_monthly_reset(stats: Dict[str, Any], now_kst: datetime) -> Dict[str, Any]:
    current_period = _current_period(now_kst)
    stored_period = str(stats.get("period", current_period))
    cutoff = _monthly_reset_cutoff(now_kst)
    if now_kst >= cutoff and stored_period != current_period:
        _backup_stats(stats, backup_period=stored_period)
        return {
            "cumulative_profit_krw": 0.0,
            "run_count": 0,
            "period": current_period,
            "last_reset_at_kst": now_kst.isoformat(),
        }
    if "period" not in stats:
        stats["period"] = current_period
    return stats


def load_trading_stats(path: Path | None = None) -> Dict[str, Any]:
    p = path or _stats_path()
    now_kst = datetime.now(KST)
    if not p.exists():
        return {"cumulative_profit_krw": 0.0, "run_count": 0, "period": _current_period(now_kst)}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"cumulative_profit_krw": 0.0, "run_count": 0, "period": _current_period(now_kst)}
        normalized = {
            "cumulative_profit_krw": float(data.get("cumulative_profit_krw", 0.0)),
            "run_count": int(data.get("run_count", 0)),
            "period": str(data.get("period", _current_period(now_kst))),
        }
        return _maybe_monthly_reset(normalized, now_kst)
    except Exception:
        return {"cumulative_profit_krw": 0.0, "run_count": 0, "period": _current_period(now_kst)}


def save_trading_stats(stats: Dict[str, Any], path: Path | None = None) -> None:
    p = path or _stats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def build_goal_report(realized_profit_krw: float, monthly_target_krw: int = 100_000) -> SubscriptionGoalReport:
    if monthly_target_krw <= 0:
        monthly_target_krw = 100_000
    stats = load_trading_stats()
    cumulative = float(stats.get("cumulative_profit_krw", 0.0)) + realized_profit_krw
    stats["cumulative_profit_krw"] = cumulative
    stats["run_count"] = int(stats.get("run_count", 0)) + 1
    save_trading_stats(stats)
    achievement = max(0.0, cumulative / monthly_target_krw * 100)
    status = "달성" if achievement >= 100 else "진행중"
    return SubscriptionGoalReport(
        monthly_target_krw=monthly_target_krw,
        realized_profit_krw=cumulative,
        achievement_rate=achievement,
        status=status,
    )


def days_left_in_month_kst() -> int:
    now = datetime.now(KST)
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_month = now.replace(month=now.month + 1, day=1)
    return (next_month.date() - now.date()).days
