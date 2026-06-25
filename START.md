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

**② root — 재시작**

```bash
exit
systemctl restart coinbot.service
journalctl -u coinbot.service -f
```

> `journalctl -f` 만 쓰면 인증 입력 칸이 안 나옵니다. 인증은 반드시 `coinbot_watch.sh`.

인증 한 번 잘 되면, 이후 **6시간마다 토큰 자동 갱신** — 평소엔 §1만 하면 됩니다.

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
| 로컬/WSL에서 카카오 인증 시도 | **불가** — Vultr `example1` + `/home/bot2/Coin` 에서만 허용 |

---

## 5. 더 보기

- 최초 서버 세팅(systemd, venv, `.env`): [VULTR_DEPLOY.md](btc_live_trading/VULTR_DEPLOY.md)
- 카카오 토큰·Safety First 상세: [README.md](README.md)
