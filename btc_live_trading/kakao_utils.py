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
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_KAKAO_REFRESH_LOCK = threading.Lock()
_KAKAO_REFRESH_LISTENER: Callable[[], None] | None = None
_KAKAO_INVALID_GRANT_LISTENER: Callable[[str, str], None] | None = None

# 로컬 만료 판정: 만료 이 초 전부터 '만료 임박'으로 간주하고 갱신을 허용한다.
ACCESS_TOKEN_EXPIRY_MARGIN_SECONDS = 300
# refresh_token(마스터 열쇠) 만료 임박 경고 기준(일)
REFRESH_TOKEN_WARN_DAYS = 7
_REFRESH_EXPIRY_WARNED = False


def register_kakao_token_refresh_listener(callback: Callable[[], None] | None) -> None:
    """HTTP refresh 성공 시 호출할 콜백 등록(알림 등). None이면 해제."""
    global _KAKAO_REFRESH_LISTENER
    _KAKAO_REFRESH_LISTENER = callback


def register_kakao_invalid_grant_listener(
    callback: Callable[[str, str], None] | None,
) -> None:
    """refresh HTTP 가 invalid_grant 이면 호출할 콜백 등록(매매 게이트 차단 등).

    callback(error_code, error_description). None이면 해제.
    """
    global _KAKAO_INVALID_GRANT_LISTENER
    _KAKAO_INVALID_GRANT_LISTENER = callback


def _notify_kakao_token_http_refreshed() -> None:
    cb = _KAKAO_REFRESH_LISTENER
    if cb is None:
        return
    try:
        cb()
    except Exception:
        logger.debug("카카오 refresh 리스너 실행 실패", exc_info=True)


def _notify_kakao_invalid_grant(error_code: str, error_description: str) -> None:
    cb = _KAKAO_INVALID_GRANT_LISTENER
    if cb is None:
        return
    try:
        cb(error_code or "", error_description or "")
    except Exception:
        logger.debug("카카오 invalid_grant 리스너 실행 실패", exc_info=True)

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


def _read_json_payload() -> Dict[str, Any]:
    if not KAKAO_CODE_JSON.exists():
        return {}
    try:
        data = json.loads(KAKAO_CODE_JSON.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Could not read %s: %s", KAKAO_CODE_JSON, exc)
        return {}


def _read_json_tokens() -> tuple[str, str]:
    data = _read_json_payload()
    return (
        str(data.get("access_token") or "").strip(),
        str(data.get("refresh_token") or "").strip(),
    )


def get_access_expires_at() -> float:
    """kakao_code.json 의 access 만료 시각(unix). 없으면 0."""
    data = _read_json_payload()
    raw = data.get("expires_at") or data.get("access_expires_at") or 0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def get_refresh_expires_at() -> float:
    """kakao_code.json 의 refresh 만료 시각(unix). 없으면 0."""
    data = _read_json_payload()
    raw = data.get("refresh_expires_at") or 0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def get_refresh_token_expires_in_remaining() -> int | None:
    """저장된 refresh_expires_at 기준 남은 초. 모르면 None."""
    expires_at = get_refresh_expires_at()
    if expires_at <= 0:
        return None
    return int(expires_at - time.time())


def is_access_token_locally_fresh(*, margin_seconds: int | None = None) -> bool:
    """HTTP 없이 로컬 expires_at 으로 액세스 토큰 유효 여부 판정.

    카톡 자동로그인처럼 '만료 전이면 그대로 사용, 만료면 갱신' 정책의 핵심.
    expires_at 이 없으면 False(갱신/로그인 경로로 유도).
    """
    access = get_access_token().strip()
    if not access:
        return False
    expires_at = get_access_expires_at()
    if expires_at <= 0:
        return False
    margin = ACCESS_TOKEN_EXPIRY_MARGIN_SECONDS if margin_seconds is None else max(0, int(margin_seconds))
    return time.time() < (expires_at - margin)


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


def _read_env_key_values(keys: list[str]) -> Dict[str, str]:
    """`.env`에서 지정 키 값을 다시 읽는다(저장 검증용)."""
    env_path = _env_file_path()
    found = {k: "" for k in keys}
    if not env_path.exists():
        return found
    try:
        raw = env_path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        logger.error(".env 재읽기 실패(%s): %s", env_path, exc)
        return found
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for key in keys:
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                found[key] = stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return found


def _write_kakao_code_json(
    access_token: str,
    refresh_token: str,
    *,
    expires_at: float | None = None,
    refresh_expires_at: float | None = None,
    refresh_token_expires_in: int | None = None,
    rotate_refresh: bool = True,
) -> bool:
    """kakao_code.json 갱신. refresh 미회전 시 기존 refresh_token을 절대 유실하지 않는다.

    rotate_refresh=False 이면 refresh_token 인자를 무시하고 파일의 기존 값을 유지한다.
    rotate_refresh=True 이어도 새 refresh가 비어 있으면 기존 값을 유지한다(빈칸 덮어쓰기 금지).
    """
    existing = _read_json_payload()
    existing_access = str(existing.get("access_token") or "").strip()
    existing_refresh = str(existing.get("refresh_token") or "").strip()
    eff_access = (access_token or existing_access or "").strip()

    new_refresh = (refresh_token or "").strip()
    if rotate_refresh and new_refresh:
        eff_refresh = new_refresh
    else:
        # 카카오가 refresh_token 필드를 생략하거나 빈 값을 준 경우 — 기존 값 유지
        eff_refresh = existing_refresh

    payload: Dict[str, Any] = {}
    if eff_access:
        payload["access_token"] = eff_access
    # refresh가 한 번이라도 있었다면 키를 절대 빼지 않는다(생략 = 삭제와 동일해짐).
    if eff_refresh:
        payload["refresh_token"] = eff_refresh
    elif existing_refresh:
        payload["refresh_token"] = existing_refresh
        eff_refresh = existing_refresh

    if expires_at is not None and expires_at > 0:
        payload["expires_at"] = float(expires_at)
    else:
        prev_exp = existing.get("expires_at") or existing.get("access_expires_at")
        try:
            prev_f = float(prev_exp or 0)
        except (TypeError, ValueError):
            prev_f = 0.0
        if prev_f > 0 and eff_access == existing_access:
            payload["expires_at"] = prev_f

    # refresh 만료: 새 refresh_token_expires_in 이 있으면 갱신, 없으면 기존 유지
    eff_refresh_expires_at = 0.0
    if refresh_expires_at is not None and float(refresh_expires_at) > 0:
        eff_refresh_expires_at = float(refresh_expires_at)
    elif refresh_token_expires_in is not None:
        try:
            rtei = int(refresh_token_expires_in)
        except (TypeError, ValueError):
            rtei = 0
        if rtei > 0:
            eff_refresh_expires_at = time.time() + rtei
            payload["refresh_token_expires_in"] = rtei
    if eff_refresh_expires_at > 0:
        payload["refresh_expires_at"] = eff_refresh_expires_at
    else:
        try:
            prev_r = float(existing.get("refresh_expires_at") or 0)
        except (TypeError, ValueError):
            prev_r = 0.0
        if prev_r > 0:
            payload["refresh_expires_at"] = prev_r
        prev_in = existing.get("refresh_token_expires_in")
        if prev_in is not None:
            try:
                payload["refresh_token_expires_in"] = int(prev_in)
            except (TypeError, ValueError):
                pass

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
            "[ERROR] 토큰 파일 덮어쓰기 실패! kakao_code.json 저장 실패(%s): %s — "
            "권한/디스크/용량을 확인하세요. 원인=%s",
            KAKAO_CODE_JSON,
            type(exc).__name__,
            exc,
        )
        return False


def _merge_env_file(updates: Dict[str, str]) -> bool:
    """`.env` 키를 원자적으로 갱신. 성공 시 True.

    값이 빈 문자열인 키는 기존 라인을 삭제하지 않고 그대로 둔다
    (KAKAO_REFRESH_TOKEN 이 None/빈칸으로 날아가는 사고 방지).
    """
    env_path = _env_file_path()
    if not env_path.exists():
        logger.error(
            "[ERROR] 토큰 파일 덮어쓰기 실패! .env 파일이 없습니다: %s — "
            "카카오 토큰을 디스크에 저장할 수 없습니다.",
            env_path,
        )
        return False
    try:
        raw = env_path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        logger.error(
            "[ERROR] 토큰 파일 덮어쓰기 실패! .env 읽기 실패(%s): %s — 원인=%s",
            env_path,
            type(exc).__name__,
            exc,
        )
        return False

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
                else:
                    # 빈 값으로 덮어쓰지 않음 — 기존 라인 유지
                    out.append(line)
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
        return True
    except Exception as exc:
        logger.error(
            "[ERROR] 토큰 파일 덮어쓰기 실패! .env 저장 실패(%s): %s — 원인=%s",
            env_path,
            type(exc).__name__,
            exc,
        )
        return False


def persist_kakao_tokens(
    access_token: str,
    refresh_token: Optional[str] = None,
    *,
    expires_in: int | None = None,
    expires_at: float | None = None,
    refresh_token_expires_in: int | None = None,
    rotate_refresh: bool | None = None,
) -> bool:
    """access/refresh 토큰을 os.environ, kakao_code.json, .env 세 곳에 동기화 저장.

    refresh_token(마스터 열쇠) 처리 규칙:
      * rotate_refresh=False 또는 refresh_token is None/빈 문자열
          → 회전 없음. 기존 env/json의 refresh_token을 절대 유실하지 않고 유지.
          → .env / json 에 KAKAO_REFRESH_TOKEN 을 빈칸으로 덮어쓰지 않음.
      * rotate_refresh=True 이고 비어있지 않은 refresh_token
          → 회전 발급. 새 값으로 기존 값을 대체 저장.

    refresh_token_expires_in:
      * 카카오 응답에 있으면 refresh_expires_at = now + 초 로 저장.
      * 응답에 없으면(미회전) 기존 refresh_expires_at 유지.

    카카오 토큰 갱신 API는 refresh 유효기간이 충분히 남으면 응답에서
    `refresh_token` 필드를 생략한다. 이 경우 access만 갱신하고 refresh는 유지해야 한다.

    저장 직후 json/.env를 다시 읽어 access·refresh가 100% 일치하는지 검증한다.
    불일치 시 `[ERROR] 토큰 파일 덮어쓰기 실패!` 를 남기고 False.
    """
    if not kakao_api_allowed():
        _log_kakao_env_block_once("토큰 저장")
        return False
    ja, jr_file = _read_json_tokens()
    prev_refresh = (jr_file or os.getenv("KAKAO_REFRESH_TOKEN", "").strip()).strip()

    # --- 마스터 열쇠(refresh_token) 회전 분기 ---
    new_refresh = (refresh_token or "").strip() if refresh_token is not None else ""
    if rotate_refresh is None:
        # 명시 플래그 없으면: 새 refresh 문자열이 있을 때만 회전
        rotate_refresh = bool(new_refresh)

    if rotate_refresh and new_refresh:
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
        eff_refresh = prev_refresh
        if eff_refresh:
            logger.info(
                "카카오 응답에 refresh_token 없음/미회전 — 기존 마스터 열쇠 유지: %s "
                "(access_token만 갱신)",
                _mask_token(eff_refresh),
            )
        else:
            logger.warning(
                "카카오 refresh_token 유지 시도였으나 디스크/환경에 기존 값이 없습니다. "
                "access만 저장합니다."
            )

    eff_access = (access_token or "").strip() or os.getenv("KAKAO_ACCESS_TOKEN", "").strip() or ja
    if not eff_access:
        logger.error("[ERROR] 토큰 파일 덮어쓰기 실패! 저장할 access_token이 비어 있습니다.")
        return False

    eff_expires_at = 0.0
    if expires_at is not None and float(expires_at) > 0:
        eff_expires_at = float(expires_at)
    elif expires_in is not None:
        try:
            ei = int(expires_in)
        except (TypeError, ValueError):
            ei = 0
        if ei > 0:
            eff_expires_at = time.time() + ei

    # refresh 수명: 응답에 새 값이 있을 때만 갱신(회전 시 보통 함께 옴)
    rtei: int | None = None
    if refresh_token_expires_in is not None:
        try:
            rtei = int(refresh_token_expires_in)
        except (TypeError, ValueError):
            rtei = None
        if rtei is not None and rtei <= 0:
            rtei = None

    json_ok = _write_kakao_code_json(
        eff_access,
        eff_refresh if (rotate_refresh and new_refresh) else "",
        expires_at=eff_expires_at if eff_expires_at > 0 else None,
        refresh_token_expires_in=rtei,
        rotate_refresh=bool(rotate_refresh and new_refresh),
    )

    # access는 항상 갱신. refresh는 회전할 때만 .env 키를 건드린다(미회전 시 기존 라인 유지).
    file_updates: Dict[str, str] = {"KAKAO_ACCESS_TOKEN": eff_access}
    if rotate_refresh and new_refresh:
        file_updates["KAKAO_REFRESH_TOKEN"] = new_refresh
    if rtei is not None:
        file_updates["KAKAO_REFRESH_TOKEN_EXPIRES_IN"] = str(rtei)
        file_updates["KAKAO_REFRESH_EXPIRES_AT"] = str(int(time.time() + rtei))
    env_ok = _merge_env_file(file_updates)

    # --- 저장 검증: 파일을 다시 읽어 100% 일치 확인 ---
    saved_access, saved_refresh = _read_json_tokens()
    json_access_ok = saved_access == eff_access
    # refresh 미회전이어도 기존 값이 디스크에 남아 있어야 한다.
    expected_refresh = eff_refresh
    json_refresh_ok = (not expected_refresh) or (saved_refresh == expected_refresh)

    env_values = _read_env_key_values(["KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN"])
    env_access_ok = env_values.get("KAKAO_ACCESS_TOKEN", "") == eff_access
    env_refresh_ok = (not expected_refresh) or (
        env_values.get("KAKAO_REFRESH_TOKEN", "") == expected_refresh
    )

    ok = bool(json_ok and env_ok and json_access_ok and json_refresh_ok and env_access_ok and env_refresh_ok)
    if ok:
        refresh_left = get_refresh_token_expires_in_remaining()
        refresh_left_txt = "(미저장)"
        if refresh_left is not None:
            days = refresh_left / 86400.0
            refresh_left_txt = f"{refresh_left}s ({days:.1f}일)"
            if refresh_left < 0:
                refresh_left_txt = f"만료됨 {refresh_left}s"
            elif refresh_left < 30 * 86400:
                logger.warning(
                    "카카오 refresh_token 잔여 수명 1개월 미만 — 다음 access 갱신 시 "
                    "새 refresh_token 이 발급될 수 있습니다. 반드시 디스크에 저장되는지 확인하세요. "
                    "remaining=%s",
                    refresh_left_txt,
                )
        logger.info(
            "✅ 토큰 파일 저장 검증 통과: access=%s refresh=%s rotate_refresh=%s "
            "refresh_token_expires_in/remaining=%s (kakao_code.json + .env)",
            _mask_token(eff_access),
            _mask_token(saved_refresh or expected_refresh),
            rotate_refresh and bool(new_refresh),
            refresh_left_txt if rtei is None else f"응답={rtei}s / 남은={refresh_left_txt}",
        )
        os.environ["KAKAO_ACCESS_TOKEN"] = saved_access
        if saved_refresh:
            os.environ["KAKAO_REFRESH_TOKEN"] = saved_refresh
        elif expected_refresh:
            os.environ["KAKAO_REFRESH_TOKEN"] = expected_refresh
        return True

    reasons: list[str] = []
    if not json_ok:
        reasons.append("kakao_code.json 쓰기 실패")
    if not env_ok:
        reasons.append(".env 쓰기 실패")
    if not json_access_ok:
        reasons.append(
            f"json access 불일치(의도={_mask_token(eff_access)} 파일={_mask_token(saved_access)})"
        )
    if not json_refresh_ok:
        reasons.append(
            f"json refresh 불일치(의도={_mask_token(expected_refresh)} 파일={_mask_token(saved_refresh)})"
        )
    if not env_access_ok:
        reasons.append(
            f".env access 불일치(의도={_mask_token(eff_access)} 파일={_mask_token(env_values.get('KAKAO_ACCESS_TOKEN', ''))})"
        )
    if not env_refresh_ok:
        reasons.append(
            f".env refresh 불일치(의도={_mask_token(expected_refresh)} 파일={_mask_token(env_values.get('KAKAO_REFRESH_TOKEN', ''))})"
        )
    logger.error(
        "[ERROR] 토큰 파일 덮어쓰기 실패! 원인: %s — "
        "카카오 서버는 새 refresh를 발급했을 수 있으나 디스크에 없음. "
        "즉시 권한/디스크를 확인하고 coinbot_watch.sh 로 재인증하세요.",
        "; ".join(reasons) or "알 수 없음",
    )
    hydrate_tokens_from_json()
    return False


def clear_env_key(key: str) -> None:
    """.env 키 값을 비우고 os.environ 에서 제거."""
    os.environ.pop(key, None)
    # 의도적 삭제는 빈 문자열이 아니라 키 라인 제거가 필요 — _merge_env_file 은
    # 빈 값일 때 보존하므로, 여기서는 직접 비운 값으로 기록한다.
    env_path = _env_file_path()
    if not env_path.exists():
        return
    try:
        raw = env_path.read_text(encoding="utf-8-sig")
        lines = raw.splitlines(keepends=True)
        out: list[str] = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                out.append(f"{key}=\n")
            else:
                out.append(line)
        _atomic_write_text(env_path, "".join(out))
    except Exception as exc:
        logger.error("clear_env_key(%s) 실패: %s", key, exc)


def clear_kakao_auth_code() -> None:
    clear_env_key("KAKAO_AUTH_CODE")


def apply_token_response(token_data: Dict[str, Any]) -> str:
    """OAuth/refresh 응답 JSON에서 토큰을 꺼내 **디스크에 저장·검증**한 뒤 access를 반환.

    카카오 토큰 갱신 API 특성:
      - refresh_token 유효기간이 충분히 남으면(약 1개월+) 응답 JSON에서
        `refresh_token` 필드를 **아예 생략**한다.
      - 이 경우 기존 파일의 refresh_token을 절대 지우거나 빈칸으로 덮어쓰지 않는다.
      - access_token 은 응답 새 값으로 항상 갱신·저장한다.

    저장/검증 실패 시 빈 문자열을 반환한다(메모리만 성공으로 착각하지 않음).
    """
    access = str(token_data.get("access_token") or "").strip()
    if not access:
        logger.error(
            "카카오 토큰 응답에 access_token이 없습니다. keys=%s",
            sorted(str(k) for k in token_data.keys()),
        )
        return ""

    # 필드 존재 + 비어있지 않을 때만 refresh 회전. 생략/null/"" 는 기존 값 유지.
    has_refresh_field = "refresh_token" in token_data
    raw_refresh = token_data.get("refresh_token") if has_refresh_field else None
    rotated = str(raw_refresh or "").strip() if raw_refresh is not None else ""
    has_new_refresh = bool(rotated)

    expires_in_raw = token_data.get("expires_in")
    try:
        expires_in = int(expires_in_raw) if expires_in_raw is not None else None
    except (TypeError, ValueError):
        expires_in = None

    rtei_raw = token_data.get("refresh_token_expires_in")
    try:
        refresh_token_expires_in = int(rtei_raw) if rtei_raw is not None else None
    except (TypeError, ValueError):
        refresh_token_expires_in = None

    if has_new_refresh:
        logger.info(
            "카카오 갱신 응답에 refresh_token 포함 → 마스터 열쇠 회전 처리 시작 "
            "(keys=%s refresh_token_expires_in=%s)",
            sorted(str(k) for k in token_data.keys()),
            refresh_token_expires_in if refresh_token_expires_in is not None else "(없음)",
        )
        ok = persist_kakao_tokens(
            access,
            rotated,
            expires_in=expires_in,
            refresh_token_expires_in=refresh_token_expires_in,
            rotate_refresh=True,
        )
        logger.info("카카오 마스터 열쇠 회전 저장 결과: %s", "성공" if ok else "실패")
    else:
        logger.info(
            "카카오 갱신 응답에 refresh_token 필드 없음/빈값 → 기존 refresh 유지, access만 저장 "
            "(has_field=%s keys=%s refresh_token_expires_in=%s)",
            has_refresh_field,
            sorted(str(k) for k in token_data.keys()),
            refresh_token_expires_in if refresh_token_expires_in is not None else "(없음)",
        )
        # 미회전인데 refresh_token_expires_in 만 온 경우는 드묾 — 있으면 잔여 수명만 갱신
        ok = persist_kakao_tokens(
            access,
            None,
            expires_in=expires_in,
            refresh_token_expires_in=refresh_token_expires_in,
            rotate_refresh=False,
        )
    if not ok:
        logger.error(
            "[ERROR] 토큰 파일 덮어쓰기 실패! apply_token_response — "
            "액세스 토큰을 메모리 성공으로 취급하지 않습니다. "
            "kakao_code.json/.env 권한·디스크를 확인하세요."
        )
        return ""
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
    """카카오 access_token 유효성(HTTP 200) 확인.

    평시 매 사이클에서 호출하지 말 것 — 알림 전송/기동 복구 등
    '로그인 시도' 경로에서만 사용한다(카카오 API 부하·정책 회피).
    """
    token = (access_token or "").strip()
    if not token or not kakao_api_allowed():
        return False
    try:
        response = requests.get(
            "https://kapi.kakao.com/v1/user/access_token_info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        if response.status_code == 200:
            return True
        logger.warning(
            "카카오 access_token 검증 실패: HTTP %s body=%s",
            response.status_code,
            (response.text or "")[:200],
        )
        return False
    except requests.RequestException as exc:
        logger.warning("카카오 access_token 검증 네트워크 오류: %s", type(exc).__name__)
        return False


def ensure_access_token_for_login(client_id: str, refresh_token: str = "") -> str:
    """카톡 자동로그인 스타일: 만료됐을 때만 refresh.

    - 로컬 expires_at 이 유효하면 HTTP 없이 기존 access 반환
    - 만료/없음이면 refresh_kakao_access_token_sync 로 갱신(+디스크 저장 검증)
    """
    if not kakao_api_allowed():
        _log_kakao_env_block_once("자동 로그인")
        return ""
    hydrate_tokens_from_json()
    access = get_access_token().strip()
    if access and is_access_token_locally_fresh():
        return access
    if access:
        logger.info(
            "카카오 액세스 토큰 만료/만료임박 — 로그인(알림) 시점에만 자동 갱신합니다 "
            "(expires_at=%s)",
            int(get_access_expires_at()) or "(없음)",
        )
    else:
        logger.info("카카오 액세스 토큰 없음 — 로그인(알림) 시점에 refresh로 자동 로그인합니다.")
    return refresh_kakao_access_token_sync(client_id, refresh_token, force=True)


def refresh_kakao_access_token_sync(
    client_id: str,
    refresh_token: str = "",
    *,
    force: bool = False,
) -> str:
    """Thread-safe 카카오 access_token 갱신(Double-checked locking).

    - force=False(기본): 로컬 expires_at 이 유효하면 HTTP refresh를 하지 않는다.
    - force=True: 401 등 실무효가 확인된 '로그인 시도'에서만 강제 갱신.
    - 성공 시 apply_token_response → persist 검증까지 통과한 access만 반환.
    """
    if not kakao_api_allowed():
        _log_kakao_env_block_once("토큰 갱신")
        return ""

    hydrate_tokens_from_json()
    access = get_access_token().strip()
    if access and not force and is_access_token_locally_fresh():
        return access

    rt = (refresh_token or get_refresh_token()).strip()
    if not client_id:
        logger.error("카카오 토큰 갱신 실패: REST API 키(client_id)가 비어 있습니다.")
        return ""
    if not rt:
        logger.error(
            "카카오 토큰 갱신 실패: refresh_token이 비어 있습니다. "
            "kakao_code.json / .env 를 확인하거나 coinbot_watch.sh 로 재인증하세요."
        )
        return access if (access and not force) else ""

    new_access = ""
    refreshed_now = False
    last_error = ""
    with _KAKAO_REFRESH_LOCK:
        hydrate_tokens_from_json()
        access = get_access_token().strip()
        if access and not force and is_access_token_locally_fresh():
            logger.debug("카카오 refresh 대기 — 다른 스레드가 갱신한 access_token 사용")
            return access

        try:
            token_data = refresh_access_token_request(client_id, rt)
            new_access = apply_token_response(token_data)
            if new_access:
                logger.info(
                    "카카오 액세스 토큰 자동 갱신 완료 (thread-safe, 디스크 저장·검증 통과)"
                )
                refreshed_now = True
            else:
                last_error = (
                    "refresh HTTP는 성공했으나 토큰 파일 저장/검증 실패 "
                    "(kakao_code.json / .env 권한·디스크 확인)"
                )
                logger.error("[ERROR] %s", last_error)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.error("카카오 토큰 갱신 예외: %s", last_error)
            raise

    # 데드락 방지: 리스너(알림 발송)는 반드시 락 해제 후 호출한다.
    if refreshed_now:
        try:
            _notify_kakao_token_http_refreshed()
        except Exception:
            logger.exception("카카오 갱신 성공 알림 처리 중 예외(토큰 갱신·저장 자체는 성공)")
    elif last_error:
        logger.error("카카오 토큰 갱신 최종 실패 원인: %s", last_error)
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
    try:
        response = requests.post(token_url, data=data, timeout=15)
    except requests.RequestException as exc:
        logger.error(
            "카카오 토큰 갱신 네트워크 실패: %s — 원인=%s",
            type(exc).__name__,
            exc,
        )
        raise

    if response.status_code != 200:
        err: Dict[str, Any] = {}
        desc = ""
        try:
            err = response.json()
            desc = str(err.get("error_description") or err.get("error") or "")
        except Exception:
            desc = (response.text or "")[:300]
        error_code = str(err.get("error") or "")
        if "expired_or_invalid_refresh_token" in desc or error_code == "invalid_grant":
            logger.error(
                "Kakao refresh token 폐기/만료(invalid_grant): code=%s desc=%s HTTP=%s — "
                "재인증 필요(coinbot_watch.sh). 흔한 원인: "
                "1) 갱신 성공 후 .env/json 저장 실패로 옛 refresh 재사용 "
                "2) WinSCP로 낡은 .env 덮어쓰기 "
                "3) 다른 환경에서 동일 앱 재인증(패밀리 폐기) "
                "4) refresh_token 수명 만료",
                error_code or "(없음)",
                desc or "(없음)",
                response.status_code,
            )
            _notify_kakao_invalid_grant(error_code, desc)
        else:
            logger.error(
                "Kakao token refresh failed: HTTP=%s code=%s desc=%s body=%s",
                response.status_code,
                error_code or "(없음)",
                desc or "(없음)",
                (response.text or "")[:300],
            )
        response.raise_for_status()
    return response.json()
