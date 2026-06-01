"""
카카오 OAuth 최초 연동: 브라우저에서 받은 인가 코드로 access / refresh 토큰을 발급해 저장합니다.

사전 준비:
  - btc_live_trading/.env 에 KAKAO_REST_API_KEY, KAKAO_REDIRECT_URI 설정
  - 카카오 개발자 콘솔의 Redirect URI 가 위 값과 동일해야 합니다.

사용:
  python scripts/auth_kakao.py
  python scripts/auth_kakao.py --code "인가코드"
"""

from __future__ import annotations

import argparse
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

load_dotenv(LIVE / ".env")

from kakao_utils import (  # noqa: E402
    apply_token_response,
    exchange_authorization_code,
    get_redirect_uri,
)
from urllib.parse import quote  # noqa: E402


def _auth_url(client_id: str, redirect_uri: str) -> str:
    if not client_id or not redirect_uri:
        return ""
    return (
        "https://kauth.kakao.com/oauth/authorize?"
        f"client_id={client_id}&redirect_uri={quote(redirect_uri, safe='')}&response_type=code"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Kakao OAuth: authorization code → tokens")
    parser.add_argument("--code", default="", help="인가 코드 (생략 시 입력 프롬프트)")
    args = parser.parse_args()

    client_id = os.getenv("KAKAO_REST_API_KEY", "").strip()
    if not client_id:
        print("오류: KAKAO_REST_API_KEY 가 .env 에 없습니다.", file=sys.stderr)
        sys.exit(1)

    redirect_uri = get_redirect_uri()
    code = (args.code or "").strip()
    if not code:
        url = _auth_url(client_id, redirect_uri)
        if url:
            print("브라우저에서 아래 URL로 로그인 후 리다이렉트 URL의 code= 값을 복사하세요.")
            print(url)
        print(f"Redirect URI: {redirect_uri}")
        if not sys.stdin.isatty():
            print("비대화형 환경: py -3 scripts/auth_kakao.py --code \"<인가코드>\" 로 실행하세요.", file=sys.stderr)
            sys.exit(2)
        print("브라우저 리다이렉트 URL 의 code= 뒤 값을 붙여넣으세요.")
        code = input("Authorization code: ").strip()
    if not code:
        print("인가 코드가 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    try:
        data = exchange_authorization_code(client_id, redirect_uri, code)
    except Exception as exc:
        print(f"토큰 발급 실패: {exc}", file=sys.stderr)
        sys.exit(1)

    access = apply_token_response(data)
    if not access:
        print("응답에 access_token 이 없습니다.", file=sys.stderr)
        sys.exit(1)

    print("저장 완료: btc_live_trading/.env 및 btc_live_trading/kakao_code.json")
    print("이제 KAKAO_ACCESS_TOKEN / KAKAO_REFRESH_TOKEN 이 자동으로 유지됩니다.")


if __name__ == "__main__":
    main()
