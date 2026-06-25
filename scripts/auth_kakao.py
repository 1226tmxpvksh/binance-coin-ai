"""
카카오 OAuth 최초 연동: 브라우저에서 받은 인가 코드로 access / refresh 토큰을 발급해 저장합니다.

사전 준비:
  - btc_live_trading/.env 에 KAKAO_REST_API_KEY, KAKAO_REDIRECT_URI 설정
  - 카카오 개발자 콘솔의 Redirect URI 가 위 값과 동일해야 합니다.

사용:
  python scripts/auth_kakao.py              # 대화형: URL 출력 + input() 대기
  python scripts/auth_kakao.py --auto       # 로컬 콜백(127.0.0.1:8765) 자동 수신
  python scripts/auth_kakao.py --code "인가코드"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

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
    kakao_api_allowed,
    open_kakao_auth_url,
)

_INTERACTIVE_INPUT_PROMPT = (
    "👉 위 링크를 클릭하여 로그인한 후, "
    "발급받은 인가 코드(또는 리다이렉트된 전체 URL)를 붙여넣고 Enter를 누르세요: "
)


def _auth_url(client_id: str, redirect_uri: str) -> str:
    if not client_id or not redirect_uri:
        return ""
    return (
        "https://kauth.kakao.com/oauth/authorize?"
        f"client_id={client_id}&redirect_uri={quote(redirect_uri, safe='')}&response_type=code"
    )


def _extract_code(raw: str) -> str:
    """인가 코드만 추출. code= 값, 전체 리다이렉트 URL, 순수 코드 모두 지원."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return ""

    # URL 또는 ?code= / &code= 형태
    if "code=" in text or "://" in text:
        parsed = urlparse(text)
        for candidate in (parsed.query, parsed.fragment, text.lstrip("?")):
            if not candidate:
                continue
            qs = parse_qs(candidate, keep_blank_values=False)
            if qs.get("code"):
                return unquote(str(qs["code"][0]).strip())
        if "code=" in text:
            tail = text.split("code=", 1)[1]
            code = tail.split("&", 1)[0].split("#", 1)[0].strip()
            return unquote(code)

    return unquote(text)


def _print_interactive_auth_banner(*, client_id: str, redirect_uri: str, auth_url: str) -> None:
    masked_id = f"{client_id[:4]}…{client_id[-4:]}" if len(client_id) > 10 else client_id
    print()
    print("=" * 72)
    print("  [카카오 대화형 인증] Interactive Auth")
    print("=" * 72)
    print(f"  REST API Key : {masked_id}")
    print(f"  Redirect URI : {redirect_uri}")
    print("-" * 72)
    print("  👇 아래 URL을 PC/휴대폰 브라우저에서 여세요:")
    print()
    print(f"  {auth_url}")
    print()
    print("=" * 72)
    print()


def _interactive_prompt_code(*, open_browser: bool) -> str:
    """터미널에 URL 출력 후 input()으로 인가 코드(또는 전체 URL) 대기."""
    if not sys.stdin.isatty():
        print(
            "비대화형 환경: py -3 scripts/auth_kakao.py --code \"<인가코드>\" 로 실행하세요.",
            file=sys.stderr,
        )
        sys.exit(2)

    client_id = os.getenv("KAKAO_REST_API_KEY", "").strip()
    redirect_uri = get_redirect_uri()
    url = _auth_url(client_id, redirect_uri)
    if not url:
        print("오류: KAKAO_REST_API_KEY 또는 KAKAO_REDIRECT_URI가 없습니다.", file=sys.stderr)
        sys.exit(1)

    _print_interactive_auth_banner(client_id=client_id, redirect_uri=redirect_uri, auth_url=url)
    if open_browser:
        open_kakao_auth_url(url)

    while True:
        raw = input(_INTERACTIVE_INPUT_PROMPT).strip()
        code = _extract_code(raw)
        if code:
            return code
        print("⚠️  인가 코드를 찾을 수 없습니다. code= 값 또는 리다이렉트 URL 전체를 다시 붙여넣으세요.")


def _exchange_and_save(client_id: str, redirect_uri: str, code: str) -> None:
    """인가 코드 → 토큰 교환 → 원자적 저장(apply_token_response / persist)."""
    try:
        data = exchange_authorization_code(client_id, redirect_uri, code)
    except Exception as exc:
        print(f"토큰 발급 실패: {exc}", file=sys.stderr)
        print(
            "인가 코드는 1회용·약 10분 유효입니다. 스크립트를 다시 실행해 새 URL로 발급받으세요.",
            file=sys.stderr,
        )
        clear_kakao_auth_code()
        sys.exit(1)

    access = apply_token_response(data)
    if not access:
        print("응답에 access_token 이 없습니다.", file=sys.stderr)
        sys.exit(1)

    clear_kakao_auth_code()
    print()
    print("✅ 저장 완료: btc_live_trading/.env 및 btc_live_trading/kakao_code.json")
    print("   KAKAO_ACCESS_TOKEN / KAKAO_REFRESH_TOKEN 이 자동으로 유지됩니다.")


def main() -> None:
    if not kakao_api_allowed():
        print(
            "오류: 환경 화이트리스트 불일치 — 카카오 인증은 hostname=example1, "
            "project=/home/bot2/Coin 에서만 실행할 수 있습니다.\n"
            "Vultr에서 bash ~/Coin/scripts/coinbot_watch.sh 를 사용하세요.",
            file=sys.stderr,
        )
        sys.exit(2)

    parser = argparse.ArgumentParser(description="Kakao OAuth: authorization code → tokens")
    parser.add_argument("--code", default="", help="인가 코드 (지정 시 비대화형)")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="로컬 콜백(127.0.0.1:8765)으로 브라우저 인증 후 코드 자동 수신",
    )
    parser.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 생략")
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

    # --code 없이 단독 실행 → 대화형 모드 (coinbot_watch.sh 가 이 경로 사용)
    if not code:
        code = _interactive_prompt_code(open_browser=not args.no_browser)

    if not code:
        print("인가 코드가 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    _exchange_and_save(client_id, redirect_uri, code)


if __name__ == "__main__":
    main()
