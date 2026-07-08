"""오늘 카카오·일일리포트 변경분 로컬 검증 (토큰/네트워크 불필요)."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "btc_live_trading"))
sys.path.insert(0, str(ROOT / "ai_trading"))

KST = timezone(timedelta(hours=9))
FAILURES: list[str] = []


def ok(name: str) -> None:
    print(f"  OK  {name}")


def fail(name: str, detail: str) -> None:
    FAILURES.append(f"{name}: {detail}")
    print(f"  FAIL {name} - {detail}")


def test_daily_trade_summary() -> None:
    import main_ai as m

    now = datetime(2026, 6, 10, 20, 0, tzinfo=KST)
    yesterday = (now - timedelta(days=1)).replace(hour=10, minute=0)
    today_open = now.replace(hour=9, minute=15)
    today_close = now.replace(hour=11, minute=30)

    lines = [
        json.dumps(
            {
                "event": "open",
                "logged_at_kst": yesterday.isoformat(),
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entry_price": 78000,
                "order_mode": "paper",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "event": "open",
                "logged_at_kst": today_open.isoformat(),
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entry_price": 79000,
                "order_mode": "live",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "event": "close",
                "exit_at_kst": today_close.isoformat(),
                "symbol": "BTCUSDT",
                "side": "BUY",
                "exit_price": 79500,
                "pnl_krw": 12000,
                "pnl_usdt": 8.5,
                "exit_reason": "take_profit",
            },
            ensure_ascii=False,
        ),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        trade_log = Path(tmp) / "virtual_trades.jsonl"
        trade_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with patch.object(m, "TRADE_LOG_PATH", trade_log):
            summary = m._format_daily_trade_summary(now)
            events = m._load_today_trade_events(now)

    if len(events) != 2:
        fail("daily_trade_summary", f"expected 2 today events, got {len(events)}")
        return
    if "09:15" not in summary or "매수 진입" not in summary:
        fail("daily_trade_summary", f"missing open line: {summary!r}")
        return
    if "11:30" not in summary or "청산" not in summary or "take_profit" not in summary:
        fail("daily_trade_summary", f"missing close line: {summary!r}")
        return
    if "78000" in summary:
        fail("daily_trade_summary", "yesterday event leaked into summary")
        return
    ok("daily_trade_summary filters today only and formats open/close")


def test_periodic_report_once_per_day() -> None:
    import main_ai as m

    now = datetime(2026, 6, 10, 8, 0, tzinfo=KST)
    stats: dict = {}
    if not m._should_send_periodic_report(stats, now, 1440):
        fail("periodic_report", "should allow first report of day")
        return
    stats = {"last_report_at_kst": (now - timedelta(days=1)).isoformat()}
    if not m._should_send_periodic_report(stats, now, 1440):
        fail("periodic_report", "should allow when last report was yesterday")
        return
    stats["last_kakao_sent_date_kst"] = "2026-06-10"
    if m._should_send_periodic_report(stats, now, 1440):
        fail("periodic_report", "should block after kakao sent today")
        return
    ok("periodic_report once per KST day (1440 min)")


def test_notify_kakao_gating() -> None:
    import main_ai as m

    sent: list[str] = []

    class _Core:
        def send_message(self, title: str, body: str) -> bool:
            sent.append(title)
            return True

    with patch.object(m, "_kakao_alerts_enabled", return_value=True), patch.object(
        m, "_env_str", side_effect=lambda k, d="": "key" if k == "KAKAO_REST_API_KEY" else d
    ), patch.object(m, "KakaoNotifier", object()), patch.object(m, "_hydrate_kakao_tokens", lambda: None), patch.object(
        m, "_kakao_once_per_day_enabled", return_value=True
    ), patch.object(m, "_kakao_already_sent_today", return_value=False), patch.object(
        m, "_ensure_async_kakao_started", lambda: None
    ), patch.object(m, "_ASYNC_KAKAO", None), patch.object(
        m, "_build_kakao_notifier_core", _Core
    ), patch.object(m, "_mark_kakao_sent_today", lambda *a, **k: None):
        blocked = m._notify_kakao("startup", "body")
        digest = m._notify_kakao("digest", "body", daily_digest=True)
        refresh = m._notify_kakao("refresh", "body", exempt_daily_limit=True)

    if blocked is not False:
        fail("notify_gating", "non-digest should be blocked")
        return
    if digest is not True or refresh is not True:
        fail("notify_gating", "digest/refresh exempt should send")
        return
    if sent != ["digest", "refresh"]:
        fail("notify_gating", f"expected ['digest','refresh'], got {sent}")
        return
    ok("notify_kakao daily_digest + exempt_daily_limit gating")


def test_refresh_listener() -> None:
    from kakao_utils import register_kakao_token_refresh_listener, _notify_kakao_token_http_refreshed

    fired = {"n": 0}

    def cb() -> None:
        fired["n"] += 1

    register_kakao_token_refresh_listener(cb)
    _notify_kakao_token_http_refreshed()
    register_kakao_token_refresh_listener(None)

    if fired["n"] != 1:
        fail("refresh_listener", f"callback count={fired['n']}")
        return
    ok("register_kakao_token_refresh_listener fires on HTTP refresh hook")


def test_build_kakao_message_has_trade_section() -> None:
    import main_ai as m

    now = datetime(2026, 6, 10, 20, 0, tzinfo=KST)
    with patch.object(m, "_format_daily_trade_summary", return_value="· 09:15 매수 진입 BTCUSDT @ 79,000"):
        msg = m._build_kakao_message(
            report={
                "decision": "HOLD",
                "confidence": 0.5,
                "reason": "test",
                "risk_allowed": True,
                "market": {"symbol": "BTCUSDT", "interval": "15m", "price": 79000},
                "monitor_model": "gpt-4o-mini",
                "monitor_summary": "",
            },
            stats={"virtual_balance_krw": 500000, "virtual_balance_usdt": 362},
            learning_summary="",
            close_info=None,
            open_position=None,
            dry_run=True,
            live_futures_usdt=None,
            krw_per_usdt=1380,
            now_kst=now,
        )
    if "📅 [오늘 매매 내역 (KST)]" not in msg:
        fail("build_kakao_message", "missing trade section")
        return
    if "09:15 매수 진입" not in msg:
        fail("build_kakao_message", "trade summary not embedded")
        return
    ok("build_kakao_message includes daily trade section")


def main() -> int:
    print("=== verify_today_changes ===")
    test_daily_trade_summary()
    test_periodic_report_once_per_day()
    test_notify_kakao_gating()
    test_refresh_listener()
    test_build_kakao_message_has_trade_section()
    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
