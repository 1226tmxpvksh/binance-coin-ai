"""
카카오톡 메시지 1회 발송 진단 스크립트.

현재 btc_live_trading/.env + kakao_code.json 토큰으로
"카카오 API 테스트 메시지입니다" 를 1회 보내고,
HTTP 상태·응답 본문·토큰 상태를 터미널에 전부 출력한다.

사용 (서버 bot2):
  cd ~/Coin
  source ~/venv/bin/activate
  python scripts/test_kakao_send.py

※ 카카오 API는 hostname=example1 + project=/home/bot2/Coin 에서만 허용된다.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "btc_live_trading"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(LIVE / ".env", override=True)

import requests  # noqa: E402

from legacy.kakao.kakao_utils import (  # noqa: E402
    get_access_expires_at,
    get_access_token,
    get_refresh_expires_at,
    get_refresh_token,
    get_refresh_token_expires_in_remaining,
    hydrate_tokens_from_json,
    is_access_token_locally_fresh,
    kakao_api_allowed,
    kakao_env_block_reason,
    validate_access_token,
)
from legacy.kakao.kakao_notifier import KakaoNotifier, KAKAO_MEMO_API_URL  # noqa: E402

KST = timezone(timedelta(hours=9))


def _mask(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return "(없음)"
    if len(t) <= 12:
        return f"{t[:2]}…{t[-2:]}(len={len(t)})"
    return f"{t[:6]}…{t[-4:]}(len={len(t)})"


def _print_token_state() -> None:
    hydrate_tokens_from_json()
    access = get_access_token().strip()
    refresh = get_refresh_token().strip()
    expires_at = get_access_expires_at()
    refresh_expires_at = get_refresh_expires_at()
    refresh_left = get_refresh_token_expires_in_remaining()
    local_fresh = is_access_token_locally_fresh()
    print("=== 토큰 상태 ===")
    print(f"  access_token : {_mask(access)}")
    print(f"  refresh_token: {_mask(refresh)}")
    print(f"  local_fresh  : {local_fresh}")
    if expires_at > 0:
        exp_dt = datetime.fromtimestamp(expires_at, tz=KST)
        print(f"  access expires_at : {exp_dt.strftime('%Y-%m-%d %H:%M:%S KST')} ({int(expires_at - time.time())}s)")
    else:
        print("  access expires_at : (없음)")
    if refresh_expires_at > 0:
        rexp_dt = datetime.fromtimestamp(refresh_expires_at, tz=KST)
        left = refresh_left if refresh_left is not None else int(refresh_expires_at - time.time())
        days = left / 86400.0
        print(
            f"  refresh expires_at: {rexp_dt.strftime('%Y-%m-%d %H:%M:%S KST')} "
            f"(remaining={left}s / {days:.1f}일)"
        )
    else:
        print("  refresh expires_at: (없음 — 재인증 후 refresh_token_expires_in 저장됨)")
    if access and kakao_api_allowed():
        try:
            ok = validate_access_token(access)
            print(f"  HTTP validate: {'유효' if ok else '무효/만료'}")
        except Exception as exc:
            print(f"  HTTP validate: 예외 {type(exc).__name__}: {exc}")
    else:
        print("  HTTP validate: 스킵 (토큰 없음 또는 화이트리스트 차단)")


def _raw_post_once(access: str, text: str) -> None:
    """Notifier 우회 — raw HTTP 응답을 그대로 출력."""
    print("=== Raw HTTP 요청 ===")
    print(f"  URL: {KAKAO_MEMO_API_URL}")
    payload = {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": "https://www.binance.com",
            "mobile_web_url": "https://www.binance.com",
        },
    }
    try:
        response = requests.post(
            KAKAO_MEMO_API_URL,
            headers={"Authorization": f"Bearer {access}"},
            data={"template_object": json.dumps(payload, ensure_ascii=False)},
            timeout=15,
        )
    except requests.Timeout as exc:
        print(f"[CRITICAL KAKAO EXCEPTION] Timeout: {exc}")
        return
    except requests.ConnectionError as exc:
        print(f"[CRITICAL KAKAO EXCEPTION] ConnectionError: {exc}")
        return
    except Exception as exc:
        print(f"[CRITICAL KAKAO EXCEPTION] {type(exc).__name__}: {exc}")
        return

    print(f"  HTTP status : {response.status_code}")
    print(f"  response.headers Content-Type: {response.headers.get('Content-Type', '')}")
    print("  response.text:")
    print(response.text or "(빈 본문)")
    try:
        print("  response.json():")
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except Exception:
        print("  response.json(): (파싱 불가)")

    if response.status_code != 200:
        print(
            f"[CRITICAL KAKAO ERROR] 메시지 전송 실패! "
            f"HTTP: {response.status_code}, 응답내용: {response.text}"
        )


def main() -> int:
    print("=== test_kakao_send.py ===")
    print(f"시간: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    print(f".env : {LIVE / '.env'}")
    print(f"json : {LIVE / 'kakao_code.json'}")
    print()

    if not kakao_api_allowed():
        reason = kakao_env_block_reason() or "환경 불일치"
        print(f"[CRITICAL KAKAO ERROR] 화이트리스트 차단 — {reason}")
        print("이 스크립트는 Vultr(example1) /home/bot2/Coin 에서만 실제 발송됩니다.")
        _print_token_state()
        return 2

    rest = (os.getenv("KAKAO_REST_API_KEY") or "").strip()
    if not rest or rest == "your_rest_api_key_here":
        print("[CRITICAL KAKAO ERROR] KAKAO_REST_API_KEY 가 없습니다.")
        return 2

    _print_token_state()
    print()

    hydrate_tokens_from_json()
    access = get_access_token().strip()
    title = "카카오 API 테스트"
    body = (
        "카카오 API 테스트 메시지입니다\n"
        f"시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}"
    )
    full_text = f"{title}\n{body}"

    if access:
        _raw_post_once(access, full_text)
        print()

    print("=== KakaoNotifier.send_message() ===")
    notifier = KakaoNotifier(enabled=True, rest_api_key=rest)
    ok = notifier.send_message(title, body)
    print(f"  결과: {'성공' if ok else '실패'}")
    print()
    _print_token_state()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
