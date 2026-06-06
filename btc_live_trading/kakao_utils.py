"""
카카오 OAuth 토큰 저장소 및 갱신 유틸리티.

.env에는 KAKAO_REST_API_KEY, KAKAO_REDIRECT_URI를 두고,
access / refresh 토큰은 .env와 kakao_code.json에 동기화해 자동 관리한다.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# 작업 디렉터리(CWD)와 무관하게 항상 같은 파일을 읽고 쓰도록 절대경로로 고정한다.
_MODULE_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
KAKAO_CODE_JSON = Path(os.path.abspath(_MODULE_DIR / "kakao_code.json"))
DEFAULT_REDIRECT_URI = "https://example.com/oauth"
LOCALHOST_REDIRECT_URI = "http://127.0.0.1:8765/oauth"


def get_kakao_json_path() -> Path:
    return KAKAO_CODE_JSON


def _env_file_path() -> Path:
    return Path(os.path.abspath(_MODULE_DIR / ".env"))


def _atomic_write_text(path: Path, content: str) -> None:
    """임시 파일에 먼저 쓰고 os.replace로 교체 → 쓰기 중단 시에도 원본이 손상되지 않음."""
    target = Path(os.path.abspath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_kakao_", suffix=".swap")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)  # 같은 파일시스템 내 원자적 교체
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except OSError:
            pass
        raise


def get_redirect_uri() -> str:
    return (os.getenv("KAKAO_REDIRECT_URI") or DEFAULT_REDIRECT_URI).strip()


def _read_json_tokens() -> tuple[str, str]:
    if not KAKAO_CODE_JSON.exists():
        return "", ""
    try:
        data = json.loads(KAKAO_CODE_JSON.read_text(encoding="utf-8-sig"))
        return (
            str(data.get("access_token") or "").strip(),
            str(data.get("refresh_token") or "").strip(),
        )
    except Exception as exc:
        logger.warning("Could not read %s: %s", KAKAO_CODE_JSON, exc)
        return "", ""


def hydrate_tokens_from_json() -> None:
    """kakao_code.json 값을 os.environ에 병합(비어 있을 때만)."""
    ja, jr = _read_json_tokens()
    if not os.getenv("KAKAO_ACCESS_TOKEN", "").strip() and ja:
        os.environ["KAKAO_ACCESS_TOKEN"] = ja
    if not os.getenv("KAKAO_REFRESH_TOKEN", "").strip() and jr:
        os.environ["KAKAO_REFRESH_TOKEN"] = jr


def get_access_token() -> str:
    a = os.getenv("KAKAO_ACCESS_TOKEN", "").strip()
    if a:
        return a
    ja, _ = _read_json_tokens()
    return ja


def get_refresh_token() -> str:
    r = os.getenv("KAKAO_REFRESH_TOKEN", "").strip()
    if r and r != "your_refresh_token_here":
        return r
    _, jr = _read_json_tokens()
    return jr


def _write_kakao_code_json(access_token: str, refresh_token: str) -> bool:
    """kakao_code.json 갱신. 기존 파일 값과 병합해 어떤 토큰도 유실되지 않도록 한다.

    반환값: 저장 성공 여부. 쓰기 실패는 다음 갱신을 영구 차단하므로 명확히 로깅한다.
    """
    existing_access, existing_refresh = _read_json_tokens()
    eff_access = (access_token or existing_access or "").strip()
    eff_refresh = (refresh_token or existing_refresh or "").strip()

    payload: Dict[str, str] = {}
    if eff_access:
        payload["access_token"] = eff_access
    if eff_refresh:
        payload["refresh_token"] = eff_refresh
    if not payload:
        return False

    try:
        _atomic_write_text(
            KAKAO_CODE_JSON,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        logger.info("kakao_code.json 갱신 완료: %s", KAKAO_CODE_JSON)
        return True
    except Exception as exc:
        logger.error(
            "kakao_code.json 저장 실패(%s): %s — 다음 토큰 갱신 시 문제가 될 수 있으니 권한/디스크를 확인하세요.",
            KAKAO_CODE_JSON,
            exc,
        )
        return False


def _merge_env_file(updates: Dict[str, str]) -> None:
    env_path = _env_file_path()
    if not env_path.exists():
        logger.debug(".env not found at %s — skipping file update", env_path)
        return
    try:
        raw = env_path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        logger.error("Failed to read .env: %s", exc)
        return

    lines = raw.splitlines(keepends=True)
    keys_done: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        matched = False
        for key, val in updates.items():
            if key in keys_done:
                continue
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                if val:
                    out.append(f"{key}={val}\n")
                keys_done.add(key)
                matched = True
                break
        if not matched:
            out.append(line)

    for key, val in updates.items():
        if key not in keys_done and val:
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            out.append(f"{key}={val}\n")

    try:
        _atomic_write_text(env_path, "".join(out))
        logger.info(".env 토큰 키 갱신 완료: %s", env_path)
    except Exception as exc:
        logger.error(".env 저장 실패(%s): %s", env_path, exc)


def persist_kakao_tokens(access_token: str, refresh_token: Optional[str] = None) -> bool:
    """access/refresh 토큰을 os.environ, kakao_code.json, .env 세 곳에 동기화 저장.

    refresh_token이 None이면 기존 env/json의 refresh를 유지하고,
    값이 주어지면(카카오가 회전 발급한 새 refresh) 반드시 덮어써 저장한다.
    반환값: kakao_code.json 저장 성공 여부.
    """
    ja, jr_file = _read_json_tokens()

    if access_token:
        os.environ["KAKAO_ACCESS_TOKEN"] = access_token

    if refresh_token is not None:
        new_refresh = refresh_token.strip()
        prev_refresh = (os.getenv("KAKAO_REFRESH_TOKEN", "").strip() or jr_file)
        if new_refresh and new_refresh != prev_refresh:
            logger.info("카카오 refresh_token이 회전 발급되어 새 값을 저장합니다.")
        os.environ["KAKAO_REFRESH_TOKEN"] = new_refresh
        eff_refresh = new_refresh
    else:
        eff_refresh = os.getenv("KAKAO_REFRESH_TOKEN", "").strip() or jr_file

    eff_access = access_token or os.getenv("KAKAO_ACCESS_TOKEN", "").strip() or ja

    json_ok = _write_kakao_code_json(eff_access, eff_refresh)

    file_updates: Dict[str, str] = {}
    if eff_access:
        file_updates["KAKAO_ACCESS_TOKEN"] = eff_access
    if eff_refresh:
        file_updates["KAKAO_REFRESH_TOKEN"] = eff_refresh
    if file_updates:
        _merge_env_file(file_updates)

    return json_ok


def clear_env_key(key: str) -> None:
    """.env 키 값을 비우고 os.environ 에서 제거."""
    os.environ.pop(key, None)
    _merge_env_file({key: ""})


def clear_kakao_auth_code() -> None:
    clear_env_key("KAKAO_AUTH_CODE")


def apply_token_response(token_data: Dict[str, Any]) -> str:
    """OAuth/refresh 응답 JSON에서 토큰을 꺼내 저장하고 access_token 문자열을 반환."""
    access = str(token_data.get("access_token") or "").strip()
    new_refresh = token_data.get("refresh_token")
    refresh_opt: Optional[str]
    if new_refresh is not None:
        refresh_opt = str(new_refresh).strip()
    else:
        refresh_opt = None
    saved = persist_kakao_tokens(access, refresh_opt)
    if refresh_opt:
        logger.info("카카오 토큰 갱신 응답에 새 refresh_token 포함 → 저장 %s", "성공" if saved else "실패")
    return access


def exchange_authorization_code(
    client_id: str,
    redirect_uri: str,
    code: str,
) -> Dict[str, Any]:
    token_url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code.strip(),
    }
    response = requests.post(token_url, data=data, timeout=15)
    if response.status_code != 200:
        try:
            err = response.json()
            logger.error("Kakao token exchange failed: %s", err.get("error_description", err))
        except Exception:
            logger.error("Kakao token exchange failed: HTTP %s", response.status_code)
        response.raise_for_status()
    return response.json()


def capture_authorization_code_via_localhost(
    client_id: str,
    *,
    redirect_uri: str | None = None,
    port: int = 8765,
    timeout_sec: float = 180.0,
    open_browser: bool = True,
) -> str:
    """
    로컬 HTTP 콜백으로 인가 코드 수신. redirect_uri 는 카카오 콘솔 등록값과 동일해야 함.
    기본: http://127.0.0.1:8765/oauth
    """
    import http.server
    import socketserver
    import threading
    import webbrowser
    from urllib.parse import parse_qs, quote, urlparse

    uri = (redirect_uri or LOCALHOST_REDIRECT_URI).strip()
    parsed = urlparse(uri)
    path = parsed.path or "/oauth"
    host = parsed.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError(f"로컬 콜백 redirect_uri 만 지원합니다: {uri}")

    auth_code: dict[str, str] = {"value": ""}
    error_msg: dict[str, str] = {"value": ""}
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            req_path = urlparse(self.path).path
            if req_path != path:
                self.send_error(404)
                return
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("error"):
                error_msg["value"] = str(qs["error"][0])
            auth_code["value"] = str(qs.get("code", [""])[0]).strip()
            body = (
                "<html><body><h2>카카오 인증 완료</h2>"
                "<p>이 창을 닫고 터미널로 돌아가세요.</p></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, format: str, *args: object) -> None:
            return

    with socketserver.TCPServer(("127.0.0.1", port), _Handler) as httpd:
        httpd.timeout = 1.0
        auth_url = (
            "https://kauth.kakao.com/oauth/authorize?"
            f"client_id={client_id}&redirect_uri={quote(uri, safe='')}&response_type=code"
        )
        logger.info("카카오 로컬 OAuth 대기: %s (redirect_uri=%s)", auth_url, uri)
        if open_browser:
            try:
                webbrowser.open(auth_url)
            except Exception as exc:
                logger.warning("브라우저 자동 열기 실패: %s", exc)
        deadline = __import__("time").time() + timeout_sec
        while not done.is_set() and __import__("time").time() < deadline:
            httpd.handle_request()

    if error_msg["value"]:
        raise RuntimeError(f"카카오 OAuth 거부: {error_msg['value']}")
    if not auth_code["value"]:
        raise TimeoutError(
            f"{timeout_sec:.0f}초 내 인가 코드를 받지 못했습니다. "
            "카카오 개발자 콘솔 Redirect URI에 아래 값이 등록되어 있는지 확인하세요: "
            f"{uri}"
        )
    return auth_code["value"]


def refresh_access_token_request(client_id: str, refresh_token: str) -> Dict[str, Any]:
    token_url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token.strip(),
    }
    response = requests.post(token_url, data=data, timeout=15)
    if response.status_code != 200:
        try:
            err = response.json()
            desc = str(err.get("error_description") or err.get("error") or "")
            if "expired_or_invalid_refresh_token" in desc or err.get("error") == "invalid_grant":
                logger.debug("Kakao refresh token expired: %s", desc or err)
            else:
                logger.error("Kakao token refresh failed: %s", err.get("error_description", err))
        except Exception:
            logger.error("Kakao token refresh failed: HTTP %s", response.status_code)
        response.raise_for_status()
    return response.json()
