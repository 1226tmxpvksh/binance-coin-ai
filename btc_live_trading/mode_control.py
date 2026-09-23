"""
AI_DRY_RUN 파일 조회·한 줄 교체 (매매/주문 호출 없음).

디스코드 /mode · /setmode · /confirm_live 가 사용한다.
.coinbot 재시작은 호출하지 않는다 — 승인된 정책이 나오기 전 .env 만 바꾼다.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_MODULE_DIR = Path(__file__).resolve().parent
_ROOT = _MODULE_DIR.parent
ENV_PATH = _MODULE_DIR / ".env"
MODE_EVENT_LOG = _ROOT / "data" / "mode_change_events.jsonl"
KST = timezone(timedelta(hours=9))
CONFIRM_WINDOW_SEC = 30.0
_LIVE_PENDING: dict[str, float] = {}

_AI_DRY_RUN_ASSIGN = re.compile(r"^([ \t]*AI_DRY_RUN[ \t]*=)(.*)$")


def _truthy(raw: str) -> bool:
    return (raw or "").strip().strip("'").strip('"').lower() in {"1", "true", "yes", "y", "on"}


def authorized_user_id() -> int | None:
    raw = (os.getenv("DISCORD_AUTHORIZED_USER_ID") or "").strip()
    if not raw or raw.startswith("your_"):
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def is_authorized(user_id: int | None) -> bool:
    allowed = authorized_user_id()
    if allowed is None or user_id is None:
        return False
    return int(user_id) == allowed


def read_dry_run_from_env_file(path: Path | None = None) -> bool | None:
    """파일의 AI_DRY_RUN 할당 줄. 없으면 None."""
    target = path or ENV_PATH
    if not target.exists():
        return None
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _AI_DRY_RUN_ASSIGN.match(raw.rstrip("\r"))
        if match:
            return _truthy(match.group(2))
    return None


def mode_label(dry_run: bool | None) -> str:
    if dry_run is None:
        return "확인 불가"
    return "페이퍼(가상)" if dry_run else "실전"


def replace_ai_dry_run_line(
    new_value: str,
    *,
    path: Path | None = None,
) -> tuple[bool, str]:
    """AI_DRY_RUN= 할당 줄만 교체. 다른 줄은 그대로 둔다."""
    value = (new_value or "").strip().lower()
    if value not in {"true", "false"}:
        return False, "AI_DRY_RUN 값은 true 또는 false 만 허용합니다."
    target = path or ENV_PATH
    if not target.exists():
        return False, f".env 없음: {target}"
    try:
        original = target.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return False, f".env 읽기 실패: {type(exc).__name__}"

    lines = original.splitlines(keepends=True)
    if not lines and original:
        lines = [original]
    found = False
    out: list[str] = []
    for line in lines:
        ending = ""
        body = line
        if body.endswith("\r\n"):
            ending = "\r\n"
            body = body[:-2]
        elif body.endswith("\n"):
            ending = "\n"
            body = body[:-1]
        elif body.endswith("\r"):
            ending = "\r"
            body = body[:-1]
        stripped = body.lstrip()
        if stripped.startswith("#"):
            out.append(line)
            continue
        match = _AI_DRY_RUN_ASSIGN.match(body)
        if match and not found:
            out.append(f"{match.group(1)}{value}{ending}")
            found = True
            continue
        out.append(line)
    if not found:
        return False, "AI_DRY_RUN 할당 줄을 찾지 못했습니다."

    new_text = "".join(out)
    if new_text == original:
        return True, "unchanged"
    try:
        target.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return False, f".env 쓰기 실패: {type(exc).__name__}"
    os.environ["AI_DRY_RUN"] = value
    return True, "updated"


def append_mode_change_event(
    *,
    previous: str,
    next_mode: str,
    discord_user_id: int | None,
    extra: dict[str, Any] | None = None,
    log_path: Path | None = None,
) -> None:
    payload: dict[str, Any] = {
        "logged_at_kst": datetime.now(KST).isoformat(timespec="seconds"),
        "previous_mode": previous,
        "next_mode": next_mode,
        "discord_user_id": str(discord_user_id or ""),
    }
    if extra:
        payload.update(extra)
    dest = log_path or MODE_EVENT_LOG
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def request_live_confirm(user_id: int, *, now: float | None = None) -> float:
    expires = float(now if now is not None else time.monotonic()) + CONFIRM_WINDOW_SEC
    _LIVE_PENDING[str(user_id)] = expires
    return expires


def consume_live_confirm(user_id: int, *, now: float | None = None) -> str:
    """ok | missing | expired."""
    clock = float(now if now is not None else time.monotonic())
    key = str(user_id)
    expires = _LIVE_PENDING.get(key)
    if expires is None:
        return "missing"
    if expires <= clock:
        _LIVE_PENDING.pop(key, None)
        return "expired"
    _LIVE_PENDING.pop(key, None)
    return "ok"


def clear_live_confirm(user_id: int | None = None) -> None:
    if user_id is None:
        _LIVE_PENDING.clear()
        return
    _LIVE_PENDING.pop(str(user_id), None)
