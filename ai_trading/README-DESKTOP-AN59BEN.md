# AI Trading 운영 가이드

`ai_trading`은 Binance 시장 데이터, `btc_day_strategy` 백테스트 자료, OpenAI 판단 로직을 결합해 BTC 매매를 운영하는 엔진입니다. **실전 운영 시 `AI_DRY_RUN=false`** 로 두고, 주문·잔고·청산은 **`binance_futures_tools`** 의 Binance USDT-M API만 사용합니다. 가상 모드는 검증·교육용으로만 켭니다.

## 프로젝트 구조 (실전 기준)

| 위치 | 설명 |
|------|------|
| `ai_trading/main_ai.py` | 무한 루프·시그널 종료·실전/가상 분기 |
| `ai_trading/binance_futures_tools.py` | 잔고 조회 `fetch_futures_usdt_balance_from_env`, 진입·청산·비상 청산 공용 |
| `ai_trading/reporting.py` | `trading_stats.json`, 월 백업 파일명 |
| `ai_trading/risk_guard.py` | 포지션 리스크 계산 |
| `ai_trading/ai_logic/decision_engine.py` | OpenAI 호출 |
| `ai_trading/data/` | 통계·`virtual_trades.jsonl`·`ai_learning_logs.csv`·로그 |
| `btc_live_trading/.env` | API 키·알림·운영 변수(공유) |
| `btc_live_trading/fx_rates.py` | USDT/KRW (CoinGecko) |
| `legacy/kakao/` | 카카오 OAuth·알림 레거시 (shim: `btc_live_trading/kakao_*.py`) |
| `btc_day_strategy/` | 백테스트·전략 라이브러리 (시작 시 연결 점검) |

상위 폴더 개요·주의사항은 저장소 루트 **`README.md`**, 배포는 **`DEPLOY.md`** 를 참고하세요.

## 주요 기능

- 5분 주기 시장 감시와 단발 점검 실행을 지원합니다.
- RSI/EMA/Bollinger 로컬 게이트를 먼저 통과한 경우에만 진입용 AI 판단을 호출해 OpenAI 토큰 사용량을 줄입니다.
- `trading_stats.json`, `virtual_trades.jsonl`, `ai_learning_logs.csv`에 운영 상태와 학습 기록을 저장합니다.
- 카카오 액세스 토큰 만료 시 `refresh_kakao_access_token_sync()`로 중앙 갱신하고, `.env`와 `kakao_code.json`에 동기화합니다.
- HTTP refresh 성공 시 **카카오톡 갱신 성공 알림**을 발송합니다(`KAKAO_ONCE_PER_DAY` 한도와 별도).
- **일일 상태 리포트**(`AI_STATUS_REPORT_MINUTES=1440`)에 **당일(KST) 매수·매도 내역**(`virtual_trades.jsonl`)을 포함합니다.
- **`KakaoNotifier`는 Stateless** — `self`에 토큰을 보관하지 않고, 전송·갱신 시마다 정본에서 실시간 조회합니다 (Stale Token·Replay Attack 방지).
- refresh-token까지 만료되면 터미널에 인가 URL을 표시하고 새 인가 코드를 입력받아 즉시 세션을 복구합니다.
- 실행 시작 시 `btc_live_trading`, `btc_day_strategy` 핵심 모듈이 정상 로드되는지 표 형태로 폴더 연결성 체크를 수행합니다.
- **USDT→KRW**는 CoinGecko `tether` 대비 `krw` 시세를 주기적으로 조회합니다(`btc_live_trading/fx_rates.py`). 수동 `KRW_PER_USDT` 설정은 제거되었습니다.
- **실전(`AI_DRY_RUN=false`)**일 때 카카오 리포트의 잔고·수익률 줄은 Binance **USDT-M 선물 지갑** `futures_account_balance`의 USDT를 사용합니다. 조회 실패 시에만 원장(`virtual_balance_*`)으로 표시합니다.
- 카카오 제목·본문 첫머리는 `[실전 매매 리포트]` / `[가상 매매 리포트]`로 구분됩니다.

## 설치

저장소 루트(`Coin/`)에서:

```powershell
py -3 -m pip install -r btc_live_trading\requirements.txt
py -3 -m pip install -r ai_trading\requirements.txt
py -3 -m pip install -r btc_day_strategy\requirements.txt
```

OpenAI 관련 패키지가 별도로 필요하면:

```powershell
py -3 -m pip install openai requests python-dotenv
```

## 환경변수 설정

운영 환경변수는 기본적으로 `btc_live_trading\.env`를 사용합니다. 파일이 없다면 같은 경로에 새로 만듭니다.

```env
# === 매매 모드 ===
AI_DRY_RUN=false                 # true=가상, false=실전

# === AI / OpenAI ===
OPENAI_MODEL=gpt-4o
OPENAI_ENTRY_MODEL=gpt-4o
OPENAI_MONITOR_MODEL=gpt-4o-mini

# === 루프·리포트 ===
AI_LOOP_SECONDS=300              # 5분마다 시장 감시
AI_TIMEFRAME=15m
AI_STATUS_REPORT_MINUTES=1440     # 1440=하루 1회(KST) 상태 리포트(당일 매매 내역 포함)
KAKAO_ONCE_PER_DAY=true           # true=일일 리포트 1회(토큰 갱신 성공 알림은 별도)
AI_RUN_ONCE=false

# === 가상 자산 (Dry-run·원장 기준) ===
AI_VIRTUAL_INITIAL_KRW=500000
AI_VIRTUAL_INITIAL_USDT=362
AI_PAPER_HOLD_MINUTES=60

# === 진입 게이트 ===
AI_VOLUME_MIN_RATIO=1.5
AI_VOLUME_GATE_ENABLED=true
AI_ATR_MIN_ENTRY_PCT=0.15
AI_TRADE_ON_WEEKENDS=false

# === 리스크 ===
AI_ATR_STOP_MULTIPLIER=1.2
MONTHLY_TARGET_KRW=100000

# === 비용 집계 ===
AI_EST_DAILY_SERVER_COST_KRW=500
AI_MONTHLY_SUBSCRIPTION_COST_KRW=80000
AI_EST_TOKENS_PER_ENTRY_CALL=900
# OpenAI 단가(USD / 1K tokens). 미설정 시 비용 리포트는 호출 횟수만 표시
# OPENAI_PRICE_PER_1K_INPUT=0.0025
# OPENAI_PRICE_PER_1K_OUTPUT=0.01

# === 실패 유사도·반복 차단 (학습 로그 기반, 손익액 트리거 아님) ===
AI_FAILURE_SIM_STRONG=0.92
AI_FAILURE_SIM_MODERATE=0.85
AI_REPEAT_FAILURE_LOOKBACK=20
AI_REPEAT_FAILURE_BLOCK_COUNT=3

# === 2단계 모델 다운시프트 (보류 — 미구현, 설계만) ===
# 1단계(반복실패 차단·비용리포트) 효과를 1~2주 관찰한 뒤 재논의.
# AI_ENTRY_MODEL_FALLBACK=gpt-4o-mini
# AI_FALLBACK_ESCALATE_CONF=0.80   # mini 1차 채택 최소 confidence (초안 0.72 → 0.80으로 상향)
# 발동: 게이트 열림 + 반복실패 차단 아님 + MODERATE≤sim<STRONG
# 1차 fallback → BUY/SELL & conf≥0.80 이면 채택 / 아니면 주모델(gpt-4o) 재확인

# === 카카오 ===
KAKAO_ALERTS_ENABLED=true
KAKAO_AUTH_RETRY_MINUTES=30      # 인증 대기 모드에서 자동 복구 재시도 주기(분)
KAKAO_BLOCKED_LOG_MINUTES=60     # 인증 차단 ERROR 로그 최소 간격(분) — 그 사이는 DEBUG
KAKAO_HEARTBEAT_LOG_MINUTES=60   # 토큰 검증 루프 생존 INFO 하트비트 간격(분) — 매 사이클 DEBUG는 항상
AI_LEARNING_LOG_MAX_ROWS=5000
```

### 카카오 인증 값

- `KAKAO_REST_API_KEY`: 카카오 개발자 콘솔의 REST API 키입니다.
- `KAKAO_REDIRECT_URI`: 카카오 개발자 콘솔에 등록한 Redirect URI와 정확히 같아야 합니다.
- `KAKAO_ACCESS_TOKEN`: 카카오톡 메시지 발송용 액세스 토큰입니다.
- `KAKAO_REFRESH_TOKEN`: 액세스 토큰 자동 갱신에 사용하는 토큰입니다.

`KAKAO_REDIRECT_URI`가 콘솔 등록값과 다르면 토큰 발급 단계에서 KOE205가 발생합니다.

## 실행 방법

무한 루프 운영:

```powershell
# 저장소 루트(Coin/)에서
py -3 ai_trading\main_ai.py
```

단발 점검:

```powershell
$env:AI_RUN_ONCE="true"
py -3 ai_trading\main_ai.py
```

실행 시작 시 아래 형태의 표와 로그가 보이면 프로젝트 폴더 연결성 체크가 정상 통과한 것입니다.

```text
[Project Connectivity Health Check]
+------------------+--------+--------------------------+
| Folder           | Status | Detail                   |
+------------------+--------+--------------------------+
| btc_live_trading | OK     | 3 modules loaded         |
| btc_day_strategy | OK     | 3 modules loaded         |
+------------------+--------+--------------------------+

연결 확인: btc_live_trading 모듈 로드 완료
연결 확인: btc_day_strategy 전략 로드 완료
```

### 단일 실행 (Single Instance Lock)

- `__main__` 진입 직후 `_acquire_single_instance_lock()` — 프로젝트 루트 `Coin/.coinbot.lock` 독점.
- Linux: `fcntl.flock(LOCK_EX|LOCK_NB)` / Windows: `msvcrt.locking(LK_NBLCK)`.
- 이미 다른 인스턴스가 락을 쥐면 ERROR 로그 후 `sys.exit(1)` (**카카오 부트스트랩 전**).
- 서버에서 `pgrep -af main_ai.py`로 1프로세스만 떠 있는지 확인.

### Windows / WSL / 로컬 PC

- 카카오 API는 **Vultr 정품 서버만** 허용: `hostname==example1`, `project==/home/bot2/Coin` (하드코딩, `.env` 우회 불가).
- 로컬에서 `main_ai.py`를 실행해도 카카오·매매 테스트는 가능하나 **카카오 토큰은 서버에서만** 발급·갱신하세요.
- 카카오 인증: SSH → `bash ~/Coin/scripts/coinbot_watch.sh` 또는 `python ~/Coin/scripts/auth_kakao.py`

### 매매 루프 (5분 1회)

- `_initialize_trading_loop_once()` — 기동·카카오 부트스트랩은 프로세스당 1회.
- `_CYCLE_LOCK` — 사이클 중복 진입 차단.
- `_wait_for_next_cycle_slot()` — `AI_LOOP_SECONDS`(기본 300) 미만 재실행 방지.
- 동일 15m 캔들에 OpenAI 재호출 없음 (`candle_open_time_ms` 기준).

## 운영 및 모니터링

매 루프가 끝날 때마다 콘솔 로그는 **구조화된 대시보드 형태**로 출력됩니다. 원시 JSON(`json.dumps`) 대신, AI 판단·시장 지표·잔고·보유 포지션을 구역별로 나눈 텍스트 블록입니다.

```text
==================================================
[AI 매매 판단]
- 최종 결정: 🟢 BUY (신뢰도: 72%)
- 판단 사유: [유사 과거실패 sim≥0.88] 최근 손실 반성에서...
- AI 절감 토큰: 11,700

[시장 지표 (BTCUSDT)]
- 현재 가격: 79,027.37
- 핵심 지표: RSI 58.9 / EMA 갭 -0.05% / 볼린저 위치 0.66

[현재 포지션 및 잔고]
- 실잔고(USDT): 331.26
- 보유 포지션: 0.012 BTC [BUY] (진입가: 79,000.00 / 진입시간: 15:00)
==================================================
```

- **실전(`AI_DRY_RUN=false`)**이면 Binance 선물 지갑 USDT를 `실잔고(USDT)`로 표시합니다. 가상 모드는 `가상 잔고(USDT)`입니다.
- 포지션이 있으면 `open_position`의 코인 수량·진입가·`opened_at_kst`(KST `HH:MM`)를 한 줄에 강조합니다.
- 포맷 함수: `main_ai._format_cycle_dashboard` (루프 출력은 `run_forever` / `AI_RUN_ONCE` 경로).

## 카카오 OAuth (Stateless + Thread-safe)

| 항목 | 구현 |
|------|------|
| **토큰 정본** | `btc_live_trading/kakao_code.json` + `.env` + `os.environ` |
| **알림 모듈** | `KakaoNotifier` — 인스턴스에 토큰 **미보관**, 매 호출 `get_access_token()` |
| **중앙 갱신** | `refresh_kakao_access_token_sync()` — Double-checked locking |
| **401 처리** | 중앙 갱신 → 1회 재전송 |
| **환경 제한** | `kakao_api_allowed()` — `example1` + `/home/bot2/Coin`만 허용 |
| **Safety First** | Heartbeat 실패 시 해당 사이클 매매 차단 (`_kakao_auth_ready`) |
| **갱신 재시도** | refresh 실패 시 최대 3회 — 시도 사이 `kakao_code.json` 재동기화로 외부 재인증 토큰 즉시 사용 |
| **인증 대기 모드** | 만료 시 `KAKAO_AUTH_RETRY_MINUTES`(기본 30분)마다 자동 복구 재시도, 차단 ERROR 로그는 `KAKAO_BLOCKED_LOG_MINUTES`(기본 60분)당 1회 |
| **데드락 방지** | 갱신 성공 알림 리스너는 `_KAKAO_REFRESH_LOCK` **해제 후** 호출 — 알림 경로에서 같은 스레드가 재갱신에 진입해도 락에 걸리지 않음 |
| **디스크 강제 저장** | `persist_kakao_tokens` — `.env` + `kakao_code.json` 원자 쓰기 후 재읽기 검증. 불일치 시 `[ERROR] 토큰 파일 덮어쓰기 실패!` 및 갱신 실패 처리 |
| **refresh 생략 방어** | 응답에 `refresh_token` 필드가 없거나 빈 값이면 기존 refresh 유지(`rotate_refresh=False`). access만 새 값으로 저장 |
| **자동 로그인** | `KakaoNotifier.send_message` / `ensure_access_token_for_login` — 로컬 `expires_at` 만료일 때만 refresh (평시 HTTP 없음) |
| **워커 자가 복구** | 비동기 알림 워커 스레드가 비정상 종료돼도 다음 알림 시 자동 재기동 (`is_alive()` 실체크) |
| **생존 하트비트** | 인증 정상 사이클마다 DEBUG, `KAKAO_HEARTBEAT_LOG_MINUTES`(기본 60분)마다 INFO 하트비트 |

**토큰 정책 (카톡 자동로그인 스타일):**
1. 매매 사이클 게이트(`_kakao_auth_ready`)는 **디스크에 access+refresh가 있는지만** 확인 — HTTP validate/refresh 없음.
2. **알림을 보낼 때(로그인 시도)** 로컬 `expires_at`이 만료됐거나 401이면 `refresh_kakao_access_token_sync(force=True)`.
3. 갱신 HTTP 성공 후 `apply_token_response` → `persist_kakao_tokens`가 `.env`/`kakao_code.json`에 쓰고 **재읽기 100% 일치**를 확인. 저장 실패면 access를 성공으로 반환하지 않음(재시작 후 `invalid_grant` 예방).
4. 별도 “6시간 스케줄 갱신 스레드” 없음.

생존·실패 원인 확인:

```bash
journalctl -u coinbot.service --since "1 hour ago" --no-pager | grep -E "카카오 토큰 하트비트|자동 갱신 완료|토큰 파일 덮어쓰기|invalid_grant|알림 워커"
```

### 카카오 401 / 재인증

카카오 401 또는 `expired_or_invalid_refresh_token`이 발생하면 refresh-token까지 만료된 상태입니다.

**서버에서 재인증 (권장):**

```bash
bash ~/Coin/scripts/coinbot_watch.sh
# 또는
python ~/Coin/scripts/auth_kakao.py
```

1. 터미널에 카카오 로그인 URL이 출력됩니다.
2. 브라우저에서 로그인 후 리다이렉트 URL **전체** 또는 `code=` 값을 붙여넣습니다.
3. `✅ 저장 완료` 확인. **재시작은 선택** — 봇이 실행 중이면 인증 대기 모드가 최대 `KAKAO_AUTH_RETRY_MINUTES`(기본 30분) 안에 새 토큰을 자동으로 집어 씁니다. 즉시 반영하려면 `systemctl restart coinbot.service` (root).

`auth_kakao.py`는 전체 URL·순수 코드 모두 자동 파싱합니다. 토큰은 `btc_live_trading/.env`와 `kakao_code.json`에 원자적으로 저장됩니다.

KOE205가 발생하면 `KAKAO_REDIRECT_URI`와 카카오 개발자 콘솔 Redirect URI가 정확히 같은지 확인한 뒤 새 인가 코드를 다시 발급하세요.

## 실전(`AI_DRY_RUN=false`) 동작 요약

| 구분 | 구현 위치 |
|------|-----------|
| 리포트 꼭지·카카오 제목의 `[실전 매매 리포트]` / `[가상 매매 리포트]` | `main_ai._report_bracket_title`, 알림 제목·`_build_kakao_message` 첫 줄 |
| 실잔고(USDT-M 선물) | `main_ai._fetch_binance_futures_usdt_balance` → `reporting.resolve_report_balances` |
| 원화 표시용 환율 | `btc_live_trading.fx_rates.fetch_usdt_krw` (CoinGecko), `main_ai._krw_per_usdt` |
| 포지션 크기 산정용 잔고 | `run_cycle` 내 `live_wallet_usdt` / `account_balance_usdt` |

제거됨: OpenAI Organization Costs 조회(`main_ai`의 `_fetch_openai_api_usage` 등), `.env`의 `KRW_PER_USDT`·`OPENAI_MONTHLY_BUDGET_USD`.

## 리스크 관리 (강제 종료·복구·비상 청산)

### 우아한 종료 (Ctrl+C / SIGTERM)

- `main_ai.install_shutdown_handlers()`가 `SIGINT`·`SIGTERM`(지원되는 환경)에 `_shutdown_signal_handler`를 등록합니다. 무한 루프(`run_forever`) 시작 시와 `__main__` 진입 시 한 번씩 호출됩니다.
- 핸들러는 `_SHUTDOWN_LOCK`을 획득한 뒤 `_graceful_shutdown_work`를 실행합니다. `run_cycle` 본문(`_run_cycle_impl`)도 동일 락으로 감싸져 있어, **한 사이클이 끝난 뒤** 종료 정리가 실행되거나, **슬립 중**이면 즉시 락을 잡고 정리합니다.
- **실전(`AI_DRY_RUN=false`)**이고 `AI_SYMBOL`(기본 `BTCUSDT`)에 거래소 포지션이 있으면 `binance_futures_tools.market_close_symbol`로 시장가 `reduceOnly` 청산을 시도한 뒤, 성공 시 카카오로 *「프로그램 종료로 인해 포지션을 긴급 청산했습니다.」* 를 보냅니다. 실패 시 청산 실패 알림과 `scripts/emergency_exit.py` 안내를 보냅니다.
- **가상 매매**에서만 `open_position`이 있으면 거래소 호출 없이 원장에서 제거하고 동일 문구(가상 종료 안내)로 알립니다. 포지션이 없으면 로그만 남기고 종료합니다.
- `KeyboardInterrupt`는 `run_forever` / 단발 실행 경로에서 동일하게 `_graceful_shutdown_work`를 호출한 뒤 `SystemExit`로 빠져나갑니다. `threading.Event`로 중복 청산을 막습니다.

### 재시작 시 동기화

- 매 `run_cycle` 초반에 `_reconcile_ledger_with_exchange`가 실행됩니다. 거래소에만 포지션이 남아 있으면 `open_position`을 `_synthetic_open_position_from_exchange`로 복구하고, 최초 1회 카카오 *「미청산 포지션 발견」* 알림을 보냅니다. 원장만 있고 거래소에는 없으면 `open_position`을 제거합니다.

### 실전 진입·청산 주문 (`AI_DRY_RUN=false`)

- **원인(과거 동작)**: 리포트의 `dry_run: false`는 환경변수만 반영했고, `_open_position`은 항상 `_paper_trade_id()`로 `PAPER-…` ID만 만들며 **거래소 주문 API를 호출하지 않았습니다.**
- **현재**: `dry_run=false`이면 `binance_futures_tools.futures_market_open_position` → `Client.futures_create_order`(MARKET, `reduceOnly=False`)로 진입하고, `trade_id`는 응답의 **`orderId`** 문자열입니다. 가상 모드는 `order_mode: paper` + `PAPER-…` ID.
- 편의 함수 **`market_buy_symbol` / `market_sell_symbol_open_short`**는 같은 모듈에서 롱·숏 진입용으로 노출됩니다.
- 청산 시 `_close_position`은 실전에서 먼저 `market_close_symbol`(시장가·reduceOnly)을 호출한 뒤 원장을 갱신합니다. 거래소 청산 실패 시 카카오 *「실전 청산 실패 알림」* 후 원장·포지션은 유지됩니다.
- 기동 시 `_send_startup_report` 및 매 사이클 `_log_order_execution_setup` 로그로 **`AI_DRY_RUN` 원문·해석값·선물 주문 스택 준비 여부**를 확인할 수 있습니다.

### 비상 수동 청산 스크립트

- `scripts/emergency_exit.py`: `.env` 로드 후 **모든 심볼**의 미결 USDT-M 포지션을 `close_all_usdm_positions`로 시장가 청산합니다. 운영 PC에서 바로 실행할 수 있습니다.

```powershell
# 저장소 루트(Coin/)에서
py -3 scripts\emergency_exit.py
```

## 운영 가이드 (비용 최적화)

- AI 호출 게이트: `RSI<=35` 또는 `RSI>=65`, `BB<=0.20` 또는 `BB>=0.80`, `|EMA_GAP|>=0.03%` 중 하나라도 충족할 때만 진입 AI를 호출합니다.
- 중립 구간(`RSI/EMA/BB` 모두 평탄)에서는 AI를 호출하지 않고 즉시 `HOLD` 처리합니다.
- 모델 분기: 모니터링 요약은 `OPENAI_MONITOR_MODEL` 또는 `AI_MONITOR_MODEL`(권장 `gpt-4o-mini`), 진입 판단은 `OPENAI_ENTRY_MODEL` 또는 `AI_ENTRY_MODEL`(권장 `gpt-4o`)을 사용합니다.
- 프롬프트 경량화: 진입 판단 시 스냅샷 핵심 필드만 전달하고 유사 케이스는 상위 3개 핵심 컬럼만 전송합니다.
- 월 순수익(Net Profit) 계산식:
  - `월 순수익 = monthly_realized_pnl_krw - ((estimated_daily_server_cost_krw * 30) + monthly_subscription_cost_krw)`
  - `AI_DAILY_SERVER_COST_KRW`(또는 `AI_EST_DAILY_SERVER_COST_KRW`), `AI_MONTHLY_SUBSCRIPTION_COST_KRW` 값은 `trading_stats.json` 집계에 반영됩니다.

## 운영 데이터

- `ai_trading/data/trading_stats.json`, `virtual_trades.jsonl`, `ai_learning_logs.csv`, `history/` — **Git 제외**(서버·PC별 런타임 데이터).
- `virtual_trades.jsonl`: `AI_TRADE_LOG_MAX_LINES`(기본 2000) 초과 시 꼬리만 유지.
- `ai_learning_logs.csv`: `AI_LEARNING_LOG_MAX_ROWS`(기본 5000) 초과 시 `_prune_learning_log`로 트림.
- `history/trading_stats_month_{YYYY-MM}_archived.json`: 월 백업. `AI_STATS_HISTORY_MAX_FILES`(기본 6) 초과 시 자동 삭제.

운영 중 카카오 알림이 실패해도 위 로컬 데이터 파일은 계속 저장됩니다.

- **학습 CSV 부하 완화**: mtime 캐시 + append 시 110% 초과 트림.

## 변경·통합 이력 (실전 최적화)

| 구분 | 내용 |
|------|------|
| Binance 잔고 API | `main_ai`의 직접 `Client` 생성 제거 → `binance_futures_tools.fetch_futures_usdt_balance_from_env` 단일화 (`futures_wallet_usdt_balance`) |
| 학습 로그 | `_load_learning_rows` mtime 캐시 + `_prune_learning_log` 디스크 트림 + append 후 캐시 무효화 |
| 거래 로그 회전 | `virtual_trades.jsonl`은 `AI_TRADE_LOG_MAX_LINES`(기본 2000) 초과 시 오래된 라인 자동 삭제 |
| 단일 실행 | `.coinbot.lock` + `fcntl`/`msvcrt` |
| 환경 화이트리스트 | `kakao_api_allowed()` — `example1` + `/home/bot2/Coin`, 로컬·WSL 차단 |
| Stateless Kakao | `KakaoNotifier` — self 토큰 제거, 정본 실시간 조회 |
| Thread-safe Refresh | `refresh_kakao_access_token_sync()` Double-checked locking |
| 갱신 알림 데드락 방지 | 성공 리스너는 락 **해제 후** 호출 (`kakao_utils.refresh_kakao_access_token_sync`) |
| 토큰 디스크 정본 | `persist_kakao_tokens` 재읽기 검증 + `apply_token_response` 저장 실패 시 `""` 반환 |
| 자동 로그인 갱신 | 알림 전송 시 만료만 refresh (`ensure_access_token_for_login` / `expires_at`) |
| 알림 워커 자가 복구 | `AsyncNotifier.is_alive()` + 사망 시 자동 재기동 (`main_ai._ensure_async_kakao_started`) |
| 인증 대기·하트비트 | `KAKAO_AUTH_RETRY_MINUTES` / `KAKAO_BLOCKED_LOG_MINUTES` / `KAKAO_HEARTBEAT_LOG_MINUTES` |
| 일일 리포트 생존 | `_send_daily_status_report` try/except — 예외 시 ERROR 로그만, `last_report_at_kst`는 성공 시에만 갱신 |
| 루프 중복 방지 | `_CYCLE_LOCK`, `_wait_for_next_cycle_slot`, 캔들 단위 AI 1회 |
| 대화형 인증 | `scripts/auth_kakao.py` — URL/코드 스마트 파싱 |
| 레거시 제거 | `main_live`·스캘핑·`order_executor` 등 미사용 엔진 삭제 |

**실전 주문 경로**: `AI_DRY_RUN=false`일 때 진입 `futures_market_open_position`, 청산·종료 `market_close_symbol` / `close_all_usdm_positions`, 잔고 `fetch_futures_usdt_balance_from_env`. 가상 원장(`virtual_balance_*`)은 통계·손익 추적용으로 갱신되며, **체결은 위 API만 사용**합니다.
