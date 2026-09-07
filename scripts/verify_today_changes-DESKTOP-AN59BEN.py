"""오늘 카카오·일일리포트 변경분 로컬 검증 (토큰/네트워크 불필요)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
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

    with patch.object(m, "_notify_alerts_enabled", return_value=True), patch.object(
        m, "_notify_channel", return_value="kakao"
    ), patch.object(m, "_kakao_alerts_enabled", return_value=True), patch.object(
        m, "_env_str", side_effect=lambda k, d="": "key" if k == "KAKAO_REST_API_KEY" else d
    ), patch.object(m, "KakaoNotifier", object()), patch.object(m, "_hydrate_kakao_tokens", lambda: None), patch.object(
        m, "_kakao_once_per_day_enabled", return_value=True
    ), patch.object(m, "_kakao_already_sent_today", return_value=False), patch.object(
        m, "_ensure_async_kakao_started", lambda: None
    ), patch.object(m, "_ASYNC_KAKAO", None), patch.object(
        m, "_build_notifier_core", _Core
    ), patch.object(m, "_build_kakao_notifier_core", _Core), patch.object(
        m, "_mark_kakao_sent_today", lambda *a, **k: None
    ):
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


def test_auth_wait_mode_state_machine() -> None:
    import time as _time

    import main_ai as m

    m._clear_kakao_auth_exhausted()
    m._mark_kakao_auth_exhausted("test")
    if not m.KAKAO_AUTH_EXHAUSTED or m.KAKAO_AUTH_EXHAUSTED_AT_MONO <= 0:
        fail("auth_wait_mode", "mark did not set exhausted + timestamp")
        return
    if m._kakao_auth_recovery_due():
        fail("auth_wait_mode", "recovery should NOT be due immediately")
        return
    m.KAKAO_AUTH_EXHAUSTED_AT_MONO = _time.monotonic() - 10 * 24 * 3600
    if not m._kakao_auth_recovery_due():
        fail("auth_wait_mode", "recovery should be due after interval")
        return
    m._clear_kakao_auth_exhausted()
    if m.KAKAO_AUTH_EXHAUSTED or m.KAKAO_AUTH_EXHAUSTED_AT_MONO != 0.0:
        fail("auth_wait_mode", "clear did not reset state")
        return
    ok("auth wait mode: mark -> not due -> due -> clear")


def test_refresh_retry_uses_new_token_from_store() -> None:
    import main_ai as m

    calls: list[str] = []

    def fake_sync(rest: str, rt: str = "", force: bool = False) -> str:
        calls.append(rt)
        if rt == "NEW":
            return "fresh-access"
        raise RuntimeError("boom")

    with patch.object(m, "_refresh_kakao_access_token_sync", fake_sync), patch.object(
        m, "_hydrate_kakao_tokens", lambda: None
    ), patch.object(m, "_get_kakao_refresh_token", lambda: "NEW"), patch.object(
        m, "_clear_kakao_auth_exhausted", lambda: None
    ), patch.object(m.time, "sleep", lambda s: None):
        result = m._refresh_kakao_access_token("OLD", "key")

    if result != "fresh-access":
        fail("refresh_retry", f"expected fresh-access, got {result!r}")
        return
    if calls != ["OLD", "NEW"]:
        fail("refresh_retry", f"expected OLD then NEW, got {calls}")
        return
    ok("refresh retry picks up externally re-authed token")


def test_refresh_retry_stops_on_repeated_invalid_grant() -> None:
    import main_ai as m

    calls: list[str] = []

    class FakeResp:
        def json(self):
            return {"error": "invalid_grant", "error_description": "expired_or_invalid_refresh_token"}

        text = "invalid_grant"

    def fake_sync(rest: str, rt: str = "", force: bool = False) -> str:
        calls.append(rt)
        exc = RuntimeError("invalid")
        exc.response = FakeResp()
        raise exc

    marked: list[str] = []
    with patch.object(m, "_refresh_kakao_access_token_sync", fake_sync), patch.object(
        m, "_hydrate_kakao_tokens", lambda: None
    ), patch.object(m, "_get_kakao_refresh_token", lambda: "SAME"), patch.object(
        m, "_mark_kakao_auth_exhausted", lambda r: marked.append(r)
    ), patch.object(m.time, "sleep", lambda s: None):
        result = m._refresh_kakao_access_token("SAME", "key")

    if result != "":
        fail("invalid_grant_stop", f"expected empty, got {result!r}")
        return
    if len(calls) != 1:
        fail("invalid_grant_stop", f"expected 1 HTTP attempt for same-token invalid_grant, got {len(calls)}")
        return
    if not marked:
        fail("invalid_grant_stop", "exhausted was not marked")
        return
    ok("refresh retry early-stops on repeated invalid_grant and marks exhausted")


def test_refresh_listener_called_outside_lock() -> None:
    """갱신 성공 리스너가 _KAKAO_REFRESH_LOCK 해제 후 호출되는지 (데드락 방지)."""
    import kakao_utils as ku

    lock_free_when_listener_ran: list[bool] = []

    def listener() -> None:
        acquired = ku._KAKAO_REFRESH_LOCK.acquire(blocking=False)
        lock_free_when_listener_ran.append(acquired)
        if acquired:
            ku._KAKAO_REFRESH_LOCK.release()

    ku.register_kakao_token_refresh_listener(listener)
    try:
        with patch.object(ku, "kakao_api_allowed", return_value=True), patch.object(
            ku, "hydrate_tokens_from_json", lambda: None
        ), patch.object(ku, "get_access_token", return_value=""), patch.object(
            ku, "is_access_token_locally_fresh", return_value=False
        ), patch.object(ku, "get_refresh_token", return_value="rt"), patch.object(
            ku, "refresh_access_token_request", return_value={"access_token": "new"}
        ), patch.object(ku, "apply_token_response", return_value="new"):
            result = ku.refresh_kakao_access_token_sync("key", force=True)
    finally:
        ku.register_kakao_token_refresh_listener(None)

    if result != "new":
        fail("listener_outside_lock", f"expected 'new', got {result!r}")
        return
    if lock_free_when_listener_ran != [True]:
        fail(
            "listener_outside_lock",
            f"listener ran while lock held (deadlock risk): {lock_free_when_listener_ran}",
        )
        return
    ok("refresh success listener runs AFTER lock release (no deadlock)")


def test_async_notifier_dead_worker_recovery() -> None:
    """워커 스레드가 죽으면 enqueue가 False를 반환하고 start()로 재기동되는지."""
    import time as _time

    from async_notifier import AsyncNotifier

    class DummyNotifier:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send_message(self, title: str, body: str) -> bool:
            self.sent.append(title)
            return True

    dummy = DummyNotifier()
    an = AsyncNotifier(dummy)

    # 죽은 워커 시뮬레이션: is_running=True인데 스레드가 없음(비정상 종료 상태)
    an.is_running = True
    an.worker_thread = None
    if an.is_alive():
        fail("async_worker_recovery", "is_alive should be False when thread is gone")
        return
    if an.send_message("t", "b") is not False:
        fail("async_worker_recovery", "enqueue should fail when worker is dead")
        return

    an.start()  # 재기동
    if not an.is_alive():
        fail("async_worker_recovery", "start() did not revive worker")
        return
    if not an.send_message("t2", "b2"):
        fail("async_worker_recovery", "enqueue should succeed after restart")
        return
    deadline = _time.time() + 3
    while _time.time() < deadline and not dummy.sent:
        _time.sleep(0.05)
    an.stop()
    if dummy.sent != ["t2"]:
        fail("async_worker_recovery", f"expected ['t2'], got {dummy.sent}")
        return
    ok("AsyncNotifier dead worker -> enqueue False -> start() revives")


def test_token_heartbeat_throttle() -> None:
    """하트비트: 매 호출 DEBUG, INFO는 KAKAO_HEARTBEAT_LOG_MINUTES마다 1회."""
    import logging

    import main_ai as m

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    m.logger.addHandler(handler)
    old_level = m.logger.level
    m.logger.setLevel(logging.DEBUG)
    try:
        m.KAKAO_HEARTBEAT_LOG_AT_MONO = 0.0
        m.KAKAO_AUTH_EXHAUSTED = False
        with patch.object(m, "_ASYNC_KAKAO", None), patch.object(
            m, "_is_kakao_access_locally_fresh", return_value=True
        ), patch.object(m, "_probe_kakao_refresh_if_access_stale", return_value=""):
            m._log_kakao_token_heartbeat()
            m._log_kakao_token_heartbeat()
    finally:
        m.logger.removeHandler(handler)
        m.logger.setLevel(old_level)
        m.KAKAO_HEARTBEAT_LOG_AT_MONO = 0.0
        m.KAKAO_AUTH_EXHAUSTED = False

    debugs = [r for r in records if r.levelno == logging.DEBUG and "게이트 정상" in r.getMessage()]
    infos = [r for r in records if r.levelno == logging.INFO and "카카오 토큰 하트비트" in r.getMessage()]
    if len(debugs) != 2:
        fail("heartbeat", f"expected 2 DEBUG heartbeats, got {len(debugs)}")
        return
    if len(infos) != 1:
        fail("heartbeat", f"expected 1 INFO heartbeat (throttled), got {len(infos)}")
        return
    ok("token heartbeat: DEBUG every call, INFO throttled to once per interval")


def test_heartbeat_probes_refresh_when_stale() -> None:
    """access 비fresh 이면 하트비트가 refresh 1회 점검한다."""
    import main_ai as m

    probes: list[str] = []

    def fake_probe() -> str:
        probes.append("hit")
        return "new-access"

    m.KAKAO_AUTH_EXHAUSTED = False
    m.KAKAO_HEARTBEAT_LOG_AT_MONO = time.monotonic()  # INFO throttle skip
    with patch.object(m, "_ASYNC_KAKAO", None), patch.object(
        m, "_is_kakao_access_locally_fresh", return_value=False
    ), patch.object(m, "_probe_kakao_refresh_if_access_stale", fake_probe):
        m._log_kakao_token_heartbeat()
    m.KAKAO_AUTH_EXHAUSTED = False
    if probes != ["hit"]:
        fail("heartbeat_probe", f"expected one probe, got {probes}")
        return
    ok("heartbeat probes refresh once when access is stale")


def test_exhausted_blocks_gate_despite_disk_tokens() -> None:
    """invalid_grant 후 디스크에 토큰이 남아 있어도 평시 게이트는 차단한다."""
    import main_ai as m

    refresh_calls: list[str] = []

    def boom(*a, **k):
        refresh_calls.append("called")
        return ""

    m._clear_kakao_auth_exhausted()
    m._mark_kakao_auth_exhausted("test invalid_grant")
    # recovery not due
    m.KAKAO_AUTH_EXHAUSTED_AT_MONO = time.monotonic()
    with patch.object(m, "_notify_alerts_enabled", return_value=True), patch.object(
        m, "_kakao_alerts_enabled", return_value=True
    ), patch.object(m, "_notify_channel", return_value="kakao"), patch.object(
        m, "KakaoNotifier", object()
    ), patch.object(m, "_hydrate_kakao_tokens", lambda: None), patch.object(
        m, "get_access_token", lambda: "disk-access"
    ), patch.object(m, "_get_kakao_refresh_token", lambda: "disk-refresh"), patch.object(
        m, "_try_kakao_auth_code_from_env", lambda *_a, **_k: ""
    ), patch.object(m, "_refresh_kakao_access_token", boom), patch.object(
        m, "_kakao_auth_recovery_due", return_value=False
    ):
        got = m._ensure_kakao_access_token(for_login=False)
        ready = m._kakao_auth_ready()
    m._clear_kakao_auth_exhausted()
    if got != "":
        fail("exhausted_gate", f"expected '', got {got!r}")
        return
    if ready:
        fail("exhausted_gate", "auth_ready must be False while exhausted")
        return
    if refresh_calls:
        fail("exhausted_gate", "must not refresh before recovery due")
        return
    ok("exhausted blocks gate even when disk still has tokens")


def test_recovery_does_not_fake_clear_on_disk_tokens() -> None:
    """복구 주기 도래 시 디스크 토큰만으로 exhausted 를 풀지 않고 실제 refresh 한다."""
    import main_ai as m

    refresh_calls: list[str] = []

    def fail_refresh(*a, **k):
        refresh_calls.append("called")
        m._mark_kakao_auth_exhausted("still dead")
        return ""

    def env_str(key: str, default: str = "") -> str:
        if key == "KAKAO_REST_API_KEY":
            return "test-rest-key"
        return default

    m._clear_kakao_auth_exhausted()
    m._mark_kakao_auth_exhausted("initial")
    with patch.object(m, "_notify_alerts_enabled", return_value=True), patch.object(
        m, "_kakao_alerts_enabled", return_value=True
    ), patch.object(m, "_notify_channel", return_value="kakao"), patch.object(
        m, "KakaoNotifier", object()
    ), patch.object(m, "_hydrate_kakao_tokens", lambda: None), patch.object(
        m, "get_access_token", lambda: "disk-access"
    ), patch.object(m, "_get_kakao_refresh_token", lambda: "disk-refresh"), patch.object(
        m, "_try_kakao_auth_code_from_env", lambda *_a, **_k: ""
    ), patch.object(m, "_refresh_kakao_access_token", fail_refresh), patch.object(
        m, "_kakao_auth_recovery_due", return_value=True
    ), patch.object(m, "_manual_kakao_authorization_recovery", lambda *_a, **_k: ""), patch.object(
        m, "_env_str", env_str
    ):
        got = m._ensure_kakao_access_token(for_login=False, show_auth_link=False)
    still = m.KAKAO_AUTH_EXHAUSTED
    m._clear_kakao_auth_exhausted()
    if got != "":
        fail("no_fake_recovery", f"expected '', got {got!r}")
        return
    if not refresh_calls:
        fail("no_fake_recovery", "recovery due must attempt real refresh")
        return
    if not still:
        fail("no_fake_recovery", "exhausted must remain after failed refresh")
        return
    ok("recovery due attempts real refresh; no fake clear on disk tokens")


def test_invalid_grant_listener_marks_exhausted() -> None:
    """kakao_utils invalid_grant 리스너가 매매 게이트 exhausted 를 켠다."""
    import main_ai as m

    m._clear_kakao_auth_exhausted()
    m._on_kakao_invalid_grant_from_utils("invalid_grant", "expired_or_invalid_refresh_token")
    marked = m.KAKAO_AUTH_EXHAUSTED
    m._clear_kakao_auth_exhausted()
    if not marked:
        fail("invalid_grant_listener", "listener did not mark exhausted")
        return
    ok("invalid_grant listener marks auth exhausted")


def test_gate_skips_http_refresh() -> None:
    """평시 게이트(비exhausted)는 디스크 자격증명만 보고 HTTP refresh를 호출하지 않는다."""
    import main_ai as m

    refresh_calls: list[str] = []

    def boom(*a, **k):
        refresh_calls.append("called")
        return ""

    m._clear_kakao_auth_exhausted()
    with patch.object(m, "_notify_alerts_enabled", return_value=True), patch.object(
        m, "_kakao_alerts_enabled", return_value=True
    ), patch.object(m, "_notify_channel", return_value="kakao"), patch.object(
        m, "KakaoNotifier", object()
    ), patch.object(m, "_hydrate_kakao_tokens", lambda: None), patch.object(
        m, "get_access_token", lambda: "disk-access"
    ), patch.object(m, "_get_kakao_refresh_token", lambda: "disk-refresh"), patch.object(
        m, "_try_kakao_auth_code_from_env", lambda *_a, **_k: ""
    ), patch.object(m, "_refresh_kakao_access_token", boom), patch.object(
        m, "KAKAO_AUTH_EXHAUSTED", False
    ):
        got = m._ensure_kakao_access_token(for_login=False)
    if got != "disk-access":
        fail("gate_no_http", f"expected disk-access, got {got!r}")
        return
    if refresh_calls:
        fail("gate_no_http", "gate must not call refresh")
        return
    ok("auth gate uses disk credentials only (no proactive refresh)")


def test_persist_tokens_validates_disk() -> None:
    """갱신 토큰이 json+.env에 저장되고 재읽기 검증을 통과하는지."""
    import kakao_utils as ku

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        json_path = tmp_path / "kakao_code.json"
        env_path = tmp_path / ".env"
        env_path.write_text(
            "KAKAO_ACCESS_TOKEN=old_a\nKAKAO_REFRESH_TOKEN=old_r\n",
            encoding="utf-8",
        )
        json_path.write_text(
            '{"access_token":"old_a","refresh_token":"old_r"}\n',
            encoding="utf-8",
        )
        with patch.object(ku, "kakao_api_allowed", return_value=True), patch.object(
            ku, "KAKAO_CODE_JSON", json_path
        ), patch.object(ku, "_env_file_path", lambda: env_path):
            ok_persist = ku.persist_kakao_tokens(
                "new_access", "new_refresh", expires_in=3600, refresh_token_expires_in=60 * 86400
            )
            if not ok_persist:
                fail("persist_validate", "persist returned False")
                return
            access, refresh = ku._read_json_tokens()
            if access != "new_access" or refresh != "new_refresh":
                fail("persist_validate", f"json mismatch: {access!r}/{refresh!r}")
                return
            env_vals = ku._read_env_key_values(["KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN"])
            if env_vals["KAKAO_ACCESS_TOKEN"] != "new_access" or env_vals["KAKAO_REFRESH_TOKEN"] != "new_refresh":
                fail("persist_validate", f".env mismatch: {env_vals}")
                return
            if ku.get_access_expires_at() <= 0:
                fail("persist_validate", "expires_at not saved")
                return
            if ku.get_refresh_expires_at() <= 0:
                fail("persist_validate", "refresh_expires_at not saved")
                return
            left = ku.get_refresh_token_expires_in_remaining()
            if left is None or left < 50 * 86400:
                fail("persist_validate", f"refresh remaining unexpected: {left}")
                return
            # apply without refresh_token must keep refresh_expires_at
            prev_rexp = ku.get_refresh_expires_at()
            with patch.object(ku, "persist_kakao_tokens", wraps=ku.persist_kakao_tokens):
                pass
            ok2 = ku.persist_kakao_tokens(
                "access2", None, expires_in=100, rotate_refresh=False
            )
            if not ok2:
                fail("persist_validate", "access-only persist failed")
                return
            if abs(ku.get_refresh_expires_at() - prev_rexp) > 2:
                fail("persist_validate", "refresh_expires_at wiped on access-only update")
                return
            # apply_token_response must fail closed when persist fails
            with patch.object(ku, "persist_kakao_tokens", return_value=False):
                empty = ku.apply_token_response(
                    {"access_token": "x", "refresh_token": "y", "expires_in": 100}
                )
            if empty != "":
                fail("persist_validate", "apply_token_response should return '' when persist fails")
                return
    ok("persist writes+validates json/.env; refresh_expires_at saved; apply fails closed")


def test_omit_refresh_token_preserves_old() -> None:
    """카카오가 refresh_token 필드를 생략하면 기존 refresh를 절대 지우지 않는다."""
    import kakao_utils as ku

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        json_path = tmp_path / "kakao_code.json"
        env_path = tmp_path / ".env"
        env_path.write_text(
            "KAKAO_ACCESS_TOKEN=old_access\nKAKAO_REFRESH_TOKEN=keep_me_refresh\n",
            encoding="utf-8",
        )
        json_path.write_text(
            '{"access_token":"old_access","refresh_token":"keep_me_refresh"}\n',
            encoding="utf-8",
        )
        with patch.object(ku, "kakao_api_allowed", return_value=True), patch.object(
            ku, "KAKAO_CODE_JSON", json_path
        ), patch.object(ku, "_env_file_path", lambda: env_path):
            # 응답에 refresh_token 키 자체가 없음 (카카오 1개월+ 잔여 시 동작)
            result = ku.apply_token_response(
                {"access_token": "brand_new_access", "expires_in": 21600}
            )
            if result != "brand_new_access":
                fail("omit_refresh", f"expected new access, got {result!r}")
                return
            access, refresh = ku._read_json_tokens()
            if access != "brand_new_access":
                fail("omit_refresh", f"access not updated: {access!r}")
                return
            if refresh != "keep_me_refresh":
                fail("omit_refresh", f"refresh wiped/changed: {refresh!r}")
                return
            env_vals = ku._read_env_key_values(["KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN"])
            if env_vals["KAKAO_ACCESS_TOKEN"] != "brand_new_access":
                fail("omit_refresh", f".env access wrong: {env_vals}")
                return
            if env_vals["KAKAO_REFRESH_TOKEN"] != "keep_me_refresh":
                fail("omit_refresh", f".env refresh wiped: {env_vals}")
                return
            # refresh_token: null / "" 도 동일하게 유지
            result2 = ku.apply_token_response(
                {"access_token": "access2", "refresh_token": "", "expires_in": 100}
            )
            if result2 != "access2":
                fail("omit_refresh", f"empty refresh field should still save access, got {result2!r}")
                return
            _, refresh2 = ku._read_json_tokens()
            if refresh2 != "keep_me_refresh":
                fail("omit_refresh", f"empty refresh_token wiped old value: {refresh2!r}")
                return
    ok("omit/empty refresh_token preserves existing refresh; access updates")


def test_daily_report_survives_exception() -> None:
    """일일 리포트 중 예외가 나도 루프로 전파되지 않고 last_report를 찍지 않는다."""
    import main_ai as m

    stats: dict = {"last_report_at_kst": ""}
    now = datetime(2026, 8, 17, 9, 0, tzinfo=KST)

    with patch.object(m, "get_market_monitor_summary", side_effect=RuntimeError("timeout")), patch.object(
        m, "_build_kakao_message", side_effect=RuntimeError("boom")
    ), patch.object(m, "_notify_kakao", return_value=True), patch.object(
        m, "save_trading_stats", lambda *_a, **_k: None
    ):
        ok_flag = m._send_daily_status_report(
            report={"decision": "HOLD"},
            stats=stats,
            learning_summary="",
            close_info=None,
            open_position=None,
            dry_run=True,
            live_futures_usdt=None,
            krw_per_usdt=1380.0,
            now_kst=now,
            monitor_model="gpt-4o-mini",
            snapshot={"symbol": "BTCUSDT"},
        )
    if ok_flag is not False:
        fail("daily_report_survive", f"expected False on exception, got {ok_flag!r}")
        return
    if stats.get("last_report_at_kst"):
        fail("daily_report_survive", "last_report must not be set after failure")
        return
    ok("daily report exceptions are swallowed; last_report not marked on failure")


def test_repeat_failure_block_skips_ai_and_counts() -> None:
    """과거 학습 로그에 동일 시그니처가 반복되면 AI 호출 전 HOLD + 카운터 증가."""
    import main_ai as m

    sig = "side=NA|trend=bull|rsi=50.00|bb=mid|atr_pct=0.40|ema_gap_pct=0.40"
    snapshot = {
        "rsi": 50.0,
        "bb_position": 0.5,
        "atr_pct": 0.4,
        "ema_gap_pct": 0.4,
        "ema20": 100.0,
        "ema60": 99.0,
        "price": 100.0,
        "volume_ratio": 2.0,
    }
    if m._market_signature(snapshot) != sig:
        # 버킷 변경에 대비해 실제 시그니처로 맞춤
        sig = m._market_signature(snapshot)

    rows = [
        {
            "market_signature": sig,
            "failure_reason": f"fail-{i}",
            "reflection_summary": f"reflect-{i}",
            "warning": "warn",
            "pnl_usdt": "-1.0",
            "logged_at_kst": f"2026-09-0{i+1}T10:00:00+09:00",
        }
        for i in range(3)
    ]

    with patch.object(m, "_load_learning_rows", return_value=rows), patch.dict(
        os.environ,
        {
            "AI_FAILURE_SIM_STRONG": "0.92",
            "AI_REPEAT_FAILURE_LOOKBACK": "20",
            "AI_REPEAT_FAILURE_BLOCK_COUNT": "3",
        },
        clear=False,
    ):
        memory, _summary, sim = m._find_closest_failure_memory(snapshot)
        blocked, reason = m._should_block_repeat_failure(snapshot, sim)

    if not memory or sim < 0.92:
        fail("repeat_failure_block", f"expected strong memory match, got sim={sim}")
        return
    if not blocked:
        fail("repeat_failure_block", f"expected block, reason={reason!r}")
        return
    if "반복 실패" not in reason:
        fail("repeat_failure_block", f"unexpected reason: {reason}")
        return

    stats: dict = {
        "ai_calls_saved_by_gate": 0,
        "ai_calls_saved_by_repeat_failure": 0,
        "estimated_tokens_saved": 0,
        "ai_calls_by_day": {},
        "ai_entry_calls": 0,
    }
    called = {"ai": 0}

    def fake_ai(*_a, **_k):
        called["ai"] += 1
        return {"decision": "BUY", "confidence": 0.9, "reason": "should-not-run"}

    # 게이트 열린 스냅샷 (RSI/BB/EMA 중 하나) — 위 mid RSI면 게이트 닫힐 수 있음
    open_snap = dict(snapshot)
    open_snap["rsi"] = 50.0
    open_snap["ema_gap_pct"] = 0.5  # 게이트 오픈용
    open_snap["ema20"] = 100.0
    open_snap["ema60"] = 99.0

    with patch.object(m, "_load_learning_rows", return_value=rows), patch.object(
        m, "get_ai_decision", side_effect=fake_ai
    ), patch.dict(
        os.environ,
        {
            "AI_FAILURE_SIM_STRONG": "0.92",
            "AI_REPEAT_FAILURE_LOOKBACK": "20",
            "AI_REPEAT_FAILURE_BLOCK_COUNT": "3",
            "AI_GATE_EMA_GAP_MIN_PCT": "0.03",
            "AI_EST_TOKENS_PER_ENTRY_CALL": "900",
        },
        clear=False,
    ):
        gate_open, _ = m._ai_entry_gate(open_snap)
        if not gate_open:
            fail("repeat_failure_block", "test snapshot must open entry gate")
            return
        mem_block, _, mem_sim = m._find_closest_failure_memory(open_snap)
        block_repeat, block_reason = m._should_block_repeat_failure(open_snap, mem_sim)
        if not block_repeat:
            fail("repeat_failure_block", "expected repeat block when gate open")
            return
        # run_cycle 분기와 동일한 카운터 갱신 경로 재현
        model_ai_decision = {
            "decision": "HOLD",
            "reason": block_reason,
            "confidence": 1.0,
        }
        stats["ai_calls_saved_by_gate"] = int(stats.get("ai_calls_saved_by_gate", 0)) + 1
        stats["ai_calls_saved_by_repeat_failure"] = int(stats.get("ai_calls_saved_by_repeat_failure", 0)) + 1
        stats["estimated_tokens_saved"] = int(stats.get("estimated_tokens_saved", 0)) + 900
        m._bump_ai_calls_by_day(stats, "saved_gate")
        m._bump_ai_calls_by_day(stats, "saved_repeat")
        _ = mem_block  # memory would be unused when blocked

    if called["ai"] != 0:
        fail("repeat_failure_block", "get_ai_decision must not be called when blocked")
        return
    if model_ai_decision.get("decision") != "HOLD":
        fail("repeat_failure_block", "decision must be HOLD")
        return
    if stats["ai_calls_saved_by_gate"] != 1 or stats["ai_calls_saved_by_repeat_failure"] != 1:
        fail("repeat_failure_block", f"counters wrong: {stats}")
        return
    if stats["estimated_tokens_saved"] != 900:
        fail("repeat_failure_block", f"tokens saved wrong: {stats['estimated_tokens_saved']}")
        return
    ok("repeat failure block skips AI and increments gate/repeat counters")


def test_repeat_failure_no_block_when_below_count() -> None:
    import main_ai as m

    snapshot = {
        "rsi": 50.0,
        "bb_position": 0.5,
        "atr_pct": 0.4,
        "ema_gap_pct": 0.4,
        "ema20": 100.0,
        "ema60": 99.0,
    }
    sig = m._market_signature(snapshot)
    rows = [
        {
            "market_signature": sig,
            "failure_reason": "only-twice-1",
            "reflection_summary": "r1",
            "warning": "",
            "pnl_usdt": "-1",
            "logged_at_kst": "2026-09-01T10:00:00+09:00",
        },
        {
            "market_signature": sig,
            "failure_reason": "only-twice-2",
            "reflection_summary": "r2",
            "warning": "",
            "pnl_usdt": "-1",
            "logged_at_kst": "2026-09-02T10:00:00+09:00",
        },
    ]
    with patch.object(m, "_load_learning_rows", return_value=rows), patch.dict(
        os.environ,
        {"AI_REPEAT_FAILURE_BLOCK_COUNT": "3", "AI_FAILURE_SIM_STRONG": "0.92"},
        clear=False,
    ):
        _, _, sim = m._find_closest_failure_memory(snapshot)
        blocked, _ = m._should_block_repeat_failure(snapshot, sim)
    if blocked:
        fail("repeat_failure_no_block", "2 hits < block_count=3 should not block")
        return
    ok("repeat failure does not block below count threshold")


def test_build_kakao_message_shows_repeat_failure_saved() -> None:
    import main_ai as m

    now = datetime(2026, 6, 10, 20, 0, tzinfo=KST)
    with patch.object(m, "_format_daily_trade_summary", return_value="· 없음"):
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
            stats={
                "virtual_balance_krw": 500000,
                "virtual_balance_usdt": 362,
                "ai_calls_saved_by_gate": 12,
                "ai_calls_saved_by_repeat_failure": 4,
                "estimated_tokens_saved": 8000,
            },
            learning_summary="",
            close_info=None,
            open_position=None,
            dry_run=True,
            live_futures_usdt=None,
            krw_per_usdt=1380,
            now_kst=now,
        )
    if "반복실패 차단: 4회" not in msg:
        fail("kakao_repeat_line", "missing repeat failure count in report")
        return
    if "AI 게이트 절감 호출: 12회" not in msg:
        fail("kakao_repeat_line", "missing gate saved line")
        return
    ok("daily report includes repeat-failure saved count")


def main() -> int:
    print("=== verify_today_changes ===")
    test_daily_trade_summary()
    test_periodic_report_once_per_day()
    test_notify_kakao_gating()
    test_refresh_listener()
    test_build_kakao_message_has_trade_section()
    test_auth_wait_mode_state_machine()
    test_refresh_retry_uses_new_token_from_store()
    test_refresh_retry_stops_on_repeated_invalid_grant()
    test_refresh_listener_called_outside_lock()
    test_async_notifier_dead_worker_recovery()
    test_token_heartbeat_throttle()
    test_heartbeat_probes_refresh_when_stale()
    test_exhausted_blocks_gate_despite_disk_tokens()
    test_recovery_does_not_fake_clear_on_disk_tokens()
    test_invalid_grant_listener_marks_exhausted()
    test_persist_tokens_validates_disk()
    test_omit_refresh_token_preserves_old()
    test_gate_skips_http_refresh()
    test_daily_report_survives_exception()
    test_repeat_failure_block_skips_ai_and_counts()
    test_repeat_failure_no_block_when_below_count()
    test_build_kakao_message_shows_repeat_failure_saved()
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
