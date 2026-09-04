# Coin — AI BTC 자동매매 (프로젝트 지도)

> **이 문서는 “매 대화마다 맥락이 없는 외부 AI 어시스턴트”가 저장소 구조를 빠르게 파악하기 위한 진입점입니다.**  
> 사람용 배포·일상 절차는 [DEPLOY.md](DEPLOY.md)를 보세요.

## 한눈에

Binance **USDT-M 선물 BTC/USDT**를 OpenAI가 주기적으로 분석하고, 조건이 맞으면 진입·청산하는 **24/7 무인 매매 봇**입니다.  
알림 기본 채널은 **디스코드 웹훅**이고, 상태 조회는 **별도 디스코드 슬래시 봇**입니다.  
카카오톡 알림은 RT 조기 폐기 문제로 **`legacy/kakao/`에 보존**만 하고 기본 경로에서는 쓰지 않습니다.

---

## 디렉터리 구조

```
Coin/
├── README.md                 ← 지금 이 파일 (AI/구조 진입점)
├── DEPLOY.md                 ← Vultr 최초 배포 + 일상 재시작
├── ai_trading/               ← 매매 엔진 (coinbot.service 가 실행)
│   ├── main_ai.py            ← 메인 루프 (주문·리스크·리포트)
│   ├── reporting.py          ← trading_stats / 원장 요약
│   ├── binance_futures_tools.py
│   ├── risk_guard.py
│   ├── ai_logic/decision_engine.py
│   └── data/                 ← stats, virtual_trades.jsonl (런타임)
├── btc_live_trading/         ← 공유 .env, 알림, 전략 지표
│   ├── .env                  ← 시크릿·운영 파라미터 (커밋 금지)
│   ├── discord_notifier.py   ← 웹훅 단방향 알림
│   ├── async_notifier.py     ← 알림 워커 큐
│   ├── status_query.py       ← /status 읽기 전용 스냅샷
│   ├── fx_rates.py
│   ├── strategy/             ← 지표·진입/청산·리스크 헬퍼
│   ├── kakao_utils.py        ← shim → legacy/kakao
│   └── kakao_notifier.py     ← shim → legacy/kakao
├── legacy/kakao/              ← 카카오 OAuth·알림·진단 (보존)
├── scripts/
│   ├── discord_bot.py        ← /status /health 슬래시 봇
│   ├── test_discord_send.py
│   ├── coinbot_watch.sh      ← 로그 팔로우 (+카카오 인증 시)
│   ├── emergency_exit.py
│   ├── reset_live_ledger.py
│   └── auth_kakao.py 등       ← shim → legacy/kakao
├── deploy/
│   └── coinbot-discord-bot.service
└── btc_day_strategy/         ← 백테스트 도구 (시작 시 연결 점검)
```

---

## 알림 채널

| 모드 | 설정 | 구현 |
|------|------|------|
| **기본** | `NOTIFY_CHANNEL=discord` | `discord_notifier.py` + `DISCORD_WEBHOOK_URL` |
| 레거시 | `NOTIFY_CHANNEL=kakao` | `legacy/kakao/` (shim으로 기존 import 유지) |

카카오로 되돌리기: `.env`에서 `NOTIFY_CHANNEL=kakao` → `systemctl restart coinbot.service`.  
자세한 복구: [legacy/kakao/README.md](legacy/kakao/README.md).

슬래시 조회(`/status`, `/health`)는 웹훅과 **별도 프로세스** (`scripts/discord_bot.py`, `DISCORD_BOT_TOKEN`).

---

## `.env` 섹션 요약 (값은 넣지 말 것)

파일: `btc_live_trading/.env`

1. **거래소** — `BINANCE_API_KEY` / `SECRET`  
2. **OpenAI** — API Key + 진입/모니터 모델명  
3. **알림** — `NOTIFY_CHANNEL`, Discord 웹훅·봇 토큰, `KAKAO_*`(레거시)  
4. **매매 엔진** — `AI_DRY_RUN`, 심볼, 루프 초, 레버리지, 리스크 비율, 가상원금  
5. **리스크/필터** — ATR, R/R, 거래량 게이트, 오답노트 유사도  
6. **운영/로그** — 비용 추정, 트레이드/학습 로그 최대 줄 수  

⚠️ **시크릿·토큰·웹훅 URL을 채팅/README/커밋에 붙이지 말 것.**

---

## 두 개의 systemd 프로세스

| 서비스 | 역할 | 재시작 |
|--------|------|--------|
| `coinbot.service` | `main_ai.py` 매매 루프 | `systemctl restart coinbot.service` |
| `coinbot-discord-bot.service` | 슬래시 조회만 (매매 호출 없음) | `systemctl restart coinbot-discord-bot.service` |

한쪽이 죽어도 다른 쪽에 영향 없도록 **분리**되어 있습니다.

---

## 자주 쓰는 명령

```bash
# 매매
systemctl restart coinbot.service
journalctl -u coinbot.service -f
pgrep -af main_ai.py

# 디스코드 조회 봇
systemctl restart coinbot-discord-bot.service
journalctl -u coinbot-discord-bot.service -f

# 테스트
cd /home/bot2/Coin && source ~/venv/bin/activate
python scripts/test_discord_send.py
python scripts/discord_bot.py          # 토큰 없으면 즉시 종료(의도)

# 카카오 레거시 (NOTIFY_CHANNEL=kakao 일 때)
bash scripts/coinbot_watch.sh
python scripts/auth_kakao.py

# 비상 청산
python scripts/emergency_exit.py
```

배포 절차 전문: [DEPLOY.md](DEPLOY.md).

---

## 이 프로젝트를 처음 보는 AI가 알아야 할 주의사항

1. **`AI_DRY_RUN=false`이면 실전 매매 중**이다. 주문·청산·리스크 파라미터를 함부로 바꾸지 말 것.  
2. **`legacy/kakao/kakao_utils.py`의 토큰 저장·회전·원자 저장 로직은 이미 검증된 정상 코드**다. “정리” 목적으로 리팩터링하지 말 것. 위치만 격리된 것이다.  
3. **`.env` 값, API 키, 봇 토큰, 웹훅 URL은 민감정보**다. 대화·문서·커밋·로그에 원문을 넣지 말 것.  
4. **조회 봇에서 매매 함수를 호출하지 말 것.** `/buy` `/sell` 같은 명령은 안전장치 합의 전 금지.  
5. **`main_ai.py` / `discord_notifier.py` / `status_query.py` 핵심 경로**는 요청 없이 대규모 리팩터하지 말 것.  
6. 카카오 자격증명은 **`btc_live_trading/.env` + `kakao_code.json`** 에 남긴다. `legacy/kakao/`로 옮기지 말 것.

---

## 상세 문서

| 문서 | 용도 |
|------|------|
| [DEPLOY.md](DEPLOY.md) | 서버 배포·재시작·카카오 재인증 |
| [ai_trading/README.md](ai_trading/README.md) | 엔진·환경변수·리스크 상세 |
| [legacy/kakao/README.md](legacy/kakao/README.md) | 카카오 레거시 복구 |
| [btc_day_strategy/README.md](btc_day_strategy/README.md) | 백테스트 |
