# AI 코인 자동매매 시스템

Binance USDT-M 선물 **BTC/USDT** 차트를 AI가 실시간 분석하고, 조건 충족 시 자동으로 진입·청산하는 **24/7 무인 매매 봇**입니다.  
매매 판단·리스크 관리·알림·토큰 갱신까지 하나의 데몬 프로세스(`main_ai.py`)로 통합 운영합니다.

```
15m 시장 스냅샷 → 기술적 게이트(ATR·거래량) → OpenAI 판단 → 리스크·주문 → 카카오톡 리포트
```

---

## 시스템 개요

| 항목 | 설명 |
|------|------|
| **대상 시장** | Binance USDT-M 선물 BTC/USDT (15분봉 기준) |
| **판단 엔진** | OpenAI GPT — 진입·모니터링·손실 사후분석 |
| **운영 주기** | **5분마다** 시장 감시 (`AI_LOOP_SECONDS=300`) |
| **알림** | 카카오톡 — **하루 1회(KST) 상태 리포트** + **토큰 갱신 성공 시 즉시 통보** |
| **배포** | Vultr Linux + **systemd** (`coinbot.service`) 24/7 데몬 |

시스템은 **5분 단위로 시장을 감시**합니다.  
카카오 토큰은 **카톡 자동로그인처럼** 동작합니다 — 평시 HTTP 갱신 없음, **알림 전송(로그인 시도) 시 만료됐을 때만** refresh 하고 `.env` + `kakao_code.json`에 즉시 저장·검증합니다.  
평시에는 **하루 1회(KST)** 상태 리포트를 발송합니다 (`AI_STATUS_REPORT_MINUTES=1440`, `KAKAO_ONCE_PER_DAY=true`).  
리포트에는 **당일(KST) 매수·매도(진입·청산) 내역**이 포함됩니다.  
액세스 토큰이 HTTP로 갱신되면 **갱신 성공 알림**을 별도로 보냅니다(일일 1회 한도 제외).

---

## 주요 기술적 특징

### 1. Stateless 알림 아키텍처 (Stale Token 제거)

24/7 데몬 환경에서 `KakaoNotifier`가 초기화 시점의 토큰을 메모리에 보관하면, 백그라운드 갱신(`kakao_utils`)과 불일치가 생겨 **Replay Attack → 토큰 패밀리 전체 폐기**가 발생할 수 있습니다.

**해결:** 알림 모듈은 토큰을 인스턴스 상태(`self`)에 **전혀 보관하지 않습니다.**  
전송·갱신 시마다 `kakao_code.json` 정본과 `os.environ`에서 **실시간 조회**만 수행합니다.

```
KakaoNotifier.send_message()
  └─ hydrate_tokens_from_json() → get_access_token()   # 매 호출 fresh read
  └─ 401 시 refresh_kakao_access_token_sync()          # 중앙 Thread-safe 갱신
```

### 2. Kakao OAuth2 — Thread-safe 토큰 관리

| 메커니즘 | 구현 |
|----------|------|
| **중앙 갱신 채널** | `refresh_kakao_access_token_sync()` — Double-checked locking, Refresh HTTP 1회만 |
| **원자적 저장** | 임시 파일 → `os.replace` → write-after-read 검증 |
| **회전 처리** | refresh_token 회전 시 `.env` + `kakao_code.json` + `os.environ` 3곳 동기화 |
| **환경 화이트리스트** | `kakao_api_allowed()` — 정품 서버·경로에서만 API 허용 (로컬/WSL 차단) |
| **Safety First** | 카카오 Heartbeat 실패 시 해당 사이클 매매 차단, 프로세스는 유지·재시도 |
| **토큰 갱신 알림** | HTTP refresh 성공 시 카카오 통보 (`register_kakao_token_refresh_listener`) — **락 해제 후** 발송(데드락 방지) |
| **디스크 강제 저장** | 갱신 성공 시 `.env` + `kakao_code.json` 원자 저장 → 재읽기 100% 일치 검증. 실패 시 `[ERROR] 토큰 파일 덮어쓰기 실패!` |
| **refresh 생략 방어** | 카카오가 응답에서 `refresh_token`을 빼면(잔여 수명 충분) **기존 refresh를 유지**하고 access만 갱신 — 빈칸 덮어쓰기 금지 |
| **refresh 수명 기록** | 응답의 `refresh_token_expires_in` → `kakao_code.json`의 `refresh_expires_at` 저장. 잔여 1개월 미만이면 WARNING |
| **자동 로그인 갱신** | 알림 전송 직전 로컬 `expires_at` 만료일 때만 refresh (카카오 API 정책·부하 회피) |
| **갱신 3회 재시도** | refresh 실패 시 정본 재동기화 후 최대 3회 재시도 — 실행 중 재인증한 새 토큰 즉시 반영 |
| **인증 대기 모드** | `invalid_grant` 시 매매 차단. 디스크에 낡은 토큰이 있어도 통과하지 않음. `KAKAO_AUTH_RETRY_MINUTES`(기본 30분)마다 **실제 refresh**로만 복구 재시도 — **재시작 불필요** |
| **알림 워커 자가 복구** | `AsyncNotifier` 스레드가 죽어도 다음 알림 시 `is_alive()` 검사 후 자동 재기동 |
| **생존 하트비트** | access 로컬 만료 시 **refresh 1회 점검**(죽은 refresh를 자정까지 방치하지 않음). 정상일 때만 INFO `게이트 정상` |
| **RT 헬스체크(진단)** | `scripts/kakao_rt_healthcheck.py` — coinbot과 분리, 15분마다 force refresh → `data/kakao_rt_health.jsonl`. 조회: `python scripts/kakao_rt_health_report.py` |
| **일일 리포트 생존** | `_send_daily_status_report` — 생성/발송 예외를 삼킴(`[ERROR] 일일 리포트 발송 중 오류 발생`). 실패 시 `last_report` 미갱신 → 다음 사이클/다음 날 재시도 |

별도의 “6시간 갱신 스레드”는 없습니다. 매매 게이트는 디스크 자격증명(+인증 대기 모드)을 보고, **access 만료 시 하트비트·알림 경로에서 refresh**합니다. 저장 검증에 실패하면 갱신을 성공으로 취급하지 않습니다.

### 3. 다층 방어 기제

| 계층 | 내용 |
|------|------|
| **단일 프로세스** | `.coinbot.lock` + `fcntl`/`msvcrt` — 중복 `main_ai.py` 실행 차단 |
| **루프 중복 방지** | `_CYCLE_LOCK` + `AI_LOOP_SECONDS` 간격 가드 + 동일 15m 캔들 AI 1회 |
| **네트워크 복원력** | 카카오 401 → 중앙 갱신 후 1회 재전송; refresh 실패는 3회 재시도, `invalid_grant` 원인은 ERROR 로그로 기록 |
| **로그 스팸 방지** | 인증 차단 ERROR는 `KAKAO_BLOCKED_LOG_MINUTES`(기본 60분)마다 1회만 — 5분마다 반복되던 에러 제거 |
| **알림 경로 생존 보장** | 갱신 성공 알림은 락 해제 후 발송(데드락 방지), 알림 워커 사망 시 자동 재기동, `KAKAO_HEARTBEAT_LOG_MINUTES`(기본 60분)마다 생존 INFO 하트비트 |
| **토큰 디스크 정본** | refresh 후 메모리만 갱신하지 않음 — `.env`/`kakao_code.json` 저장 실패 시 갱신 실패로 처리 |
| **진입 게이트** | ATR 최소 변동성, 7일 평균 대비 거래량 급증(`AI_VOLUME_MIN_RATIO`), 주말 신규 진입 차단 |
| **매매 모드** | `AI_DRY_RUN=true` 가상 매매 / `false` 실전 매매 — 동일 코드 경로 |
| **원금 복구 리포팅** | 잔고 < 초기 원금 시 「원금 복구 중」 표기, 착시 수익 방지 |

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| **언어** | Python 3 |
| **거래소** | Binance Futures API (USDT-M) |
| **AI** | OpenAI GPT (`gpt-4o` 진입 / `gpt-4o-mini` 모니터링) |
| **알림** | Kakao REST API (OAuth2, 나에게 보내기) |
| **서비스 관리** | systemd (`coinbot.service`) |
| **배포** | Vultr VPS, WinSCP + SSH |
| **데이터** | JSON 원장, JSONL 거래 로그, CSV 학습 로그 |

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  systemd (coinbot.service)                                  │
│  └─ ai_trading/main_ai.py          ← 메인 루프 (5분 주기)   │
│       ├─ data/market_data.py       ← Binance 15m 스냅샷     │
│       ├─ ai_logic/decision_engine  ← OpenAI 매매 판단       │
│       ├─ binance_futures_tools     ← 선물 주문·청산         │
│       ├─ reporting.py              ← 원장·리포트            │
│       └─ _notify_kakao()           ← 알림 (Stateless)       │
├─────────────────────────────────────────────────────────────┤
│  btc_live_trading/                                          │
│       ├─ kakao_utils.py            ← OAuth2·토큰 정본       │
│       ├─ kakao_notifier.py         ← 카카오 메시지 (무상태) │
│       └─ strategy/                 ← 공용 리스크·전략       │
├─────────────────────────────────────────────────────────────┤
│  btc_day_strategy/                 ← 백테스트·전략 검증     │
│  scripts/                          ← 인증·운영 스크립트     │
└─────────────────────────────────────────────────────────────┘
```

**데이터 흐름:** 15m 스냅샷(5분 1회) → ATR·거래량 게이트 → AI 판단 → 리스크·주문 → 원금 기준 리포트 → 카카오톡

---

## 프로젝트 구조

| 경로 | 역할 |
|------|------|
| **`ai_trading/`** | 메인 루프, AI 판단, 선물 주문, 리포트, 학습 로그 |
| **`btc_live_trading/`** | `.env`, 카카오 OAuth, 환율, 공용 전략 모듈 |
| **`btc_day_strategy/`** | 백테스트·전략 라이브러리 |
| **`scripts/`** | `coinbot_watch.sh`, `auth_kakao.py`, `check_kakao_auth.py` 등 |

---

## 운영 가이드

### 일상 배포 (코드 수정 후)

```bash
# 서버 SSH (root)
pgrep -af main_ai.py
systemctl restart coinbot.service
journalctl -u coinbot.service -f
```

정상 로그: `카카오톡 메시지 전송 성공`, `[AI 매매 판단]` 대시보드

### 카카오 재인증 (토큰 만료 시)

```bash
bash ~/Coin/scripts/coinbot_watch.sh
# 또는
python ~/Coin/scripts/auth_kakao.py
```

→ URL 로그인 → code 붙여넣기 → `systemctl restart coinbot.service`

### 로컬 실행 (개발·Dry-run)

```bash
py -3 ai_trading\main_ai.py
```

`.env`에서 `AI_DRY_RUN=true` 권장. 카카오 API는 **운영 서버에서만** 동작합니다.

---

## 환경 변수 (핵심)

```env
AI_DRY_RUN=false                 # true=가상, false=실전
AI_LOOP_SECONDS=300              # 감시 주기 (초)
AI_TIMEFRAME=15m
AI_STATUS_REPORT_MINUTES=1440     # 상태 리포트: 1440=하루 1회(KST)
KAKAO_ONCE_PER_DAY=true           # true=일일 상태 리포트 1회(토큰 갱신 알림은 제외)
AI_VOLUME_MIN_RATIO=1.5          # 7일 평균 대비 거래량 급증 기준
KAKAO_ALERTS_ENABLED=true        # false 시 알림·게이트 우회
KAKAO_AUTH_RETRY_MINUTES=30      # 인증 대기 모드 자동 복구 주기
KAKAO_BLOCKED_LOG_MINUTES=60     # 인증 차단 ERROR 로그 최소 간격
KAKAO_HEARTBEAT_LOG_MINUTES=60   # 토큰 검증 루프 생존 INFO 하트비트 간격
```

전체 목록은 [`ai_trading/README.md`](ai_trading/README.md) 참고.

---

## 상세 문서

| 문서 | 내용 |
|------|------|
| [**START.md**](START.md) | Vultr 서버 일상 시작·카카오 재인증 |
| [**ai_trading/README.md**](ai_trading/README.md) | 환경변수, 리스크, 대시보드, 카카오 운영 |
| [**btc_live_trading/VULTR_DEPLOY.md**](btc_live_trading/VULTR_DEPLOY.md) | 최초 서버 배포(systemd, venv) |
| [**btc_day_strategy/README.md**](btc_day_strategy/README.md) | 일봉 백테스트·전략 비교 |

---

## Git 제외 (민감·런타임 데이터)

`.env`, `kakao_code.json`, `trading_stats.json`, `virtual_trades.jsonl`, `ai_learning_logs.csv`, `.coinbot.lock`  
→ WinSCP로 **코드만** 업로드하고, 토큰·운영 데이터는 서버에 유지합니다.

---

## 라이선스·면책

본 프로젝트는 개인 학습·포트폴리오 목적으로 작성되었습니다.  
암호화폐 자동매매는 원금 손실 위험이 있으며, 실전 투자 결정과 그에 따른 책임은 운영자에게 있습니다.
