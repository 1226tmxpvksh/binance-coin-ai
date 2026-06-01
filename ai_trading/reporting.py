from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple


@dataclass
class SubscriptionGoalReport:
    monthly_target_krw: int
    realized_profit_krw: float
    achievement_rate: float
    status: str


@dataclass
class LedgerSummary:
    balance_krw: float
    balance_usdt: float
    total_profit_krw: float
    total_profit_usdt: float
    total_profit_pct: float
    monthly_profit_krw: float
    estimated_daily_server_cost_krw: float
    monthly_subscription_cost_krw: float
    monthly_operating_cost_krw: float
    monthly_net_profit_krw: float
    trade_count: int
    win_count: int
    loss_count: int
    unique_failure_count: int
    last_reflection_summary: str


@dataclass
class MonthlyPnlDisplay:
    """원금 복구 중일 때 월 실현손익 착시를 막기 위한 표시용 집계."""
    principal_recovering: bool
    status_label: str
    initial_balance_krw: float
    balance_krw: float
    shortfall_krw: float
    monthly_realized_raw_krw: float
    monthly_profit_display_krw: float
    monthly_net_display_krw: float
    monthly_operating_cost_krw: float
    monthly_line: str
    net_line: str


KST = timezone(timedelta(hours=9))


def _stats_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "trading_stats.json"


def _history_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "history"


def _current_period(now_kst: datetime) -> str:
    return now_kst.strftime("%Y-%m")


def _monthly_reset_cutoff(now_kst: datetime) -> datetime:
    return now_kst.replace(day=1, hour=9, minute=0, second=0, microsecond=0)


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


def default_trading_stats(
    *,
    initial_balance_krw: float,
    initial_balance_usdt: float,
    now_kst: datetime | None = None,
) -> Dict[str, Any]:
    now = now_kst or datetime.now(KST)
    return {
        "period": _current_period(now),
        "last_reset_at_kst": now.isoformat(),
        "initial_balance_krw": float(initial_balance_krw),
        "initial_balance_usdt": float(initial_balance_usdt),
        "virtual_balance_krw": float(initial_balance_krw),
        "virtual_balance_usdt": float(initial_balance_usdt),
        "total_realized_pnl_krw": 0.0,
        "total_realized_pnl_usdt": 0.0,
        "monthly_realized_pnl_krw": 0.0,
        "monthly_realized_pnl_usdt": 0.0,
        "estimated_daily_server_cost_krw": 0.0,
        "monthly_subscription_cost_krw": 0.0,
        "cumulative_profit_krw": 0.0,
        "run_count": 0,
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "monthly_trade_count": 0,
        "monthly_win_count": 0,
        "monthly_loss_count": 0,
        "unique_failure_count": 0,
        "last_reflection_summary": "",
        "last_cycle_at_kst": "",
        "last_trade_closed_at_kst": "",
        "last_report_at_kst": "",
        "ai_entry_calls": 0,
        "ai_monitor_calls": 0,
        "ai_calls_saved_by_gate": 0,
        "estimated_tokens_saved": 0,
        "open_position": None,
    }


def _backup_stats(
    stats: Dict[str, Any],
    *,
    backup_period: str,
    archived_at_kst: datetime | None = None,
) -> None:
    """월말 리셋 직전 스냅샷. 동일 월(backup_period)은 파일 1개만 유지(덮어쓰기)."""
    history_dir = _history_dir()
    history_dir.mkdir(parents=True, exist_ok=True)
    at = archived_at_kst or datetime.now(KST)
    backup_path = history_dir / f"trading_stats_month_{backup_period}_archived.json"
    payload = dict(stats)
    payload["backed_up_at_kst"] = at.isoformat()
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    prune_stats_history()


def prune_stats_history(max_files: int | None = None) -> int:
    """history/*.json 상한 초과분 삭제. 반환: 삭제한 파일 수."""
    if max_files is None:
        raw = os.environ.get("AI_STATS_HISTORY_MAX_FILES", "6").strip()
        try:
            max_files = max(1, int(float(raw)))
        except ValueError:
            max_files = 6
    history_dir = _history_dir()
    if not history_dir.exists():
        return 0
    files = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[max_files:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _normalize_stats(
    data: Dict[str, Any],
    *,
    initial_balance_krw: float,
    initial_balance_usdt: float,
    now_kst: datetime,
) -> Dict[str, Any]:
    stats = default_trading_stats(
        initial_balance_krw=initial_balance_krw,
        initial_balance_usdt=initial_balance_usdt,
        now_kst=now_kst,
    )
    legacy_profit = _safe_float(data.get("cumulative_profit_krw", 0.0))
    stats["period"] = str(data.get("period", stats["period"]))
    stats["last_reset_at_kst"] = str(data.get("last_reset_at_kst", stats["last_reset_at_kst"]))
    stats["initial_balance_krw"] = _safe_float(data.get("initial_balance_krw", initial_balance_krw), initial_balance_krw)
    stats["initial_balance_usdt"] = _safe_float(data.get("initial_balance_usdt", initial_balance_usdt), initial_balance_usdt)
    stats["virtual_balance_krw"] = _safe_float(
        data.get("virtual_balance_krw", stats["initial_balance_krw"] + legacy_profit),
        stats["initial_balance_krw"] + legacy_profit,
    )
    stats["virtual_balance_usdt"] = _safe_float(
        data.get("virtual_balance_usdt", stats["initial_balance_usdt"]),
        stats["initial_balance_usdt"],
    )
    stats["total_realized_pnl_krw"] = _safe_float(data.get("total_realized_pnl_krw", legacy_profit), legacy_profit)
    stats["total_realized_pnl_usdt"] = _safe_float(data.get("total_realized_pnl_usdt", 0.0))
    stats["monthly_realized_pnl_krw"] = _safe_float(data.get("monthly_realized_pnl_krw", legacy_profit), legacy_profit)
    stats["monthly_realized_pnl_usdt"] = _safe_float(data.get("monthly_realized_pnl_usdt", 0.0))
    stats["estimated_daily_server_cost_krw"] = _safe_float(data.get("estimated_daily_server_cost_krw", 0.0))
    stats["monthly_subscription_cost_krw"] = _safe_float(data.get("monthly_subscription_cost_krw", 0.0))
    stats["cumulative_profit_krw"] = _safe_float(data.get("cumulative_profit_krw", stats["total_realized_pnl_krw"]))
    stats["run_count"] = _safe_int(data.get("run_count", 0))
    stats["trade_count"] = _safe_int(data.get("trade_count", 0))
    stats["win_count"] = _safe_int(data.get("win_count", 0))
    stats["loss_count"] = _safe_int(data.get("loss_count", 0))
    stats["monthly_trade_count"] = _safe_int(data.get("monthly_trade_count", stats["trade_count"]))
    stats["monthly_win_count"] = _safe_int(data.get("monthly_win_count", stats["win_count"]))
    stats["monthly_loss_count"] = _safe_int(data.get("monthly_loss_count", stats["loss_count"]))
    stats["unique_failure_count"] = _safe_int(data.get("unique_failure_count", 0))
    stats["last_reflection_summary"] = str(data.get("last_reflection_summary", ""))
    stats["last_cycle_at_kst"] = str(data.get("last_cycle_at_kst", ""))
    stats["last_trade_closed_at_kst"] = str(data.get("last_trade_closed_at_kst", ""))
    stats["last_report_at_kst"] = str(data.get("last_report_at_kst", ""))
    stats["ai_entry_calls"] = _safe_int(data.get("ai_entry_calls", 0))
    stats["ai_monitor_calls"] = _safe_int(data.get("ai_monitor_calls", 0))
    stats["ai_calls_saved_by_gate"] = _safe_int(data.get("ai_calls_saved_by_gate", 0))
    stats["estimated_tokens_saved"] = _safe_int(data.get("estimated_tokens_saved", 0))
    open_position = data.get("open_position")
    stats["open_position"] = open_position if isinstance(open_position, dict) else None
    return stats


def _maybe_monthly_reset(stats: Dict[str, Any], now_kst: datetime) -> Tuple[Dict[str, Any], bool]:
    current_period = _current_period(now_kst)
    stored_period = str(stats.get("period", current_period))
    cutoff = _monthly_reset_cutoff(now_kst)
    if now_kst >= cutoff and stored_period != current_period:
        _backup_stats(stats, backup_period=stored_period, archived_at_kst=now_kst)
        stats["period"] = current_period
        stats["monthly_realized_pnl_krw"] = 0.0
        stats["monthly_realized_pnl_usdt"] = 0.0
        stats["monthly_trade_count"] = 0
        stats["monthly_win_count"] = 0
        stats["monthly_loss_count"] = 0
        stats["ai_entry_calls"] = 0
        stats["ai_monitor_calls"] = 0
        stats["ai_calls_saved_by_gate"] = 0
        stats["estimated_tokens_saved"] = 0
        stats["last_reset_at_kst"] = now_kst.isoformat()
        return stats, True
    if "period" not in stats:
        stats["period"] = current_period
    return stats, False


def load_trading_stats(
    path: Path | None = None,
    *,
    initial_balance_krw: float = 500_000.0,
    initial_balance_usdt: float = 362.0,
) -> Dict[str, Any]:
    p = path or _stats_path()
    now_kst = datetime.now(KST)
    if not p.exists():
        return default_trading_stats(
            initial_balance_krw=initial_balance_krw,
            initial_balance_usdt=initial_balance_usdt,
            now_kst=now_kst,
        )
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default_trading_stats(
                initial_balance_krw=initial_balance_krw,
                initial_balance_usdt=initial_balance_usdt,
                now_kst=now_kst,
            )
        normalized = _normalize_stats(
            data,
            initial_balance_krw=initial_balance_krw,
            initial_balance_usdt=initial_balance_usdt,
            now_kst=now_kst,
        )
        stats, did_reset = _maybe_monthly_reset(normalized, now_kst)
        if did_reset:
            save_trading_stats(stats, p)
        return stats
    except Exception:
        return default_trading_stats(
            initial_balance_krw=initial_balance_krw,
            initial_balance_usdt=initial_balance_usdt,
            now_kst=now_kst,
        )


def save_trading_stats(stats: Dict[str, Any], path: Path | None = None) -> None:
    p = path or _stats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(stats)
    payload["cumulative_profit_krw"] = _safe_float(payload.get("total_realized_pnl_krw", payload.get("cumulative_profit_krw", 0.0)))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def resolve_report_balances(
    stats: Dict[str, Any],
    *,
    dry_run: bool,
    live_futures_usdt: float | None,
    krw_per_usdt: float,
) -> Tuple[float, float, float]:
    """
    카카오 리포트 등에 쓰는 잔고(원/USDT)와 누적 수익률(%).
    실전(dry_run=False)이고 선물 지갑 USDT 조회에 성공하면 API 잔고를 사용한다.
    """
    initial_krw = _safe_float(stats.get("initial_balance_krw", 0.0))
    initial_usdt = _safe_float(stats.get("initial_balance_usdt", 0.0))
    use_live = (not dry_run) and live_futures_usdt is not None and live_futures_usdt > 0 and krw_per_usdt > 0
    if use_live:
        balance_usdt = float(live_futures_usdt)
        balance_krw = balance_usdt * krw_per_usdt
    else:
        balance_krw = _safe_float(stats.get("virtual_balance_krw", initial_krw))
        balance_usdt = _safe_float(stats.get("virtual_balance_usdt", initial_usdt))
    total_profit_pct = ((balance_krw - initial_krw) / initial_krw * 100.0) if initial_krw > 0 else 0.0
    return balance_krw, balance_usdt, total_profit_pct


def summarize_ledger(stats: Dict[str, Any]) -> LedgerSummary:
    initial_krw = _safe_float(stats.get("initial_balance_krw", 0.0))
    initial_usdt = _safe_float(stats.get("initial_balance_usdt", 0.0))
    balance_krw = _safe_float(stats.get("virtual_balance_krw", initial_krw))
    balance_usdt = _safe_float(stats.get("virtual_balance_usdt", initial_usdt))
    total_profit_krw = balance_krw - initial_krw
    total_profit_usdt = balance_usdt - initial_usdt
    total_profit_pct = (total_profit_krw / initial_krw * 100.0) if initial_krw > 0 else 0.0
    estimated_daily_server_cost_krw = _safe_float(stats.get("estimated_daily_server_cost_krw", 0.0))
    monthly_subscription_cost_krw = _safe_float(stats.get("monthly_subscription_cost_krw", 0.0))
    monthly_operating_cost_krw = estimated_daily_server_cost_krw * 30.0 + monthly_subscription_cost_krw
    monthly_net_profit_krw = _safe_float(stats.get("monthly_realized_pnl_krw", 0.0)) - monthly_operating_cost_krw
    return LedgerSummary(
        balance_krw=balance_krw,
        balance_usdt=balance_usdt,
        total_profit_krw=total_profit_krw,
        total_profit_usdt=total_profit_usdt,
        total_profit_pct=total_profit_pct,
        monthly_profit_krw=_safe_float(stats.get("monthly_realized_pnl_krw", 0.0)),
        estimated_daily_server_cost_krw=estimated_daily_server_cost_krw,
        monthly_subscription_cost_krw=monthly_subscription_cost_krw,
        monthly_operating_cost_krw=monthly_operating_cost_krw,
        monthly_net_profit_krw=monthly_net_profit_krw,
        trade_count=_safe_int(stats.get("trade_count", 0)),
        win_count=_safe_int(stats.get("win_count", 0)),
        loss_count=_safe_int(stats.get("loss_count", 0)),
        unique_failure_count=_safe_int(stats.get("unique_failure_count", 0)),
        last_reflection_summary=str(stats.get("last_reflection_summary", "")).strip(),
    )


def build_goal_report(realized_profit_krw: float, monthly_target_krw: int = 100_000) -> SubscriptionGoalReport:
    if monthly_target_krw <= 0:
        monthly_target_krw = 100_000
    stats = load_trading_stats()
    stats["total_realized_pnl_krw"] = _safe_float(stats.get("total_realized_pnl_krw", 0.0)) + realized_profit_krw
    stats["monthly_realized_pnl_krw"] = _safe_float(stats.get("monthly_realized_pnl_krw", 0.0)) + realized_profit_krw
    stats["virtual_balance_krw"] = _safe_float(stats.get("virtual_balance_krw", stats.get("initial_balance_krw", 0.0))) + realized_profit_krw
    stats["cumulative_profit_krw"] = _safe_float(stats.get("total_realized_pnl_krw", 0.0))
    stats["run_count"] = _safe_int(stats.get("run_count", 0)) + 1
    save_trading_stats(stats)
    achievement = max(0.0, _safe_float(stats.get("monthly_realized_pnl_krw", 0.0)) / monthly_target_krw * 100)
    status = "달성" if achievement >= 100 else "진행중"
    return SubscriptionGoalReport(
        monthly_target_krw=monthly_target_krw,
        realized_profit_krw=_safe_float(stats.get("monthly_realized_pnl_krw", 0.0)),
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


def build_monthly_pnl_display(
    stats: Dict[str, Any],
    *,
    balance_krw: float,
) -> MonthlyPnlDisplay:
    """
    총 평가금(잔고)이 초기 원금 미만이면 '원금 복구 중'으로 표기하고
    월 실현손익·순수익을 0원(실질)으로 보여 준다.
    """
    initial = _safe_float(stats.get("initial_balance_krw", 500_000.0), 500_000.0)
    raw_monthly = _safe_float(stats.get("monthly_realized_pnl_krw", 0.0))
    est_daily = _safe_float(stats.get("estimated_daily_server_cost_krw", 0.0))
    sub_monthly = _safe_float(stats.get("monthly_subscription_cost_krw", 0.0))
    op_cost = est_daily * 30.0 + sub_monthly
    recovering = initial > 0 and balance_krw < initial

    if recovering:
        shortfall = initial - balance_krw
        ref_note = f"체결 실현손익(참고): {raw_monthly:+,.0f}원"
        monthly_line = (
            f"📌 원금 복구 중 — 잔고 {balance_krw:,.0f}원 / 기준 {initial:,.0f}원 "
            f"({shortfall:,.0f}원 부족)\n"
            f"   {ref_note} → 실질 월 실현손익: 0원"
        )
        net_line = f"월 순수익(Net): 0원 (잔고 복구 중 · 운영비 {op_cost:,.0f}원 별도)"
        return MonthlyPnlDisplay(
            principal_recovering=True,
            status_label="원금 복구 중",
            initial_balance_krw=initial,
            balance_krw=balance_krw,
            shortfall_krw=shortfall,
            monthly_realized_raw_krw=raw_monthly,
            monthly_profit_display_krw=0.0,
            monthly_net_display_krw=0.0,
            monthly_operating_cost_krw=op_cost,
            monthly_line=monthly_line,
            net_line=net_line,
        )

    net = raw_monthly - op_cost
    monthly_line = f"월 실현손익: {raw_monthly:,.0f}원 / 월 운영비: {op_cost:,.0f}원"
    net_line = f"월 순수익(Net): {net:,.0f}원"
    return MonthlyPnlDisplay(
        principal_recovering=False,
        status_label="수익 구간",
        initial_balance_krw=initial,
        balance_krw=balance_krw,
        shortfall_krw=0.0,
        monthly_realized_raw_krw=raw_monthly,
        monthly_profit_display_krw=raw_monthly,
        monthly_net_display_krw=net,
        monthly_operating_cost_krw=op_cost,
        monthly_line=monthly_line,
        net_line=net_line,
    )
