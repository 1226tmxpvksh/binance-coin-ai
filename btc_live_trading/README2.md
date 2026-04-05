# BTC 실전 매매 — 일봉 돌파 & AI 단타(Scalping)

> **한 문서**: 설치, 두 가지 실행 모드, 전략, 구독료 목표 추적, 보안, 트러블슈팅

---

## 왜 이 프로젝트인가 (프로젝트 동기)

**Gemini, Cursor, Netflix 등 월 고정 구독료(약 10만 원)** 를 스스로 충당하는 것을 목표로 한 **AI 비서형 트레이딩 시스템**입니다.  
바이낸스 선물에서 규칙 기반·AI 컨펌·안전장치를 묶어, **손실 한도 안에서** 소액 시드로도 목표를 추적(`GoalTracker` + 카카오)합니다.

| 목표 | 설명 |
|------|------|
| 월 목표 | 구독료 수준 **약 10만 원**(KRW, 설정값) — 알림에 달성률·남은 금액 표시 |
| 리스크 | 일일 손실·긴급 정지 %, `DRY_RUN` 선행 검증 |
| AI 역할 | 단타 진입 전 **GPT-4o 컨펌**(실패 시 **fail_closed** = 진입 취소) |

---

## 중요 경고

- [ ] 실제 자금이 움직일 수 있습니다.
- [ ] **최소 1주일 `DRY_RUN=True`** 후 실전 전환을 권장합니다.
- [ ] 백테스트·시뮬 성과 ≠ 실전 (슬리피지, 체결, API 장애).
- [ ] 일봉 봇과 단타 봇을 **같은 계정에서 동시에** 돌리면 포지션이 겹칠 수 있습니다.

---

## 1. 전략 개요

### 1.1 일봉 변동성 돌파 (`TRADING_MODE` 미설정 또는 `day`)

| 항목 | 내용 |
|------|------|
| 타이밍 | 매일 **한국시간 9시** 전후 체크 |
| 방향 | 상승장 LONG만 / 하락장 SHORT만 / 중립 미진입 |
| 진입 | ATR 기반 K값 변동성 돌파 + 거래량 필터 |
| 청산 | SHORT 익일 강제 / LONG 최대 3일·트레일링 등 |
| 실행 | `python main_live.py` (기본) |

### 1.2 AI Scalping 단타 (`TRADING_MODE=scalping`)

| 항목 | 내용 |
|------|------|
| 대상 | 기본 **BTC·ETH** 선물 (`config_scalping.py` / `SCALPING_SYMBOLS`) |
| 봉 | 예: **5m** (설정 가능) |
| 1차 신호 | **RSI·볼린저** 규칙 (`strategy/scalping_strategy.py`) |
| AI 컨펌 | **GPT-4o** (`utils/ai_advisor.py`) — API·파싱 실패 시 **진입 안 함** |
| 익절 | 미실현 손익이 **+1만 원(KRW 환산 USDT)** 이상이면 시장가 청산 |
| 손절 | 진입가 대비 **-5.0%** (`STOP_MARKET` + 엔진 이중 확인) |
| 안전장치 | `safety_manager` — 일일 손실·긴급 정지·일일 거래 횟수 (단타용 설정) |

### 1.3 목표 추적·알림 (단타)

- **`GoalTracker`**: 월간 **실현 손익(USDT)** 누적, KST 기준 월 경계.
- **카카오톡**: 스캘핑 알림마다  
  `이번 달 구독료 목표(10만 원)까지 남은 금액: XXX원` 및 달성률 문구 포함.

---

## 2. 실행 모드 가이드

### 2.1 일봉 (기본)

```bash
cd btc_live_trading
python main_live.py
```

- 설정: `config_live.py`, `.env` (`BINANCE_*`, 카카오 등).
- 실전: `DRY_RUN=False` + 터미널 `YES` 또는 `LIVE_TRADING_CONFIRMED=YES`.

### 2.2 AI Scalping

```bash
set TRADING_MODE=scalping
# PowerShell: $env:TRADING_MODE="scalping"
python main_live.py
```

| 환경 변수 / 설정 | 설명 |
|------------------|------|
| `TRADING_MODE=scalping` | 단타 엔진·`config_scalping.py` 경로 사용 |
| `OPENAI_API_KEY` | `.env` 또는 환경변수 (`ai_advisor`가 패키지 루트 `.env`도 로드) |
| `USE_AI_CONFIRM` | `true`(기본): AI 승인 없으면 진입 안 함. `false`: 규칙 신호만 |
| `SCALPING_LIVE_CONFIRMED=YES` | 실전·비대화형 시 필수 (`config_scalping` 참고) |
| `SCALPING_DRY_RUN` / `DRY_RUN` | 단타 테스트 시 `true` 권장 |

`config_scalping.py`에서 심볼, 봉 주기, `TP_PROFIT_KRW`, `MONTHLY_TARGET_KRW`, `KRW_PER_USDT` 등 조정합니다.

---

## 3. 폴더 구조 (요약)

```
btc_live_trading/
├── strategy/
│   ├── indicators.py          # 일봉 + RSI/BB (스캘핑)
│   ├── scalping_strategy.py
│   ├── risk_manager.py
│   └── ...
├── utils/
│   ├── ai_advisor.py          # GPT-4o 컨펌
│   └── indicator_prompt.py
├── main_live.py               # day / scalping 분기
├── live_trading_engine.py     # 일봉
├── scalping_engine.py         # 단타
├── config_live.py / config_scalping.py
├── goal_tracker.py
├── safety_manager.py
├── order_executor.py
├── kakao_notifier.py
└── tests/test_scalping_dry_run_notify.py
```

---

## 4. 시작하기 (공통)

### 4.1 설치

```bash
cd btc_live_trading
pip install -r requirements.txt
```

### 4.2 `.env` 예시

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
OPENAI_API_KEY=          # 스캘핑 AI 컨펌용
KAKAO_REST_API_KEY=
KAKAO_ACCESS_TOKEN=
```

### 4.3 카카오톡

- [ ] [Kakao Developers](https://developers.kakao.com/console/app) 앱 생성  
- [ ] Redirect URI: `https://example.com/oauth`  
- [ ] 동의항목: **카카오톡 메시지 전송**  
- 토큰 만료 시 콘솔 안내 URL로 재발급

---

## 5. 일봉 전용 메모

- **9시**: 데이터·진입/청산·일일 요약 알림.  
- 손절/익절 조건부 주문은 거래소가 24시간 처리.  
- `daily_order_scheduler.py`는 LONG 위주 보조 스크립트 — `main_live`와 역할이 겹칠 수 있음.

---

## 6. 안전·보안 체크리스트

| 항목 | 일봉(참고) | 단타(참고) |
|------|------------|------------|
| 일일 손실 상한 | config_live | config_scalping |
| 긴급 정지 | 초기 자본 대비 % | 동일 개념 |
| API | 선물·읽기, 출금 비활성화 권장 | 동일 |
| 로그 | 키 마스킹 | 동일 |

---

## 7. 트러블슈팅

| 증상 | 확인 |
|------|------|
| AI 항상 미진입 | `OPENAI_API_KEY`, `USE_AI_CONFIRM`, 로그 fail_closed |
| 목표 JSON 오류 | `goal_tracker` — 빈/깨진 파일은 자동 무시 후 0부터 |
| ModuleNotFoundError | `btc_live_trading`에서 실행, `pip install -r requirements.txt` |

---

## 8. FAQ

- **백테스트**: `btc_day_strategy` 폴더  
- **단타 백테스트**: 동일한 **지표·리스크 모듈 패턴**으로 이 레포에 스크립트를 추가해 검증 가능 (일봉 엔진과 분리된 전략 모듈 구조)  
- **여러 코인**: 단타는 `SCALPING_SYMBOLS`, 일봉은 `SYMBOL` 단일 중심

---

## 9. 향후 발전 방향 (Roadmap)

운영 관점에서 아래를 순차 검토할 수 있습니다.

- [ ] **Docker**: `Dockerfile` / Compose로 `TRADING_MODE`·`.env` 마운트, 재시작 정책  
- [ ] **Zabbix(또는 유사 모니터링)**: 프로세스·로그 패턴·마지막 거래 시각 trap 아이템  
- [ ] **헬스체크**: HTTP 또는 스크립트로 “살아 있음”·잔고 조회 주기 보고  
- [ ] 단타 전용 **백테스트 스크립트**를 `btc_day_strategy` 또는 본 폴더에 추가

---

## 10. 면책

교육·연구 목적. 손실 책임은 사용자에게 있습니다.

---

**행운을 빕니다**
