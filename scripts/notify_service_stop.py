"""systemd ExecStopPost용 서비스 종료 알림. main_ai를 import하지 않는다.

종료코드는 항상 0이다. ExecStopPost 실패가 유닛 상태를 바꾸지 않게 한다.
"""

from __future__ import annotations

import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "btc_live_trading" / ".env"
KST = timezone(timedelta(hours=9))
COLOR_ABNORMAL = 0xED4245  # 비정상
COLOR_NORMAL = 0x99AAB5  # 정상


def read_webhook_url(env_path: Path) -> str:
    """DISCORD_WEBHOOK_URL 값만 읽는다. 없으면 빈 문자열."""
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "DISCORD_WEBHOOK_URL":
            continue
        return value.strip().strip('"').strip("'")
    return ""


def classify(service_result: str) -> tuple[bool, str]:
    """(비정상 여부, 제목). success만 정상 종료다."""
    result = (service_result or "").strip().lower()
    if result == "success":
        return False, "정상 종료(수동 stop/restart)"
    cause = result or "unknown"
    return True, f"⚠️ 비정상 종료 ({cause})"


def build_payload(
    service: str,
    service_result: str,
    exit_code: str,
    exit_status: str,
    *,
    now: datetime,
    host: str,
) -> dict:
    abnormal, title = classify(service_result)
    shown = (service_result or "").strip() or "unknown"
    return {
        "embeds": [
            {
                "title": f"{service} {title}",
                "color": COLOR_ABNORMAL if abnormal else COLOR_NORMAL,
                "fields": [
                    {"name": "서비스", "value": service, "inline": True},
                    {"name": "시각", "value": now.strftime("%Y-%m-%d %H:%M:%S KST"), "inline": True},
                    {"name": "호스트", "value": host or "-", "inline": True},
                    {"name": "SERVICE_RESULT", "value": shown, "inline": True},
                    {"name": "EXIT_CODE", "value": exit_code or "-", "inline": True},
                    {"name": "EXIT_STATUS", "value": exit_status or "-", "inline": True},
                ],
            }
        ]
    }


def notify(
    argv: list[str],
    environ: dict[str, str],
    *,
    env_path: Path = ENV_PATH,
    post: Callable[..., object] = requests.post,
    now: datetime | None = None,
    host: str | None = None,
) -> int:
    url = read_webhook_url(env_path)
    if not url:
        return 0  # 웹훅 없으면 조용히 끝낸다
    service = argv[1].strip() if len(argv) > 1 and argv[1].strip() else "coinbot"
    payload = build_payload(
        service,
        environ.get("SERVICE_RESULT", ""),
        environ.get("EXIT_CODE", ""),
        environ.get("EXIT_STATUS", ""),
        now=now or datetime.now(KST),
        host=host if host is not None else socket.gethostname(),
    )
    try:
        post(url, json=payload, timeout=8)
    except Exception:
        return 0  # 예외 문구에 URL이 섞일 수 있어 출력하지 않는다
    return 0


def main() -> int:
    return notify(sys.argv, dict(os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
