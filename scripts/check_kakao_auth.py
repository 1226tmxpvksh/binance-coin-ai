"""
카카오 토큰 사용 가능 여부 점검 (exit 0=정상, 1=수동 인증 필요).

coinbot_watch.sh 등에서 호출한다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "btc_live_trading"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(LIVE) not in sys.path:
    sys.path.insert(0, str(LIVE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(LIVE / ".env", override=True)

from kakao_utils import (  # noqa: E402
    get_access_token,
    get_refresh_token,
    hydrate_tokens_from_json,
    kakao_api_allowed,
    refresh_kakao_access_token_sync,
    validate_access_token,
)


def _alerts_enabled() -> bool:
    if not kakao_api_allowed():
        return False
    return os.getenv("KAKAO_ALERTS_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _validate_access(access: str) -> bool:
    return validate_access_token(access)


def main() -> int:
    if not _alerts_enabled():
        return 0
    client_id = os.getenv("KAKAO_REST_API_KEY", "").strip()
    if not client_id:
        print("KAKAO_REST_API_KEY 없음", file=sys.stderr)
        return 1

    hydrate_tokens_from_json()
    access = get_access_token().strip()
    if access and _validate_access(access):
        return 0

    refresh = get_refresh_token().strip()
    if refresh and refresh != "your_refresh_token_here":
        try:
            new_access = refresh_kakao_access_token_sync(client_id, refresh)
            if new_access and _validate_access(new_access):
                return 0
        except Exception:
            pass

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
