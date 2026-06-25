# VULTR 서버 배포 가이드 (ai_trading 엔진)

실전 매매는 **`ai_trading/main_ai.py`** 하나로 돌아갑니다. 저장소 루트(`Coin/`) 전체를 서버에 올리고 아래 절차를 따르세요.

> **이미 배포된 서버에서 봇을 켜거나 카카오 재인증할 때** → **[START.md](../START.md)** (일상 시작 절차)

## 1. 옮길 것 / 제외할 것

### ✅ 포함
- `ai_trading/`, `btc_live_trading/`, `btc_day_strategy/`, `scripts/` 전체 `.py`
- 각 폴더의 `requirements.txt`

### ❌ 서버로 보내지 말 것
| 항목 | 이유 |
|------|------|
| `.env`, `kakao_code.json` | API 키·토큰. **서버에서 별도 관리** |
| `trading_stats.json`, `virtual_trades.jsonl`, `ai_learning_logs.csv`, `data/history/` | PC·서버별 런타임 데이터 (Git 제외) |
| `*.log`, `*.pyc`, `__pycache__/`, `.coinbot.lock` | 자동 생성물 |
| `venv/` | 서버에서 새로 생성 |

---

## 2. 서버 필수 작업

### 2.1 바이낸스 IP 화이트리스트
```bash
curl -s ifconfig.me   # 서버 공인 IP 확인
```
→ 바이낸스 API Management → IP 제한에 추가 (PC IP와 다르므로 새로 등록)

### 2.2 .env 생성 (최초 1회)
```bash
cd /home/bot2/Coin/btc_live_trading
nano .env   # PC의 .env 내용을 **수동으로** 입력 (WinSCP로 .env 파일 자체는 올리지 않음)
```
실전 전환: `.env`에서 `AI_DRY_RUN=false`

### 2.3 타임존
```bash
sudo timedatectl set-timezone Asia/Seoul
```

### 2.4 패키지 설치
```bash
sudo apt update && sudo apt install python3 python3-pip python3-venv -y
cd /home/bot2/Coin
python3 -m venv /home/bot2/venv
source /home/bot2/venv/bin/activate
pip install -r ai_trading/requirements.txt -r btc_live_trading/requirements.txt
```

### 2.5 테스트 실행
```bash
cd /home/bot2/Coin/ai_trading
python main_ai.py
```

---

## 3. 24/7 systemd 등록

```bash
sudo nano /etc/systemd/system/coinbot.service
```
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
sudo systemctl enable coinbot
sudo systemctl start coinbot
bash ~/Coin/scripts/coinbot_watch.sh   # 인증 필요 시 URL+입력 후 로그 팔로우
```

---

## 4. 카카오 토큰 (서버 대화형 인증)

`journalctl -f`만 실행하면 **인증 입력 칸이 나오지 않습니다.** SSH에서:

```bash
bash ~/Coin/scripts/coinbot_watch.sh
# 또는 인증만:
python ~/Coin/scripts/auth_kakao.py
```

- 토큰 만료/없음 → 터미널에 **카카오 URL + input() 대기** → 전체 URL 또는 code 붙여넣기 → `✅ 저장 완료`
- 재시작: `systemctl restart coinbot.service` (root)
- **카카오 API는 `/home/bot2/Coin` + hostname `example1`에서만** 동작 (화이트리스트 하드코딩)

대안: `.env`에 `KAKAO_AUTH_CODE=<코드>` 1회 설정 후 `systemctl restart coinbot`
알림만 끄려면 `.env`에 `KAKAO_ALERTS_ENABLED=false`.

---

## 5. 주의사항

| 항목 | 설명 |
|------|------|
| **단일 실행** | `.coinbot.lock` — 1프로세스만. `pgrep -af main_ai.py`로 확인 |
| **카카오 화이트리스트** | `example1` + `/home/bot2/Coin` 외 환경에서는 API 차단 |
| **매매 주기** | `AI_LOOP_SECONDS=300` (5분 1회 AI 평가) |
| IP 변경 | 인스턴스 재생성 시 IP 바뀜 → 바이낸스 화이트리스트 재등록 |
| 드라이런 | 처음엔 `.env`의 `AI_DRY_RUN=true`로 검증 권장 |
| 방화벽 | 아웃바운드만 필요(바이낸스·카카오·OpenAI). 인바운드 불필요 |
