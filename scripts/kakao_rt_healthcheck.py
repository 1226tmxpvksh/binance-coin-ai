#!/usr/bin/env python3
"""카카오 refresh_token 독립 헬스체크 (순수 로깅 전용).

access_token 로컬 fresh 여부와 무관하게 항상 force refresh 를 1회 시도하고
결과를 data/kakao_rt_health.jsonl 에만 기록한다. 알림/대화형 인증 없음.

기존 kakao_utils.refresh_kakao_access_token_sync 를 재사용한다
(저장·회전·검증 로직과 분기하지 않기 위함).

Cron 예시 (bot2 유저, 15분 간격)::

    */15 * * * * cd /home/bot2/Coin && /home/bot2/venv/bin/python scripts/kakao_rt_healthcheck.py >>/home/bot2/Coin/data/kakao_rt_healthcheck.cron.log 2>&1

systemd timer 예::

    # /etc/systemd/system/kakao-rt-healthcheck.service
    # [Service] User=bot2 WorkingDirectory=/home/bot2/Coin
    # ExecStart=/home/bot2/venv/bin/python scripts/kakao_rt_healthcheck.py
    # /etc/systemd/system/kakao-rt-healthcheck.timer  OnCalendar=*:0/15
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "btc_live_trading"
DATA_DIR = ROOT / "data"
LOCK_PATH = ROOT / ".kakao_rt_healthcheck.lock"
JSONL_PATH = DATA_DIR / "kakao_rt_health.jsonl"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(LIVE) not in sys.path:
    sys.path.insert(0, str(LIVE))

KST = timezone(timedelta(hours=9))
_LOCK_HANDLE = None

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("kakao_rt_healthcheck")


def _acquire_lock() -> bool:
    """flock/msvcrt 비차단 단일 실행. 실패 시 False (겹침 — 조용히 종료)."""
    global _LOCK_HANDLE
    try:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        handle = open(LOCK_PATH, "a+", encoding="utf-8")
    except OSError as exc:
        logger.error("lock open failed: %s", exc)
        return False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    _LOCK_HANDLE = handle
    return True


def _append_jsonl(record: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with JSONL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _classify_exc(exc: BaseException) -> tuple[str, int | None, str, str]:
    """return result, http_status, error_code, error_description."""
    http_status: int | None = None
    error_code = ""
    error_description = ""
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            http_status = int(getattr(resp, "status_code", 0) or 0) or None
        except (TypeError, ValueError):
            http_status = None
        try:
            body = resp.json()
            if isinstance(body, dict):
                error_code = str(body.get("error") or body.get("error_code") or "")
                error_description = str(
                    body.get("error_description") or body.get("msg") or body.get("message") or ""
                )
        except Exception:
            error_description = (getattr(resp, "text", None) or str(exc))[:300]
    else:
        error_description = str(exc)[:300]

    blob = f"{error_code} {error_description} {exc}".lower()
    if "invalid_grant" in blob or "expired_or_invalid_refresh_token" in blob:
        return "invalid_grant", http_status, error_code or "invalid_grant", error_description
    if http_status is not None:
        return "http_error", http_status, error_code, error_description
    return "exception", http_status, error_code, error_description or type(exc).__name__


def main() -> int:
    if not _acquire_lock():
        # 겹침 실행 — cron 중복 시 정상. 로그 파일에도 남기지 않음.
        return 0

    try:
        from dotenv import load_dotenv

        load_dotenv(LIVE / ".env", override=True)
    except Exception:
        pass

    from kakao_utils import (
        get_refresh_token,
        get_refresh_token_expires_in_remaining,
        kakao_api_allowed,
        refresh_kakao_access_token_sync,
    )

    checked_at = datetime.now(KST).isoformat(timespec="seconds")
    remaining = get_refresh_token_expires_in_remaining()
    remaining_days: float | None = None
    if remaining is not None:
        remaining_days = round(remaining / 86400.0, 3)

    record: dict[str, Any] = {
        "checked_at_kst": checked_at,
        "result": "exception",
        "http_status": None,
        "error_code": "",
        "error_description": "",
        "refresh_remaining_days": remaining_days,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
    }

    if not kakao_api_allowed():
        record["result"] = "exception"
        record["error_description"] = "kakao_api_allowed=False (whitelist)"
        _append_jsonl(record)
        return 1

    client_id = (os.getenv("KAKAO_REST_API_KEY") or "").strip()
    if not client_id or client_id == "your_rest_api_key_here":
        record["result"] = "exception"
        record["error_description"] = "KAKAO_REST_API_KEY missing"
        _append_jsonl(record)
        return 1

    if not get_refresh_token().strip():
        record["result"] = "exception"
        record["error_description"] = "refresh_token empty on disk"
        _append_jsonl(record)
        return 1

    try:
        # force=True: access 로컬 fresh 여부와 무관하게 항상 refresh grant 시도
        access = refresh_kakao_access_token_sync(client_id, "", force=True).strip()
        if access:
            record["result"] = "ok"
        else:
            record["result"] = "exception"
            record["error_description"] = (
                "refresh returned empty (persist/validate fail or empty response)"
            )
    except Exception as exc:
        result, http_status, error_code, error_description = _classify_exc(exc)
        record["result"] = result
        record["http_status"] = http_status
        record["error_code"] = error_code
        record["error_description"] = error_description

    _append_jsonl(record)
    # stdout 한 줄 — cron 로그 확인용 (알림 아님)
    print(json.dumps(record, ensure_ascii=False))
    return 0 if record["result"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
