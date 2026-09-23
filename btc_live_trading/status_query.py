"""
매매 상태 읽기 전용 스냅샷.

main_ai.run_cycle / 주문·청산 함수를 절대 호출하지 않는다.
잔고는 reporting.resolve_report_balances 와 동일 규칙:
실전(AI_DRY_RUN=false)이면 선물 지갑 조회, 페이퍼면 virtual_balance_*.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_MODULE_DIR = Path(__file__).resolve().parent
_ROOT = _MODULE_DIR.parent
_AI = _ROOT / "ai_trading"
if str(_AI) not in sys.path:
    sys.path.insert(0, str(_AI))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reporting import (  # noqa: E402
    load_trading_stats,
    resolve_report_balances,
    summarize_ledger,
)

try:
    from binance_futures_tools import fetch_futures_usdt_balance_from_env as _fetch_futures_usdt
except ImportError:
    _fetch_futures_usdt = None  # type: ignore[assignment]

try:
    from fx_rates import fetch_usdt_krw as _fetch_usdt_krw
except ImportError:
    _fetch_usdt_krw = None  # type: ignore[assignment]

KST = timezone(timedelta(hours=9))
TRADE_LOG_PATH = _AI / "data" / "virtual_trades.jsonl"


def _env_bool(key: str, default: bool = False) -> bool:
    raw = (os.getenv(key) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _env_str(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _parse_kst(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except (TypeError, ValueError):
        return None


def _mask_secret(value: str, *, keep: int = 8) -> str:
    v = (value or "").strip()
    if not v:
        return "(없음)"
    if len(v) <= keep:
        return f"{v[:2]}…(len={len(v)})"
    return f"{v[:keep]}…(len={len(v)})"


def _today_realized_from_trades(now_kst: datetime) -> tuple[float, float]:
    """virtual_trades.jsonl 에서 오늘(KST) 청산 실현손익 합(읽기 전용). 없으면 0."""
    if not TRADE_LOG_PATH.exists():
        return 0.0, 0.0
    day = now_kst.date().isoformat()
    pnl_krw = 0.0
    pnl_usdt = 0.0
    try:
        with TRADE_LOG_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if str(row.get("event") or "").lower() != "close":
                    continue
                closed_raw = str(row.get("exit_at_kst") or row.get("logged_at_kst") or "")
                dt = _parse_kst(closed_raw)
                if dt is None or dt.date().isoformat() != day:
                    continue
                try:
                    pnl_krw += float(row.get("pnl_krw") or row.get("realized_pnl_krw") or 0.0)
                except (TypeError, ValueError):
                    pass
                try:
                    pnl_usdt += float(row.get("pnl_usdt") or row.get("realized_pnl_usdt") or 0.0)
                except (TypeError, ValueError):
                    pass
    except OSError:
        return 0.0, 0.0
    return pnl_krw, pnl_usdt


def _notify_channel_status() -> dict[str, Any]:
    channel = (_env_str("NOTIFY_CHANNEL", "discord") or "discord").lower()
    if channel in {"kakao", "kakaotalk"}:
        channel = "kakao"
    else:
        channel = "discord"
    webhook = _env_str("DISCORD_WEBHOOK_URL", "")
    bot_token = _env_str("DISCORD_BOT_TOKEN", "")
    kakao_access = _env_str("KAKAO_ACCESS_TOKEN", "")
    kakao_refresh = _env_str("KAKAO_REFRESH_TOKEN", "")
    return {
        "notify_channel": channel,
        "discord_webhook_configured": bool(webhook),
        "discord_webhook_masked": _mask_secret(webhook, keep=24) if webhook else "(없음)",
        "discord_bot_token_configured": bool(bot_token),
        "kakao_access_present": bool(kakao_access) and kakao_access != "your_access_token_here",
        "kakao_refresh_present": bool(kakao_refresh) and kakao_refresh != "your_refresh_token_here",
    }


def _krw_per_usdt() -> float:
    if _fetch_usdt_krw is not None:
        try:
            rate = float(_fetch_usdt_krw())
            if rate > 0:
                return rate
        except Exception:
            pass
    return 1380.0


def _live_futures_usdt() -> float | None:
    """main_ai 실전 리포트와 동일한 선물 지갑 조회. 실패 시 None."""
    if _fetch_futures_usdt is None:
        return None
    try:
        bal = _fetch_futures_usdt()
    except Exception:
        return None
    if bal is None:
        return None
    try:
        value = float(bal)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def build_status_snapshot() -> dict[str, Any]:
    """원장·환경변수(+실전일 때 선물 잔고) 읽기 전용 스냅샷. 매매 호출 없음."""
    now_kst = datetime.now(KST)
    dry_run = _env_bool("AI_DRY_RUN", True)
    loop_seconds = max(1, _env_int("AI_LOOP_SECONDS", 300))

    initial_krw = float(_env_str("AI_VIRTUAL_INITIAL_KRW", "500000") or 500000)
    initial_usdt = float(_env_str("AI_VIRTUAL_INITIAL_USDT", "362") or 362)
    try:
        stats = load_trading_stats(
            initial_balance_krw=initial_krw,
            initial_balance_usdt=initial_usdt,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"stats_load_failed:{type(exc).__name__}",
            "checked_at_kst": now_kst.isoformat(timespec="seconds"),
        }

    summary = summarize_ledger(stats)
    live_usdt = None if dry_run else _live_futures_usdt()
    krw_per_usdt = _krw_per_usdt() if not dry_run else 1.0
    balance_krw, balance_usdt, total_profit_pct = resolve_report_balances(
        stats,
        dry_run=dry_run,
        live_futures_usdt=live_usdt,
        krw_per_usdt=krw_per_usdt,
    )
    used_live = (
        (not dry_run)
        and live_usdt is not None
        and live_usdt > 0
        and krw_per_usdt > 0
    )
    balance_source = "binance_futures" if used_live else "virtual_ledger"

    open_pos = stats.get("open_position") if isinstance(stats.get("open_position"), dict) else None
    position: dict[str, Any] | None = None
    if open_pos:
        position = {
            "side": str(open_pos.get("side") or "").upper(),
            "symbol": str(open_pos.get("symbol") or ""),
            "entry_price": open_pos.get("entry_price"),
            "stop_loss": open_pos.get("stop_loss"),
            "take_profit": open_pos.get("take_profit"),
            "position_size": open_pos.get("position_size"),
            "opened_at_kst": str(open_pos.get("opened_at_kst") or ""),
            "expires_at_kst": str(open_pos.get("expires_at_kst") or ""),
            "hold_extensions": open_pos.get("hold_extensions", 0),
        }

    last_cycle_raw = str(stats.get("last_cycle_at_kst") or "")
    last_cycle_dt = _parse_kst(last_cycle_raw)
    lag_seconds: float | None = None
    if last_cycle_dt is not None:
        lag_seconds = max(0.0, (now_kst - last_cycle_dt).total_seconds())

    today_krw, today_usdt = _today_realized_from_trades(now_kst)
    notify = _notify_channel_status()

    return {
        "ok": True,
        "checked_at_kst": now_kst.isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "mode_label": "가상(DRY_RUN)" if dry_run else "실전",
        "loop_seconds": loop_seconds,
        "last_cycle_at_kst": last_cycle_raw or "(없음)",
        "cycle_lag_seconds": lag_seconds,
        "position": position,
        "has_position": position is not None,
        "balance_krw": balance_krw,
        "balance_usdt": balance_usdt,
        "balance_source": balance_source,
        "live_futures_usdt": live_usdt,
        "krw_per_usdt": krw_per_usdt,
        "total_profit_krw": summary.total_profit_krw,
        "total_profit_usdt": summary.total_profit_usdt,
        "total_profit_pct": total_profit_pct,
        "monthly_profit_krw": summary.monthly_profit_krw,
        "monthly_profit_usdt": float(stats.get("monthly_realized_pnl_usdt") or 0.0),
        "today_realized_pnl_krw": today_krw,
        "today_realized_pnl_usdt": today_usdt,
        "trade_count": summary.trade_count,
        "win_count": summary.win_count,
        "loss_count": summary.loss_count,
        "run_count": int(stats.get("run_count") or 0),
        "notify": notify,
    }


def assess_cycle_health(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """last_cycle_at_kst 기준으로 정상/지연 의심만 판단 (systemctl 미사용)."""
    snap = snapshot if isinstance(snapshot, dict) else build_status_snapshot()
    loop_seconds = max(1, int(snap.get("loop_seconds") or _env_int("AI_LOOP_SECONDS", 300)))
    lag = snap.get("cycle_lag_seconds")
    threshold = float(loop_seconds) * 3.0
    if lag is None:
        status = "unknown"
        detail = "last_cycle_at_kst 없음 (봇 미기동 또는 아직 사이클 미기록)"
    elif float(lag) <= threshold:
        status = "ok"
        detail = f"마지막 사이클 후 {float(lag):.0f}s (임계 {threshold:.0f}s=루프×3)"
    else:
        status = "delayed"
        detail = f"마지막 사이클 후 {float(lag):.0f}s — 임계 {threshold:.0f}s 초과 (지연 의심)"
    return {
        "status": status,
        "label": {"ok": "정상", "delayed": "지연 의심", "unknown": "확인 불가"}.get(status, status),
        "detail": detail,
        "loop_seconds": loop_seconds,
        "lag_seconds": lag,
        "threshold_seconds": threshold,
        "last_cycle_at_kst": snap.get("last_cycle_at_kst"),
    }
