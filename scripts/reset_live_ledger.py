#!/usr/bin/env python3
"""
실전 전환 시 가상(Paper) 원장 누적을 제거하고, Binance USDT-M 선물 실잔고를 기준으로
`ai_trading/data/trading_stats.json` 수익률·PnL 집계를 0부터 다시 시작한다.

`unique_failure_count`(오답 노트 건수)와 운영비 설정·최근 반성 요약은 유지한다.

  py -3 scripts\\reset_live_ledger.py
  py -3 scripts\\reset_live_ledger.py --yes
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI_DIR = ROOT / "ai_trading"
LIVE_DIR = ROOT / "btc_live_trading"
STATS_PATH = AI_DIR / "data" / "trading_stats.json"
HISTORY_DIR = AI_DIR / "data" / "history"
KST = timezone(timedelta(hours=9))

logger = logging.getLogger("reset_live_ledger")


def _bootstrap_path() -> None:
    for p in (str(ROOT), str(AI_DIR), str(LIVE_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _load_env() -> bool:
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.error("python-dotenv 필요: pip install python-dotenv")
        return False
    env_path = LIVE_DIR / ".env"
    if not env_path.exists():
        logger.error(".env 없음: %s", env_path)
        return False
    load_dotenv(env_path)
    return True


def _krw_per_usdt() -> float:
    try:
        from btc_live_trading.fx_rates import fetch_usdt_krw

        rate = fetch_usdt_krw()
        if rate and rate > 0:
            return float(rate)
    except Exception as exc:
        logger.warning("USDT/KRW 조회 실패, 1380 가정: %s", exc)
    return 1380.0


def _fetch_live_usdt() -> float | None:
    try:
        from binance_futures_tools import fetch_futures_usdt_balance_from_env

        return fetch_futures_usdt_balance_from_env()
    except ImportError as exc:
        logger.error("binance_futures_tools 로드 실패: %s", exc)
        return None


def _open_exchange_positions() -> list[str]:
    try:
        from binance_futures_tools import futures_client_from_env, futures_open_position_rows

        client = futures_client_from_env()
        if client is None:
            return []
        rows = futures_open_position_rows(client)
        return [str(r.get("symbol", "")) for r in rows if abs(float(r.get("positionAmt", 0) or 0)) > 0]
    except Exception:
        return []


def _backup_stats(stats: dict, *, archived_at: datetime) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = archived_at.strftime("%Y%m%d_%H%M%S")
    period = str(stats.get("period", archived_at.strftime("%Y-%m")))
    path = HISTORY_DIR / f"trading_stats_pre_live_reset_{period}_{ts}_kst.json"
    payload = dict(stats)
    payload["backed_up_at_kst"] = archived_at.isoformat()
    payload["backup_reason"] = "reset_live_ledger"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def _build_reset_stats(old: dict, *, live_usdt: float, krw_per_usdt: float, now_kst: datetime) -> dict:
    balance_krw = live_usdt * krw_per_usdt
    preserved = {
        "unique_failure_count": int(old.get("unique_failure_count", 0) or 0),
        "estimated_daily_server_cost_krw": float(old.get("estimated_daily_server_cost_krw", 0.0) or 0.0),
        "monthly_subscription_cost_krw": float(old.get("monthly_subscription_cost_krw", 0.0) or 0.0),
        "last_reflection_summary": str(old.get("last_reflection_summary", "") or ""),
    }
    stats = {
        "period": now_kst.strftime("%Y-%m"),
        "last_reset_at_kst": now_kst.isoformat(),
        "initial_balance_krw": balance_krw,
        "initial_balance_usdt": live_usdt,
        "virtual_balance_krw": balance_krw,
        "virtual_balance_usdt": live_usdt,
        "total_realized_pnl_krw": 0.0,
        "total_realized_pnl_usdt": 0.0,
        "monthly_realized_pnl_krw": 0.0,
        "monthly_realized_pnl_usdt": 0.0,
        "cumulative_profit_krw": 0.0,
        "run_count": 0,
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "monthly_trade_count": 0,
        "monthly_win_count": 0,
        "monthly_loss_count": 0,
        "unique_failure_count": preserved["unique_failure_count"],
        "last_reflection_summary": preserved["last_reflection_summary"],
        "last_cycle_at_kst": "",
        "last_trade_closed_at_kst": "",
        "last_report_at_kst": "",
        "ai_entry_calls": 0,
        "ai_monitor_calls": 0,
        "ai_calls_saved_by_gate": 0,
        "estimated_tokens_saved": 0,
        "open_position": None,
        "estimated_daily_server_cost_krw": preserved["estimated_daily_server_cost_krw"],
        "monthly_subscription_cost_krw": preserved["monthly_subscription_cost_krw"],
        "live_ledger_reset_at_kst": now_kst.isoformat(),
        "live_ledger_reset_usdt": live_usdt,
    }
    return stats


def _print_summary(before: dict, after: dict, *, backup_path: Path, live_usdt: float, krw_per_usdt: float) -> None:
    print("\n" + "=" * 60)
    print("실전 원장 리셋 완료")
    print("=" * 60)
    print(f"백업: {backup_path}")
    print(f"Binance 실잔고: {live_usdt:,.4f} USDT  (≈ {live_usdt * krw_per_usdt:,.0f} KRW @ {krw_per_usdt:,.2f})")
    print(f"유지 unique_failure_count: {after['unique_failure_count']}")
    print("--- 이전 → 이후 ---")
    print(f"  virtual_balance_usdt: {before.get('virtual_balance_usdt')} → {after['virtual_balance_usdt']}")
    print(f"  monthly_realized_pnl_krw: {before.get('monthly_realized_pnl_krw')} → 0")
    print(f"  total_realized_pnl_krw: {before.get('total_realized_pnl_krw')} → 0")
    print(f"  trade_count: {before.get('trade_count')} → 0")
    print("=" * 60 + "\n")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="실전 원장(trading_stats.json) PnL 초기화")
    parser.add_argument("--yes", "-y", action="store_true", help="확인 없이 실행")
    args = parser.parse_args()

    _bootstrap_path()
    if not _load_env():
        return 1

    dry_run = str(__import__("os").environ.get("AI_DRY_RUN", "true")).strip().lower() in {"1", "true", "yes", "on"}
    if dry_run:
        logger.warning("AI_DRY_RUN=true 입니다. 실전 전환 후 리셋하는 것을 권장합니다.")

    live_usdt = _fetch_live_usdt()
    if live_usdt is None or live_usdt <= 0:
        logger.error("Binance USDT-M 선물 잔고를 조회할 수 없습니다. API 키와 네트워크를 확인하세요.")
        return 1

    open_syms = _open_exchange_positions()
    if open_syms:
        logger.error("거래소에 열린 포지션이 있습니다: %s — 청산 후 다시 실행하세요.", ", ".join(open_syms))
        return 2

    krw_per_usdt = _krw_per_usdt()
    now_kst = datetime.now(KST)

    if STATS_PATH.exists():
        with open(STATS_PATH, "r", encoding="utf-8") as f:
            before = json.load(f)
        if not isinstance(before, dict):
            before = {}
    else:
        before = {}

    if not args.yes:
        print("\n⚠️  trading_stats.json 의 PnL·거래 집계를 0으로 리셋합니다.")
        print(f"    기준 잔고: {live_usdt:,.4f} USDT")
        print(f"    유지: unique_failure_count={before.get('unique_failure_count', 0)}")
        answer = input("계속하려면 yes 입력: ").strip().lower()
        if answer not in {"yes", "y"}:
            print("취소되었습니다.")
            return 0

    backup_path = _backup_stats(before, archived_at=now_kst)
    after = _build_reset_stats(before, live_usdt=live_usdt, krw_per_usdt=krw_per_usdt, now_kst=now_kst)
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(after, f, ensure_ascii=False, indent=2)

    _print_summary(before, after, backup_path=backup_path, live_usdt=live_usdt, krw_per_usdt=krw_per_usdt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
