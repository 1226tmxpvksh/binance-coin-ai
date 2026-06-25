"""
카카오 OAuth 토큰 저장소 및 갱신 유틸리티.

.env에는 KAKAO_REST_API_KEY, KAKAO_REDIRECT_URI를 두고,
access / refresh 토큰은 .env와 kakao_code.json에 동기화해 자동 관리한다.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_KAKAO_REFRESH_LOCK = threading.Lock()

# 작업 디렉터리(CWD)와 무관하게 항상 같은 파일을 읽고 쓰도록 절대경로로 고정한다.
_MODULE_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
KAKAO_CODE_JSON = Path(os.path.abspath(_MODULE_DIR / "kakao_code.json"))
DEFAULT_REDIRECT_URI = "https://example.com/oauth"
LOCALHOST_REDIRECT_URI = "http://127.0.0.1:8765/oauth"

# 정품 Vultr 운영 환경만 카카오 API 허용 — .env 오버라이드 불가(하드코딩)
ALLOWED_KAKAO_HOSTNAME = "example1"
ALLOWED_KAKAO_PROJECT_ROOT = "/home/bot2/Coin"


def _normalize_project_path(path: str) -> str:
    return os.path.realpath(path).replace("\\", "/").rstrip("/")

_KAKAO_ENV_BLOCK_LOGGED = False


def get_project_root() -> Path:
    """btc_live_trading 상위 = Coin 프로젝트 루트 (CWD 무관)."""
    return Path(os.path.realpath(str(_MODULE_DIR.parent)))


def is_windows_local_host() -> bool:
    """로컬 Windows PC 여부 (webbrowser.open 바탕화면 .url 방지용)."""
    return os.name == "nt"


def kakao_env_block_reason() -> str:
    """화이트리스트 불일치 사유(로그용). 일치하면 빈 문자열."""
    hostname = socket.gethostname().strip()
    project_root = _normalize_project_path(str(get_project_root()))
    allowed_root = ALLOWED_KAKAO_PROJECT_ROOT
    reasons: list[str] = []
    if hostname != ALLOWED_KAKAO_HOSTNAME:
        reasons.append(f"hostname={hostname!r} (허용: {ALLOWED_KAKAO_HOSTNAME!r})")
    if project_root != allowed_root:
        reasons.append(
            f"project_root={project_root!r} (허용: {allowed_root!r})"
        )
    return "; ".join(reasons)


def is_kakao_whitelisted_environment() -> bool:
    """hostname==example1 이고 프로젝트 루트==/home/bot2/Coin 일 때만 True."""
    return not kakao_env_block_reason()


def kakao_api_allowed() -> bool:
    """카카오 API 호출·토큰 저장 허용 여부. 정품 서버·정품 경로에서만 True."""
    return is_kakao_whitelisted_environment()


def _log_kakao_env_block_once(action: str) -> None:
    global _KAKAO_ENV_BLOCK_LOGGED
    if not _KAKAO_ENV_BLOCK_LOGGED:
        reason = kakao_env_block_reason() or "환경 불일치"
        logger.warning(
            "카카오 %s 차단(환경 화이트리스트) — %s. "
            "hostname=%s, project=/home/bot2/Coin 에서만 허용.",
            action,
            reason,
            ALLOWED_KAKAO_HOSTNAME,
        )
        _KAKAO_ENV_BLOCK_LOGGED = True


def open_kakao_auth_url(url: str, *, open_browser: bool = True) -> None:
    """카카오 인증 URL 표시. Windows에서는 webbrowser.open 미사용(바탕화면 .url 생성 방지)."""
    if is_windows_local_host():
        logger.info(
            "Windows: 브라우저 자동 열기 생략 — URL을 터미널에서 복사해 수동으로 여세요 "
            "(webbrowser.open 시 바탕화면에 .url 바로가기가 생성될 수 있음)."
        )
        return
    if not open_browser:
        return
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception as exc:
        logger.warning("브라우저 자동 열기 실패: %s", exc)


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


def _mask_token(token: str) -> str:
    """로그용 토큰 마스킹: 앞 6자 + …(길이) + 뒤 4자."""
    t = (token or "").strip()
    if not t:
        return "(없음)"
    if len(t) <= 12:
        return f"{t[:2]}…{t[-2:]}(len={len(t)})"
    return f"{t[:6]}…{t[-4:]}(len={len(t)})"


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
    """kakao_code.json 값을 os.environ에 동기화한다.

    json은 원자적 저장+저장 후 검증을 거치는 '정본'이므로, .env와 값이
    어긋나면 json을 우선한다(회전 후 .env만 낡은 경우 2~3일 좀비 버그 방지).
    """
    ja, jr = _read_json_tokens()
    if ja:
        os.environ["KAKAO_ACCESS_TOKEN"] = ja
    if jr:
        os.environ["KAKAO_REFRESH_TOKEN"] = jr


def get_access_token() -> str:
    ja, _ = _read_json_tokens()
    a_env = os.getenv("KAKAO_ACCESS_TOKEN", "").strip()
    if ja:
        if a_env and a_env != ja:
            logger.warning("KAKAO_ACCESS_TOKEN(.env)과 kakao_code.json 불일치 — json 값을 사용합니다.")
        return ja
    return a_env


def get_refresh_token() -> str:
    _, jr = _read_json_tokens()
    r_env = os.getenv("KAKAO_REFRESH_TOKEN", "").strip()
    if jr and jr != "your_refresh_token_here":
        if r_env and r_env != jr:
            logger.warning("KAKAO_REFRESH_TOKEN(.env)과 kakao_code.json 불일치 — json 값을 사용합니다.")
        return jr
    if r_env and r_env != "your_refresh_token_here":
        return r_env
    return jr or ""


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

    refresh_token(마스터 열쇠) 처리 규칙:
      * None 또는 빈 문자열  → 회전 없음. 기존 env/json의 refresh_token을 절대 유실하지 않고 유지.
      * 비어있지 않은 값      → 회전 발급. 무조건 새 값으로 기존 값을 대체 저장.

    저장 후 kakao_code.json을 다시 읽어 refresh_token이 의도대로 기록되었는지 검증한다.
    반환값: refresh_token 저장·검증 성공 여부(다음 갱신을 보장하는 핵심 지표).
    """
    if not kakao_api_allowed():
        _log_kakao_env_block_once("토큰 저장")
        return False
    ja, jr_file = _read_json_tokens()
    prev_refresh = (os.getenv("KAKAO_REFRESH_TOKEN", "").strip() or jr_file).strip()

    if access_token:
        os.environ["KAKAO_ACCESS_TOKEN"] = access_token

    # --- 마스터 열쇠(refresh_token) 회전 분기 ---
    new_refresh = (refresh_token or "").strip() if refresh_token is not None else ""
    if new_refresh:
        if new_refresh != prev_refresh:
            logger.info(
                "🔑 카카오 마스터 열쇠 회전 감지: %s → %s (새 refresh_token 저장)",
                _mask_token(prev_refresh),
                _mask_token(new_refresh),
            )
        else:
            logger.info("카카오 refresh_token 재발급(기존과 동일 값) 확인: %s", _mask_token(new_refresh))
        eff_refresh = new_refresh
    else:
        # 회전 없음 → 기존 유효 refresh_token을 반드시 유지
        eff_refresh = prev_refresh
        if eff_refresh:
            logger.debug("카카오 응답에 refresh_token 없음 → 기존 마스터 열쇠 유지: %s", _mask_token(eff_refresh))

    if eff_refresh:
        os.environ["KAKAO_REFRESH_TOKEN"] = eff_refresh
    # eff_refresh가 비어 있으면 기존 env 값을 굳이 지우지 않는다(유실 방지).

    eff_access = access_token or os.getenv("KAKAO_ACCESS_TOKEN", "").strip() or ja

    json_ok = _write_kakao_code_json(eff_access, eff_refresh)

    file_updates: Dict[str, str] = {}
    if eff_access:
        file_updates["KAKAO_ACCESS_TOKEN"] = eff_access
    if eff_refresh:
        file_updates["KAKAO_REFRESH_TOKEN"] = eff_refresh
    if file_updates:
        _merge_env_file(file_updates)

    # --- 저장 검증: 파일에 실제로 의도한 refresh_token이 기록됐는지 재확인 ---
    refresh_ok = True
    if eff_refresh:
        _, saved_refresh = _read_json_tokens()
        refresh_ok = (saved_refresh == eff_refresh)
        if refresh_ok:
            logger.info("✅ refresh_token 저장 검증 통과: %s (kakao_code.json)", _mask_token(eff_refresh))
        else:
            logger.error(
                "❌ refresh_token 저장 검증 실패! 파일=%s, 의도=%s — 다음 갱신이 실패할 수 있습니다.",
                _mask_token(saved_refresh),
                _mask_token(eff_refresh),
            )

    # --- os.environ 즉시 동기화: 파일 정본 → 메모리 (내부 Replay 방지) ---
    saved_access, saved_refresh_final = _read_json_tokens()
    if saved_access:
        os.environ["KAKAO_ACCESS_TOKEN"] = saved_access
    elif eff_access:
        os.environ["KAKAO_ACCESS_TOKEN"] = eff_access
    if saved_refresh_final:
        os.environ["KAKAO_REFRESH_TOKEN"] = saved_refresh_final
    elif eff_refresh:
        os.environ["KAKAO_REFRESH_TOKEN"] = eff_refresh

    return json_ok and refresh_ok


def clear_env_key(key: str) -> None:
    """.env 키 값을 비우고 os.environ 에서 제거."""
    os.environ.pop(key, None)
    _merge_env_file({key: ""})


def clear_kakao_auth_code() -> None:
    clear_env_key("KAKAO_AUTH_CODE")


def apply_token_response(token_data: Dict[str, Any]) -> str:
    """OAuth/refresh 응답 JSON에서 토큰을 꺼내 저장하고 access_token 문자열을 반환.

    카카오 갱신 응답의 refresh_token 필드 유무를 명확히 분기한다.
      * 필드가 있고 값이 비어있지 않으면 → 회전으로 간주, 새 값으로 대체 저장.
      * 필드가 없거나 빈 값이면        → 회전 없음, 기존 refresh_token 유지.
    """
    access = str(token_data.get("access_token") or "").strip()
    raw_refresh = token_data.get("refresh_token")
    has_new_refresh = raw_refresh is not None and str(raw_refresh).strip() != ""

    if has_new_refresh:
        rotated = str(raw_refresh).strip()
        logger.info("카카오 갱신 응답에 refresh_token 포함 → 마스터 열쇠 회전 처리 시작")
        ok = persist_kakao_tokens(access, rotated)
        logger.info("카카오 마스터 열쇠 회전 저장 결과: %s", "성공" if ok else "실패")
    else:
        logger.debug("카카오 갱신 응답에 refresh_token 없음 → 기존 마스터 열쇠 유지 처리")
        ok = persist_kakao_tokens(access, None)
    if not ok:
        logger.error(
            "카카오 토큰 저장/검증 실패 — 다음 갱신 시 invalid_grant가 날 수 있습니다. "
            "kakao_code.json/.env 권한·디스크를 확인하세요."
        )
    return access


def exchange_authorization_code(
    client_id: str,
    redirect_uri: str,
    code: str,
) -> Dict[str, Any]:
    if not kakao_api_allowed():
        _log_kakao_env_block_once("인가 코드 교환")
        raise RuntimeError(
            "환경 화이트리스트 불일치 — 카카오 OAuth는 hostname=example1, "
            "project=/home/bot2/Coin 에서만 실행할 수 있습니다. "
            "Vultr에서 bash ~/Coin/scripts/coinbot_watch.sh 를 사용하세요."
        )
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
    from urllib.parse import parse_qs, quote, urlparse

    if not kakao_api_allowed():
        _log_kakao_env_block_once("로컬 콜백 인증")
        raise RuntimeError(
            "환경 화이트리스트 불일치 — 카카오 로컬 콜백 인증은 hostname=example1, "
            "project=/home/bot2/Coin 에서만 사용할 수 있습니다. "
            "Vultr에서 bash ~/Coin/scripts/coinbot_watch.sh 를 사용하세요."
        )

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
            open_kakao_auth_url(auth_url)
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


def validate_access_token(access_token: str) -> bool:
    """카카오 access_token 유효성(HTTP 200) 확인."""
    token = (access_token or "").strip()
    if not token or not kakao_api_allowed():
        return False
    try:
        response = requests.get(
            "https://kapi.kakao.com/v1/user/access_token_info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        return response.status_code == 200
    except requests.RequestException:
        return bool(token)


def refresh_kakao_access_token_sync(client_id: str, refresh_token: str = "") -> str:
    """Thread-safe 카카오 access_token 갱신(Double-checked locking).

    여러 스레드/코루틴이 동시에 만료를 감지해도 Refresh HTTP 호출은 1회만 수행하고,
    대기 스레드는 락 해제 후 이미 저장된 access_token을 재사용한다.
    """
    if not kakao_api_allowed():
        _log_kakao_env_block_once("토큰 갱신")
        return ""

    hydrate_tokens_from_json()
    access = get_access_token().strip()
    if access and validate_access_token(access):
        return access

    rt = (refresh_token or get_refresh_token()).strip()
    if not client_id or not rt:
        return access

    with _KAKAO_REFRESH_LOCK:
        hydrate_tokens_from_json()
        access = get_access_token().strip()
        if access and validate_access_token(access):
            logger.debug("카카오 refresh 대기 — 다른 스레드가 갱신한 access_token 사용")
            return access

        token_data = refresh_access_token_request(client_id, rt)
        new_access = apply_token_response(token_data)
        if new_access:
            logger.info("카카오 액세스 토큰 자동 갱신 완료 (thread-safe)")
        return new_access or ""


def refresh_access_token_request(client_id: str, refresh_token: str) -> Dict[str, Any]:
    if not kakao_api_allowed():
        _log_kakao_env_block_once("토큰 갱신")
        raise RuntimeError(
            "환경 화이트리스트 불일치 — 카카오 토큰 갱신은 hostname=example1, "
            "project=/home/bot2/Coin 에서만 실행할 수 있습니다."
        )
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
