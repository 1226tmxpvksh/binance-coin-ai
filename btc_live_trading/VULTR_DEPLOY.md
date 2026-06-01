# VULTR 서버 배포 가이드 (ai_trading 엔진)

실전 매매는 **`ai_trading/main_ai.py`** 하나로 돌아갑니다. 저장소 루트(`Coin/`) 전체를 서버에 올리고 아래 절차를 따르세요.

## 1. 옮길 것 / 제외할 것

### ✅ 포함
- `ai_trading/`, `btc_live_trading/`, `btc_day_strategy/`, `scripts/` 전체 `.py`
- 각 폴더의 `requirements.txt`

### ❌ 서버로 보내지 말 것
| 항목 | 이유 |
|------|------|
| `.env` | API 키 포함. **서버에서 새로 생성** |
| `*.log`, `*.pyc`, `__pycache__/` | 자동 생성물 |
| `venv/` | 서버에서 새로 생성 |

---

## 2. 서버 필수 작업

### 2.1 바이낸스 IP 화이트리스트
```bash
curl -s ifconfig.me   # 서버 공인 IP 확인
```
→ 바이낸스 API Management → IP 제한에 추가 (PC IP와 다르므로 새로 등록)

### 2.2 .env 생성
```bash
cd /path/to/Coin/btc_live_trading
nano .env   # PC의 .env 값 복사 (BINANCE_*, OPENAI_*, KAKAO_*, AI_* 등)
```
실전 전환: `.env`에서 `AI_DRY_RUN=false`

### 2.3 타임존
```bash
sudo timedatectl set-timezone Asia/Seoul
```

### 2.4 패키지 설치
```bash
sudo apt update && sudo apt install python3 python3-pip python3-venv -y
cd /path/to/Coin
python3 -m venv venv
source venv/bin/activate
pip install -r ai_trading/requirements.txt -r btc_live_trading/requirements.txt
```

### 2.5 테스트 실행
```bash
cd /path/to/Coin/ai_trading
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
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/Coin/ai_trading
ExecStart=/home/YOUR_USERNAME/Coin/venv/bin/python main_ai.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable coinbot
sudo systemctl start coinbot
sudo journalctl -u coinbot -f
```

---

## 4. 카카오 토큰 (비대화형 서버)

서버는 브라우저가 없어 자동 OAuth가 불가능합니다. **PC에서** 토큰을 발급해 동기화하세요:

1. PC: `py scripts/auth_kakao.py` → 로그인 후 코드 붙여넣기
2. PC의 `btc_live_trading/.env`와 `btc_live_trading/kakao_code.json`을 서버로 복사
3. `sudo systemctl restart coinbot`

또는 서버 SSH에서: `py -3 scripts/auth_kakao.py --code "<인가코드>"`
카카오 알림이 필요 없으면 `.env`에 `KAKAO_ALERTS_ENABLED=false`.

---

## 5. 주의사항

| 항목 | 설명 |
|------|------|
| IP 변경 | 인스턴스 재생성 시 IP 바뀜 → 바이낸스 화이트리스트 재등록 |
| 드라이런 | 처음엔 `.env`의 `AI_DRY_RUN=true`로 검증 권장 |
| 방화벽 | 아웃바운드만 필요(바이낸스·카카오·OpenAI). 인바운드 불필요 |
