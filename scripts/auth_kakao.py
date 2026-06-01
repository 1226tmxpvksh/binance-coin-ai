"""
카카오 OAuth 최초 연동: 브라우저에서 받은 인가 코드로 access / refresh 토큰을 발급해 저장합니다.

사전 준비:
  - btc_live_trading/.env 에 KAKAO_REST_API_KEY, KAKAO_REDIRECT_URI 설정
  - 카카오 개발자 콘솔의 Redirect URI 가 위 값과 동일해야 합니다.
  - 자동 모드(--auto): 콘솔에 http://127.0.0.1:8765/oauth 등록 권장

사용:
  python scripts/auth_kakao.py              # 인가 URL 출력 + 코드 입력
  python scripts/auth_kakao.py --auto       # 브라우저 열고 로컬 콜백으로 자동 수신
  python scripts/auth_kakao.py --code "인가코드"
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "btc_live_trading"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(LIVE) not in sys.path:
    sys.path.insert(0, str(LIVE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(LIVE / ".env", override=True)

from kakao_utils import (  # noqa: E402
    LOCALHOST_REDIRECT_URI,
    apply_token_response,
    capture_authorization_code_via_localhost,
    clear_kakao_auth_code,
    exchange_authorization_code,
    get_redirect_uri,
)


def _auth_url(client_id: str, redirect_uri: str) -> str:
    if not client_id or not redirect_uri:
        return ""
    return (
        "https://kauth.kakao.com/oauth/authorize?"
        f"client_id={client_id}&redirect_uri={quote(redirect_uri, safe='')}&response_type=code"
    )


def _extract_code(raw: str) -> str:
    """사용자가 인가코드만 붙여넣든, 전체 리다이렉트 URL을 붙여넣든 code= 값만 추출."""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return ""
    if "code=" in raw:
        from urllib.parse import urlparse, parse_qs

        # 전체 URL 또는 'code=...&...' 형태 모두 처리
        query = urlparse(raw).query or raw
        parsed = parse_qs(query)
        if parsed.get("code"):
            return str(parsed["code"][0]).strip()
        # urlparse 실패 시 단순 분리
        tail = raw.split("code=", 1)[1]
        return tail.split("&", 1)[0].strip()
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description="Kakao OAuth: authorization code → tokens")
    parser.add_argument("--code", default="", help="인가 코드 (생략 시 입력 프롬프트)")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="로컬 콜백(127.0.0.1:8765)으로 브라우저 인증 후 코드 자동 수신",
    )
    parser.add_argument("--no-browser", action="store_true", help="--auto 시 브라우저 자동 열기 생략")
    args = parser.parse_args()

    client_id = os.getenv("KAKAO_REST_API_KEY", "").strip()
    if not client_id:
        print("오류: KAKAO_REST_API_KEY 가 .env 에 없습니다.", file=sys.stderr)
        sys.exit(1)

    redirect_uri = get_redirect_uri()
    code = _extract_code(args.code)

    if not code and args.auto:
        auto_uri = os.getenv("KAKAO_REDIRECT_URI", LOCALHOST_REDIRECT_URI).strip() or LOCALHOST_REDIRECT_URI
        if "127.0.0.1" not in auto_uri and "localhost" not in auto_uri:
            print(
                f"자동 모드는 localhost redirect 가 필요합니다. .env 의 KAKAO_REDIRECT_URI 를\n"
                f"  {LOCALHOST_REDIRECT_URI}\n"
                f"로 설정하고 카카오 개발자 콘솔에도 동일하게 등록하세요.",
                file=sys.stderr,
            )
            sys.exit(2)
        redirect_uri = auto_uri
        print(f"Redirect URI: {redirect_uri}")
        print("브라우저에서 카카오 로그인을 완료하면 토큰이 자동 저장됩니다...")
        try:
            code = capture_authorization_code_via_localhost(
                client_id,
                redirect_uri=redirect_uri,
                open_browser=not args.no_browser,
            )
        except Exception as exc:
            print(f"자동 인증 실패: {exc}", file=sys.stderr)
            sys.exit(1)

    if not code:
        url = _auth_url(client_id, redirect_uri)
        if url:
            print("=" * 70)
            print("[1단계] 아래 URL을 브라우저에서 여세요(자동으로 열립니다):")
            print(url)
            print("-" * 70)
            print(f"등록되어 있어야 할 Redirect URI: {redirect_uri}")
            print("  ※ 브라우저에 KOE006이 뜨면 이 URI가 카카오 콘솔에 등록되지 않은 것입니다.")
            print("    developers.kakao.com → 내 앱 → 앱 설정 > 플랫폼 키 > REST API 키")
            print("    > 리다이렉트 URI 에 위 값을 똑같이 추가 후 다시 실행하세요.")
            print("=" * 70)
            if sys.stdin.isatty() and not args.no_browser:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
        if not sys.stdin.isatty():
            print('비대화형 환경: py -3 scripts/auth_kakao.py --code "<인가코드>" 로 실행하세요.', file=sys.stderr)
            sys.exit(2)
        print("[2단계] 로그인 후 이동된 주소창의 전체 URL(또는 code= 뒤 값)을 붙여넣으세요.")
        code = _extract_code(input("코드 또는 URL 붙여넣기: "))
    if not code:
        print("인가 코드가 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    try:
        data = exchange_authorization_code(client_id, redirect_uri, code)
    except Exception as exc:
        print(f"토큰 발급 실패: {exc}", file=sys.stderr)
        print(
            "인가 코드는 1회용·약 10분 유효입니다. 새 URL로 다시 발급받으세요.",
            file=sys.stderr,
        )
        clear_kakao_auth_code()
        sys.exit(1)

    access = apply_token_response(data)
    if not access:
        print("응답에 access_token 이 없습니다.", file=sys.stderr)
        sys.exit(1)

    clear_kakao_auth_code()
    print("저장 완료: btc_live_trading/.env 및 btc_live_trading/kakao_code.json")
    print("이제 KAKAO_ACCESS_TOKEN / KAKAO_REFRESH_TOKEN 이 자동으로 유지됩니다.")


if __name__ == "__main__":
    main()
