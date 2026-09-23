"""오늘 카카오·일일리포트 변경분 로컬 검증 (토큰/네트워크 불필요)."""
from __future__ import annotations

import json
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


def test_ema_gap_entry_filter_applies_in_live_env() -> None:
    """AI_DRY_RUN=false 상태에서도 |EMA갭| < min 이면 HOLD. 설정값 자체는 바꾸지 않는다."""
    import logging
    import os
    import re

    import main_ai as m

    env_path = m.LIVE_DIR / ".env"
    env_text = env_path.read_text(encoding="utf-8-sig")
    file_match = re.search(r"^AI_DRY_RUN=(.*)$", env_text, re.M)
    file_raw = (file_match.group(1).strip().strip("'").strip('"') if file_match else "")
    if file_raw.lower() not in {"0", "false", "no", "off"}:
        fail("ema_gap_filter", f"expected .env AI_DRY_RUN=false unchanged, got {file_raw!r}")
        return

    m._load_env()
    loaded = str(os.getenv("AI_DRY_RUN", "")).strip()
    if loaded.lower() not in {"0", "false", "no", "off"}:
        fail("ema_gap_filter", f"loaded AI_DRY_RUN is not false: {loaded!r}")
        return

    decision = {"decision": "BUY", "confidence": 0.82, "reason": "추세 정렬"}
    weak = {"ema_gap_pct": 0.10}
    strong = {"ema_gap_pct": 0.45}
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Capture(level=logging.INFO)
    m.logger.addHandler(handler)
    try:
        with patch.object(m, "_min_ema_gap_pct", return_value=0.3):
            held = m._apply_ema_gap_entry_filter(dict(decision), weak)
            passed = m._apply_ema_gap_entry_filter(dict(decision), strong)
            sell_held = m._apply_ema_gap_entry_filter(
                {"decision": "SELL", "confidence": 0.8, "reason": "하락 추세"},
                {"ema_gap_pct": -0.12},
            )
        with patch.object(m, "_env_bool", return_value=False), patch.object(
            m, "_min_ema_gap_pct", return_value=0.3
        ):
            live_patched = m._apply_ema_gap_entry_filter(dict(decision), weak)
    finally:
        m.logger.removeHandler(handler)

    after_file = env_path.read_text(encoding="utf-8-sig")
    after_match = re.search(r"^AI_DRY_RUN=(.*)$", after_file, re.M)
    after_raw = (after_match.group(1).strip().strip("'").strip('"') if after_match else "")
    if after_raw != file_raw:
        fail("ema_gap_filter", f".env AI_DRY_RUN mutated: {file_raw!r} -> {after_raw!r}")
        return
    if held.get("decision") != "HOLD":
        fail("ema_gap_filter", f"live env weak gap should HOLD, got {held}")
        return
    if "EMA 갭 부족으로 진입 스킵" not in str(held.get("reason", "")):
        fail("ema_gap_filter", f"missing skip reason: {held.get('reason')!r}")
        return
    skip_logs = [msg for msg in records if "EMA 갭 부족으로 진입 스킵" in msg]
    if not skip_logs:
        fail("ema_gap_filter", f"skip log not emitted: {records!r}")
        return
    if any("(paper)" in msg for msg in skip_logs):
        fail("ema_gap_filter", f"log still has (paper) label: {skip_logs!r}")
        return
    if passed.get("decision") != "BUY":
        fail("ema_gap_filter", f"strong gap should stay BUY, got {passed}")
        return
    if sell_held.get("decision") != "HOLD":
        fail("ema_gap_filter", f"weak negative gap SELL should HOLD, got {sell_held}")
        return
    if live_patched.get("decision") != "HOLD":
        fail("ema_gap_filter", f"_env_bool=false must still HOLD, got {live_patched}")
        return
    ok("ema gap filter HOLDs weak gap with AI_DRY_RUN=false; env value unchanged")


def test_taker_fee_accounting_and_18_loss_backfill() -> None:
    """pnl 회계만: 요율·주문 commission·18건 소급이 수동 계산(-38.31)과 맞는지."""
    import csv
    import os

    import main_ai as m
    from binance_futures_tools import extract_order_commission_usdt

    extracted = extract_order_commission_usdt(
        {"commission": "0.12", "commissionAsset": "USDT"}
    )
    if extracted is None or abs(extracted - 0.12) > 1e-9:
        fail("taker_fee_extract", f"expected 0.12 from order.commission, got {extracted}")
        return
    from_fills = extract_order_commission_usdt(
        {
            "commission": "0.12",
            "commissionAsset": "USDT",
            "fills": [{"commission": "0.08", "commissionAsset": "USDT"}],
        }
    )
    if from_fills is None or abs(from_fills - 0.08) > 1e-9:
        fail("taker_fee_extract", f"fills should win without double-count, got {from_fills}")
        return
    if extract_order_commission_usdt({"commission": "0.00"}) is not None:
        fail("taker_fee_extract", "zero commission must be treated as missing")
        return
    if extract_order_commission_usdt({"commission": "0.01", "commissionAsset": "BNB"}) is not None:
        fail("taker_fee_extract", "BNB commission must fall back (None)")
        return
    ok("order commission extract uses USDT fills and ignores zero/BNB")

    paper = m._compute_realized_pnl(
        side="BUY",
        entry_price=100_000.0,
        exit_price=100_000.0,
        size=0.01,
        taker_rate=0.0004,
    )
    if abs(float(paper["fee_usdt"]) - 0.8) > 1e-9:
        fail("taker_fee_paper", f"flat round-trip fee expected 0.8, got {paper['fee_usdt']}")
        return
    if abs(float(paper["pnl_usdt_net"]) + 0.8) > 1e-9:
        fail("taker_fee_paper", f"net expected -0.8, got {paper['pnl_usdt_net']}")
        return
    if paper["pnl_usdt"] != paper["pnl_usdt_net"]:
        fail("taker_fee_paper", "pnl_usdt must equal net")
        return
    if paper["fee_source"] != "rate_open+rate_close":
        fail("taker_fee_paper", f"unexpected source {paper['fee_source']}")
        return
    ok("paper path subtracts the same taker rate on both legs")

    live = m._compute_realized_pnl(
        side="SELL",
        entry_price=80_000.0,
        exit_price=80_000.0,
        size=0.01,
        entry_fee_usdt=0.25,
        exit_fee_usdt=0.31,
        taker_rate=0.0004,
    )
    if abs(float(live["fee_usdt"]) - 0.56) > 1e-9:
        fail("taker_fee_live", f"actual commissions 0.56 expected, got {live['fee_usdt']}")
        return
    if live["fee_source"] != "order_open+order_close":
        fail("taker_fee_live", f"unexpected source {live['fee_source']}")
        return
    mixed = m._compute_realized_pnl(
        side="BUY",
        entry_price=50_000.0,
        exit_price=50_000.0,
        size=0.01,
        entry_fee_usdt=0.3,
        taker_rate=0.0004,
    )
    expected_mixed = 0.3 + abs(50_000.0 * 0.01) * 0.0004
    if abs(float(mixed["fee_usdt"]) - expected_mixed) > 1e-9:
        fail("taker_fee_live", f"mixed fee expected {expected_mixed}, got {mixed['fee_usdt']}")
        return
    ok("live path prefers order commission and estimates only the missing leg")

    csv_path = ROOT / "btc_live_trading" / "ai_learning_logs.csv"
    if not csv_path.exists():
        fail("taker_fee_backfill", f"missing {csv_path}")
        return
    gross_sum = 0.0
    net_sum = 0.0
    fee_sum = 0.0
    fee_2entry_sum = 0.0
    n = 0
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            side = str(row.get("side", "")).upper()
            entry = float(row["entry_price"])
            exitp = float(row["exit_price"])
            old_pnl = float(row["pnl_usdt"])
            dx = (entry - exitp) if side == "SELL" else (exitp - entry)
            if abs(dx) < 1e-12:
                fail("taker_fee_backfill", f"zero move on {row.get('trade_id')}")
                return
            size = old_pnl / dx
            realized = m._compute_realized_pnl(
                side=side,
                entry_price=entry,
                exit_price=exitp,
                size=size,
                taker_rate=0.0004,
            )
            gross_sum += float(realized["pnl_usdt_gross"])
            net_sum += float(realized["pnl_usdt_net"])
            fee_sum += float(realized["fee_usdt"])
            fee_2entry_sum += 2.0 * abs(entry * size) * 0.0004
            n += 1
    if n != 18:
        fail("taker_fee_backfill", f"expected 18 loss rows, got {n}")
        return
    if abs(gross_sum + 30.527047) > 0.001:
        fail("taker_fee_backfill", f"gross sum {gross_sum:.6f} != logged -30.527047")
        return
    net_2entry = gross_sum - fee_2entry_sum
    if abs(net_2entry + 38.31) > 0.01:
        fail(
            "taker_fee_backfill",
            f"2*entry notional net {net_2entry:.6f} != -38.31",
        )
        return
    if abs(net_sum + 38.31) > 0.05:
        fail(
            "taker_fee_backfill",
            f"entry+exit notional net {net_sum:.6f} drifted from -38.31 (fee={fee_sum:.6f})",
        )
        return
    ok(
        f"18-loss backfill net={net_sum:.4f} USDT (2*entry {net_2entry:.4f}) matches -38.31"
    )

    now = datetime(2026, 9, 16, 12, 0, tzinfo=KST)
    stats = {
        "virtual_balance_usdt": 362.0,
        "virtual_balance_krw": 500000.0,
        "total_realized_pnl_usdt": 0.0,
        "total_realized_pnl_krw": 0.0,
        "monthly_realized_pnl_usdt": 0.0,
        "monthly_realized_pnl_krw": 0.0,
        "trade_count": 0,
        "monthly_trade_count": 0,
        "win_count": 0,
        "monthly_win_count": 0,
        "loss_count": 0,
        "monthly_loss_count": 0,
        "unique_failure_count": 0,
        "open_position": None,
    }
    position = {
        "trade_id": "PAPER-TEST-FEE",
        "order_mode": "paper",
        "symbol": "BTCUSDT",
        "interval": "15m",
        "side": "BUY",
        "opened_at_kst": now.isoformat(),
        "entry_price": 100000.0,
        "position_size": 0.01,
        "decision_reason": "test",
        "entry_snapshot": {"price": 100000.0, "rsi": 50, "ema20": 1, "ema60": 1, "bb_position": 0.5, "atr_pct": 0.2, "ema_gap_pct": 0.4},
    }
    with tempfile.TemporaryDirectory() as tmp:
        trade_log = Path(tmp) / "virtual_trades.jsonl"
        learn_log = Path(tmp) / "ai_learning_logs.csv"
        with patch.object(m, "TRADE_LOG_PATH", trade_log), patch.object(
            m, "LEARNING_LOG_PATH", learn_log
        ), patch.object(m, "_binance_taker_fee_rate", return_value=0.0004), patch(
            "ai_logic.decision_engine.analyze_trade_failure",
            return_value={
                "market_context": "x",
                "failure_reason": "y",
                "reflection_summary": "z",
                "warning": "w",
            },
        ), patch.object(
            m, "analyze_trade_failure",
            return_value={
                "market_context": "x",
                "failure_reason": "y",
                "reflection_summary": "z",
                "warning": "w",
            },
        ):
            result = m._close_position(
                stats=stats,
                open_position=position,
                snapshot={"price": 100000.0},
                now_kst=now,
                krw_per_usdt=1300.0,
                model="gpt-4o-mini",
                exit_reason="test_fee",
                dry_run=True,
            )
    ev = result.get("close_event") or {}
    if abs(float(ev.get("pnl_usdt_gross", 0)) - 0.0) > 1e-9:
        fail("taker_fee_close", f"gross expected 0, got {ev.get('pnl_usdt_gross')}")
        return
    if abs(float(ev.get("fee_usdt", 0)) - 0.8) > 1e-9:
        fail("taker_fee_close", f"fee expected 0.8, got {ev.get('fee_usdt')}")
        return
    if abs(float(ev.get("pnl_usdt", 0)) + 0.8) > 1e-9:
        fail("taker_fee_close", f"pnl_usdt(net) expected -0.8, got {ev.get('pnl_usdt')}")
        return
    if abs(float(stats["virtual_balance_usdt"]) - (362.0 - 0.8)) > 1e-9:
        fail("taker_fee_close", f"ledger not updated with net: {stats['virtual_balance_usdt']}")
        return
    if stats.get("loss_count") != 1:
        fail("taker_fee_close", "net loss must count as a loss")
        return
    ok("paper _close_position writes gross/net/fee and books net")

    prev = os.getenv("BINANCE_TAKER_FEE_PCT")
    os.environ["BINANCE_TAKER_FEE_PCT"] = "0.04"
    try:
        rate = m._binance_taker_fee_rate()
    finally:
        if prev is None:
            os.environ.pop("BINANCE_TAKER_FEE_PCT", None)
        else:
            os.environ["BINANCE_TAKER_FEE_PCT"] = prev
    if abs(rate - 0.0004) > 1e-12:
        fail("taker_fee_env", f"0.04% should be 0.0004, got {rate}")
        return
    ok("BINANCE_TAKER_FEE_PCT=0.04 maps to rate 0.0004")


def test_status_snapshot_and_mode_guards() -> None:
    """실전 /status 잔고 경로, .env 한 줄 교체, live 확인 만료."""
    import os

    import mode_control as mc
    import status_query as sq

    stats = {
        "initial_balance_krw": 500000.0,
        "initial_balance_usdt": 362.0,
        "virtual_balance_krw": 544853.96,
        "virtual_balance_usdt": 394.0,
        "total_realized_pnl_krw": 44853.96,
        "monthly_realized_pnl_krw": 0.0,
        "monthly_realized_pnl_usdt": 0.0,
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "run_count": 1,
        "last_cycle_at_kst": "",
        "open_position": None,
    }
    prev_dry = os.getenv("AI_DRY_RUN")
    os.environ["AI_DRY_RUN"] = "false"
    try:
        with patch.object(sq, "load_trading_stats", return_value=stats), patch.object(
            sq, "_live_futures_usdt", return_value=166.78
        ), patch.object(sq, "_krw_per_usdt", return_value=1300.0):
            live_snap = sq.build_status_snapshot()
    finally:
        if prev_dry is None:
            os.environ.pop("AI_DRY_RUN", None)
        else:
            os.environ["AI_DRY_RUN"] = prev_dry
    if abs(float(live_snap.get("balance_usdt") or 0) - 166.78) > 1e-9:
        fail("status_live_bal", f"live USDT expected 166.78, got {live_snap.get('balance_usdt')}")
        return
    if live_snap.get("balance_source") != "binance_futures":
        fail("status_live_bal", f"source={live_snap.get('balance_source')}")
        return
    if abs(float(live_snap.get("balance_krw") or 0) - 166.78 * 1300.0) > 0.01:
        fail("status_live_bal", f"KRW mismatch {live_snap.get('balance_krw')}")
        return
    ok("live /status uses Binance futures wallet, not paper ledger")

    os.environ["AI_DRY_RUN"] = "true"
    called = {"live": False}

    def _forbid_live() -> float:
        called["live"] = True
        return 166.78

    try:
        with patch.object(sq, "load_trading_stats", return_value=stats), patch.object(
            sq, "_live_futures_usdt", side_effect=_forbid_live
        ):
            paper_snap = sq.build_status_snapshot()
    finally:
        if prev_dry is None:
            os.environ.pop("AI_DRY_RUN", None)
        else:
            os.environ["AI_DRY_RUN"] = prev_dry
    if called["live"]:
        fail("status_paper_bal", "paper mode must not fetch futures wallet")
        return
    if paper_snap.get("balance_source") != "virtual_ledger":
        fail("status_paper_bal", f"source={paper_snap.get('balance_source')}")
        return
    if abs(float(paper_snap.get("balance_krw") or 0) - 544853.96) > 0.01:
        fail("status_paper_bal", f"paper KRW {paper_snap.get('balance_krw')}")
        return
    ok("paper /status keeps virtual_balance and skips exchange fetch")

    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        env_path.write_text(
            "# keep\nOTHER=1\nAI_DRY_RUN=true\nAI_SYMBOL=BTCUSDT\n",
            encoding="utf-8",
        )
        before = env_path.read_text(encoding="utf-8")
        ok_write, detail = mc.replace_ai_dry_run_line("false", path=env_path)
        after = env_path.read_text(encoding="utf-8")
        if not ok_write:
            fail("mode_env_line", detail)
            return
        if "OTHER=1" not in after or "AI_SYMBOL=BTCUSDT" not in after or "# keep" not in after:
            fail("mode_env_line", "other keys were rewritten")
            return
        if "AI_DRY_RUN=false" not in after or "AI_DRY_RUN=true" in after:
            fail("mode_env_line", f"dry_run not switched: {after!r}")
            return
        if after.count("\n") != before.count("\n"):
            fail("mode_env_line", "line count changed")
            return
        if mc.read_dry_run_from_env_file(env_path) is not False:
            fail("mode_env_line", "file readback is not live")
            return
    ok("AI_DRY_RUN line-only replace leaves the rest of .env intact")

    mc.clear_live_confirm()
    mc.request_live_confirm(99, now=1000.0)
    if mc.consume_live_confirm(99, now=1031.0) != "expired":
        fail("mode_confirm", "30s window must expire")
        return
    mc.request_live_confirm(99, now=2000.0)
    if mc.consume_live_confirm(7, now=2001.0) != "missing":
        fail("mode_confirm", "other user must not confirm")
        return
    if mc.consume_live_confirm(99, now=2010.0) != "ok":
        fail("mode_confirm", "owner confirm inside window failed")
        return
    prev_uid = os.getenv("DISCORD_AUTHORIZED_USER_ID")
    os.environ["DISCORD_AUTHORIZED_USER_ID"] = "42"
    try:
        if not mc.is_authorized(42) or mc.is_authorized(7):
            fail("mode_auth", "authorized id check failed")
            return
    finally:
        if prev_uid is None:
            os.environ.pop("DISCORD_AUTHORIZED_USER_ID", None)
        else:
            os.environ["DISCORD_AUTHORIZED_USER_ID"] = prev_uid
    ok("live confirm is 30s + same user; unauthorized ids are rejected")

    bot_src = (ROOT / "scripts" / "discord_bot.py").read_text(encoding="utf-8")
    needed = [
        'name="status", description="조회 전용, 현재 매매 상태·손익·포지션 확인"',
        'name="health", description="조회 전용, 봇 프로세스 정상 동작 여부 확인"',
        'name="mode", description="조회 전용, 현재 페이퍼/실전 모드 확인"',
        'name="help", description="이 도움말"',
        "live는 2단계 확인 필요",
        "_help_embed",
        "tree.get_commands()",
    ]
    missing = [item for item in needed if item not in bot_src]
    if missing:
        fail("help_cmds", f"missing {missing}")
        return
    ok("/help is generated from CommandTree; descriptions match required text")


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
    test_ema_gap_entry_filter_applies_in_live_env()
    test_taker_fee_accounting_and_18_loss_backfill()
    test_status_snapshot_and_mode_guards()
    test_persist_tokens_validates_disk()
    test_omit_refresh_token_preserves_old()
    test_gate_skips_http_refresh()
    test_daily_report_survives_exception()
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
