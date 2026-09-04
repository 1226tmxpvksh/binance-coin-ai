# 배포 · 일상 운영 (Vultr)

최초 서버 세팅과 평소 재시작·재인증을 한곳에 모았습니다.  
프로젝트 구조·주의사항은 루트 [README.md](README.md)를 보세요.

| 항목 | 값 |
|------|-----|
| 프로젝트 경로 | `/home/bot2/Coin` |
| 봇 계정 | `bot2` |
| venv | `/home/bot2/venv` |
| 매매 서비스 | `coinbot.service` |
| 조회 봇 | `coinbot-discord-bot.service` |
| SSH (예시) | `ssh root@<서버IP>` |

기본 알림은 **디스코드** (`NOTIFY_CHANNEL=discord`). 카카오는 [legacy/kakao/](legacy/kakao/README.md).

---

## A. 최초 배포

### A.1 옮길 것 / 제외할 것

**포함:** `ai_trading/`, `btc_live_trading/`(`.env`·`kakao_code.json` 제외), `btc_day_strategy/`, `scripts/`, `legacy/`, `deploy/`, 루트 `README.md`·`DEPLOY.md`

**제외:** `.env`, `kakao_code.json`, `trading_stats.json`, `virtual_trades.jsonl`, `*.log`, `__pycache__/`, `.coinbot.lock`, `venv/`

### A.2 서버 준비

```bash
curl -s ifconfig.me   # 바이낸스 IP 화이트리스트에 등록
sudo timedatectl set-timezone Asia/Seoul

sudo apt update && sudo apt install python3 python3-pip python3-venv -y
cd /home/bot2/Coin
python3 -m venv /home/bot2/venv
source /home/bot2/venv/bin/activate
pip install -r ai_trading/requirements.txt -r btc_live_trading/requirements.txt
```

`.env`는 서버에서만 작성:

```bash
cd /home/bot2/Coin/btc_live_trading
nano .env   # PC 값을 수동 입력 (파일 자체 업로드 비권장)
```

실전: `AI_DRY_RUN=false` (⚠️ 실주문)

### A.3 systemd — 매매 봇

`/etc/systemd/system/coinbot.service`:

```ini
[Unit]
Description=AI BTC Trading Bot
After=network.target

[Service]
Type=simple
User=bot2
WorkingDirectory=/home/bot2/Coin/ai_trading
ExecStart=/home/bot2/venv/bin/python main_ai.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coinbot.service
journalctl -u coinbot.service -f
```

### A.4 systemd — 디스코드 조회 봇 (선택)

```bash
sudo cp /home/bot2/Coin/deploy/coinbot-discord-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coinbot-discord-bot.service
journalctl -u coinbot-discord-bot.service -f
```

`DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`는 `.env`에 설정. 상세는 [README.md](README.md).

---

## B. 평소 운영 (코드 올린 뒤)

WinSCP로 수정 파일만 `/home/bot2/Coin/` 동일 경로에 덮어쓰기 → SSH(root):

```bash
pgrep -af main_ai.py
systemctl restart coinbot.service
journalctl -u coinbot.service -f
```

조회 봇도 올렸다면:

```bash
systemctl restart coinbot-discord-bot.service
```

중지:

```bash
systemctl stop coinbot.service
```

`.coinbot.lock`으로 `main_ai.py` 중복 기동을 막습니다. `pgrep`으로 1개만 확인하세요.

디스코드 웹훅 테스트:

```bash
cd ~/Coin && source ~/venv/bin/activate
python scripts/test_discord_send.py
```

---

## C. 카카오 재인증 (레거시 — `NOTIFY_CHANNEL=kakao` 일 때만)

```bash
su - bot2
cd ~/Coin && source ~/venv/bin/activate
bash scripts/coinbot_watch.sh
# 또는
python scripts/auth_kakao.py
```

`journalctl -f`만으로는 인증 입력이 안 됩니다. 구현은 `legacy/kakao/`, 진입은 `scripts/` shim.

refresh 조기 사망 진단(선택):

```cron
*/15 * * * * cd /home/bot2/Coin && /home/bot2/venv/bin/python scripts/kakao_rt_healthcheck.py >>/home/bot2/Coin/data/kakao_rt_healthcheck.cron.log 2>&1
```

---

## D. 트러블슈팅

| 증상 | 해결 |
|------|------|
| `/root/Coin` 오류 | 경로는 **`/home/bot2/Coin`** |
| `No module named 'dotenv'` | `source ~/venv/bin/activate` |
| `bot2` sudo 불가 | 재시작은 **root**에서 `systemctl` |
| `$'\r': command not found` | `sed -i 's/\r$//' scripts/coinbot_watch.sh` |
| `main_ai.py` 여러 개 | `systemctl stop coinbot` 후 재시작 |
| 슬래시 명령 안 보임 | `DISCORD_GUILD_ID` 설정·봇 재시작 |

---

## E. 관련 문서

- [README.md](README.md) — 구조·주의사항 (외부 AI용 진입점)
- [legacy/kakao/README.md](legacy/kakao/README.md) — 카카오 격리
- [ai_trading/README.md](ai_trading/README.md) — 엔진·환경변수 상세
- [btc_day_strategy/README.md](btc_day_strategy/README.md) — 백테스트
