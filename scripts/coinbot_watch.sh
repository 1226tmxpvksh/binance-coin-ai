#!/usr/bin/env bash
# journalctl -u coinbot.service -f 대신 이 스크립트를 사용하세요.
# 카카오 인증이 필요하면 URL + 입력 칸을 먼저 띄운 뒤, 서비스 재시작 후 로그를 팔로우합니다.
#
# 사용 (서버 SSH):
#   bash ~/Coin/scripts/coinbot_watch.sh
#   bash ~/Coin/scripts/coinbot_watch.sh --no-restart   # 인증만, 재시작 생략
#
# journalctl에 옵션을 넘기려면 -- 뒤에 붙입니다:
#   bash ~/Coin/scripts/coinbot_watch.sh -- --since today

set -euo pipefail

SERVICE="${COINBOT_SERVICE:-coinbot.service}"
NO_RESTART=0
JOURNAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-restart)
      NO_RESTART=1
      shift
      ;;
    --)
      shift
      JOURNAL_ARGS=("$@")
      break
      ;;
    *)
      JOURNAL_ARGS+=("$1")
      shift
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# venv 위치: Coin/venv 또는 bot2 홈의 ~/venv (Vultr 배포 기본)
for _venv_activate in "$ROOT/venv/bin/activate" "$HOME/venv/bin/activate"; do
  if [[ -f "$_venv_activate" ]]; then
    # shellcheck disable=SC1091
    source "$_venv_activate"
    break
  fi
done

if [[ -x "$ROOT/venv/bin/python" ]]; then
  PYTHON="$ROOT/venv/bin/python"
elif [[ -x "$HOME/venv/bin/python" ]]; then
  PYTHON="$HOME/venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python
fi

_restart_service() {
  if [[ "$NO_RESTART" -eq 1 ]]; then
    return 0
  fi
  echo "[coinbot] 서비스 재시작 중..."
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl restart "$SERVICE"
    sleep 2
    return 0
  fi
  if sudo -n systemctl restart "$SERVICE" 2>/dev/null; then
    sleep 2
    return 0
  fi
  echo
  echo "[안내] bot2 계정은 sudo 권한이 없어 여기서 재시작할 수 없습니다."
  echo "       인증은 이미 완료되었습니다. root SSH에서 아래만 실행하세요:"
  echo "         systemctl restart $SERVICE"
  echo
  return 1
}

_follow_logs() {
  echo ">>> journalctl -u $SERVICE -f ${JOURNAL_ARGS[*]:-}"
  echo "    (종료: Ctrl+C)"
  echo
  if [[ "$(id -u)" -eq 0 ]]; then
    exec journalctl -u "$SERVICE" -f "${JOURNAL_ARGS[@]}"
  fi
  if journalctl -u "$SERVICE" -n 1 >/dev/null 2>&1; then
    exec journalctl -u "$SERVICE" -f "${JOURNAL_ARGS[@]}"
  fi
  if sudo -n journalctl -u "$SERVICE" -n 1 >/dev/null 2>&1; then
    exec sudo journalctl -u "$SERVICE" -f "${JOURNAL_ARGS[@]}"
  fi
  echo "[안내] 로그 열람 권한이 없습니다. root SSH에서 실행하세요:"
  echo "  journalctl -u $SERVICE -f"
  exit 0
}

echo "======================================================================"
echo " Coinbot 로그 팔로우 (인증 필요 시 이 터미널에서 바로 입력)"
echo " 서비스: $SERVICE"
echo "======================================================================"
echo

if ! "$PYTHON" "$ROOT/scripts/check_kakao_auth.py" >/dev/null 2>&1; then
  echo "[카카오] 마스터 열쇠가 없거나 만료되었습니다. 아래에서 인증을 진행합니다."
  echo
  # 감사 로그 trigger 구분용 (kakao_utils.append_kakao_auth_event)
  export KAKAO_AUTH_TRIGGER=watch_script
  if ! "$PYTHON" "$ROOT/scripts/auth_kakao.py"; then
    echo
    echo "카카오 인증에 실패했습니다. 코드/Redirect URI를 확인한 뒤 다시 실행하세요." >&2
    exit 1
  fi
  echo
  echo "[OK] 카카오 토큰 저장 완료."
  _restart_service || true
  echo
else
  echo "[카카오] 토큰 정상 — 로그 팔로우를 시작합니다."
  echo
fi

_follow_logs
