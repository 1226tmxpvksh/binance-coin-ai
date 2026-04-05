# VULTR 서버 배포 가이드

## 1. 옮길 폴더

`btc_live_trading` 폴더 전체를 복사하면 됩니다.

### ❌ 서버에 옮기지 말 것
| 항목 | 이유 |
|------|------|
| `.env` | API 키 포함. Git/메일로 보내지 말고 **서버에서 새로 생성** |
| `*.log` | 로그 파일 (불필요) |
| `__pycache__/` | Python 캐시 (자동 생성) |
| `venv/` | 가상환경 (서버에서 새로 만들기) |

### ✅ 반드시 포함
- `strategy/` 포함 모든 `.py` 파일
- `config_live.py`
- `requirements.txt`
- `README2.md` (참고용)

---

## 2. VULTR 서버에서 필수 작업

### 2.1 IP 화이트리스트 (가장 중요)

VULTR 서버의 **공인 IP**를 바이낸스 API 화이트리스트에 추가해야 합니다.

```bash
# 서버에서 현재 공인 IP 확인
curl -s ifconfig.me
```

→ 나온 IP를 바이낸스 API Management → IP 제한에 추가

PC IP(116.120.157.206)와 다르므로 반드시 새로 추가해야 합니다.

### 2.2 .env 파일 생성

서버에서 직접 `.env` 생성 (절대 Git/메일에 넣지 말 것):

```bash
cd /path/to/btc_live_trading
nano .env
```

내용 (PC의 .env 값 복사해서 붙여넣기):
```
BINANCE_API_KEY=실제_키
BINANCE_API_SECRET=실제_시크릿
KAKAO_REST_API_KEY=...
KAKAO_ACCESS_TOKEN=...
# 등
```

### 2.3 타임존 설정 (9시 = 한국시간)

일봉 전략이 9시(한국)에 실행되므로 서버 시간을 한국시간으로 맞춥니다.

```bash
sudo timedatectl set-timezone Asia/Seoul
timedatectl   # 확인
```

### 2.4 Python 및 패키지 설치

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3 python3-pip python3-venv -y

cd /path/to/btc_live_trading
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2.5 테스트 실행

```bash
cd /path/to/btc_live_trading
source venv/bin/activate
python main_live.py
```

`YES` 입력 후 정상 동작하는지 확인.

---

## 3. 24/7 백그라운드 실행 (systemd)

프로그램이 계속 실행되도록 systemd 서비스로 등록합니다.

```bash
sudo nano /etc/systemd/system/btc-trader.service
```

내용:
```ini
[Unit]
Description=BTC Live Trading Bot
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/btc_live_trading
ExecStart=/home/YOUR_USERNAME/btc_live_trading/venv/bin/python main_live.py
Restart=always
RestartSec=10
Environment=PATH=/home/YOUR_USERNAME/btc_live_trading/venv/bin

[Install]
WantedBy=multi-user.target
```

`YOUR_USERNAME`, 경로를 본인 환경에 맞게 수정 후:

```bash
sudo systemctl daemon-reload
sudo systemctl enable btc-trader
sudo systemctl start btc-trader
sudo systemctl status btc-trader
```

로그 확인:
```bash
sudo journalctl -u btc-trader -f
```

---

## 4. 체크리스트

- [ ] VULTR 공인 IP를 바이낸스 화이트리스트에 추가
- [ ] 서버에 .env 생성 (API 키 입력)
- [ ] 타임존 Asia/Seoul 설정
- [ ] venv 생성 후 requirements 설치
- [ ] 한 번 수동 실행으로 동작 확인
- [ ] systemd 등록 후 `status` 확인

---

## 5. 주의사항

| 항목 | 설명 |
|------|------|
| **IP 변경** | VULTR 인스턴스 재생성 시 IP 바뀜 → 바이낸스 화이트리스트 다시 등록 |
| **드라이런** | 처음엔 `config_live.py`에서 `DRY_RUN=True`로 테스트 권장 |
| **로그** | `live_trading.log`는 WorkingDirectory 기준으로 생성됨 |
| **방화벽** | 아웃바운드만 필요 (바이낸스, 카카오). 인바운드 포트 열 필요 없음 |
