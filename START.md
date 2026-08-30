# Vultr 서버 — 봇 시작 가이드

Cursor에서 코드 수정 → **WinSCP**로 서버에 올림 → **SSH**로 재시작. 이 흐름만 정리했습니다.

| 항목 | 값 |
|------|-----|
| WinSCP 업로드 위치 | `/home/bot2/Coin/` (로컬과 같은 폴더 구조) |
| SSH | `ssh root@64.176.62.150` |
| 서비스 이름 | `coinbot.service` |

---

## 1. 코드 올린 뒤 (평소 — 카카오 문제 없을 때)

WinSCP에서 **수정한 파일만** `/home/bot2/Coin/` 아래 같은 경로로 덮어쓰기.

SSH (root):

```bash
pgrep -af main_ai.py          # 좀비 프로세스 있으면 stop 후 재시작
systemctl restart coinbot.service
journalctl -u coinbot.service -f
```

`.coinbot.lock` 덕분에 `main_ai.py`가 2개 이상 떠 있으면 두 번째는 즉시 종료됩니다. 그래도 `pgrep`으로 1개만 남았는지 확인하세요.

`카카오톡 메시지 전송 성공` / 매매 로그가 보이면 끝.  
로그만 끊을 때: `Ctrl+C` (봇은 계속 실행)

봇 멈출 때:

```bash
systemctl stop coinbot.service
```

---

## 2. 카카오 재인증이 필요할 때만

로그에 `카카오 인증 실패 — 매매 로직을 실행하지 않습니다` 가 보이거나, 카톡이 며칠 안 올 때.

**① bot2 — 인증** (URL + code 입력)

```bash
su - bot2
cd ~/Coin
source ~/venv/bin/activate
bash scripts/coinbot_watch.sh
```

- 브라우저에서 URL 열고 로그인
- 이동된 주소창 **전체 URL** 또는 `code=` 뒤 값 → 터미널에 붙여넣기 (자동 파싱)
- `✅ 저장 완료` 나오면 OK

인증만 따로:

```bash
python ~/Coin/scripts/auth_kakao.py
```

**② root — 재시작 (선택)**

봇이 실행 중이면 **재시작 없이도** 인증 대기 모드가 최대 30분(`KAKAO_AUTH_RETRY_MINUTES`) 안에 새 토큰을 자동으로 집어 씁니다. 즉시 반영하고 싶을 때만:

```bash
exit
systemctl restart coinbot.service
journalctl -u coinbot.service -f
```

> `journalctl -f` 만 쓰면 인증 입력 칸이 안 나옵니다. 인증은 반드시 `coinbot_watch.sh`.

인증 한 번 잘 되면, 이후 **카톡 자동로그인처럼** 동작합니다 — 평시에는 갱신 HTTP를 치지 않고, **알림을 보낼 때 토큰이 만료됐을 때만** refresh한 뒤 `.env`/`kakao_code.json`에 저장합니다. 평소엔 §1만 하면 됩니다.

생존·저장 실패 확인:

```bash
journalctl -u coinbot.service --since "1 hour ago" --no-pager | grep -E "카카오 토큰 하트비트|자동 갱신 완료|토큰 파일 덮어쓰기|invalid_grant|CRITICAL KAKAO|알림 워커"
```

카톡 1회 수동 발송 진단:

```bash
cd ~/Coin && source ~/venv/bin/activate && python scripts/test_kakao_send.py
```

---

## 3. WinSCP로 `.sh` 파일 올렸을 때만

`coinbot_watch.sh` 올린 직후 한 번만 (CRLF 오류 나면):

```bash
sed -i 's/\r$//' /home/bot2/Coin/scripts/coinbot_watch.sh
```

---

## 4. 막혔을 때

| 증상 | 해결 |
|------|------|
| `/root/Coin` 경로 오류 | 경로는 **`/home/bot2/Coin`** |
| `No module named 'dotenv'` | `source ~/venv/bin/activate` 후 다시 실행 |
| `bot2 is not in the sudoers` | 재시작은 **root**에서 `systemctl restart` |
| `$'\r': command not found` | §3 `sed` 실행 |
| `main_ai.py`가 여러 개 실행됨 | `pgrep -af main_ai.py` → `systemctl stop coinbot.service` → 재시작 |
| 카톡이 **1시간마다** 옴 | 서버 `.env` 확인: `grep AI_STATUS_REPORT_MINUTES /home/bot2/Coin/btc_live_trading/.env` → `1440` 으로 변경, `KAKAO_ONCE_PER_DAY=true` 추가 후 재시작 |
| 토큰 갱신 알림이 **만료 후 알림 보낼 때** 옴 | 정상 (`🔑 카카오 토큰 갱신 성공`). 고정 6시간 스케줄 아님. 일일 리포트(1회)와 별개 |
| 재시작 후 `invalid_grant` | 예전엔 갱신값이 메모리만 남는 경우 있음 — 최신 코드는 디스크 저장 검증 필수. `journalctl \| grep "토큰 파일 덮어쓰기"` 확인 |
| 갱신 성공 후 카톡이 끊김 | 예전 데드락/워커 사망 경로 — 최신 코드는 락 해제 후 알림·워커 자가 복구. `journalctl \| grep "하트비트\|알림 워커"` 로 생존 확인 |
| `카카오 인증 실패` 로그 | 인증 대기 모드 — 30분마다 자동 복구 시도. `coinbot_watch.sh` 재인증만 하면 재시작 없이 살아남. 원인은 `journalctl \| grep "리프레시 토큰 갱신 실패"` 의 `code=` 확인 |

---

## 5. 더 보기

- 최초 서버 세팅(systemd, venv, `.env`): [VULTR_DEPLOY.md](btc_live_trading/VULTR_DEPLOY.md)
- 엔진·환경변수·카카오 상세: [ai_trading/README.md](ai_trading/README.md)
- 시스템 개요·아키텍처: [README.md](README.md)
