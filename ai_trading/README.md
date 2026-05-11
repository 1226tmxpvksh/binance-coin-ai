# AI Trading 운영 가이드

`ai_trading`은 Binance 시장 데이터, `btc_day_strategy` 백테스트 자료, OpenAI 판단 로직을 결합해 BTC **가상·실전** 매매를 운영하는 엔진입니다. `AI_DRY_RUN`에 따라 리포트 문구·잔고 출처가 자동 전환됩니다. 처음 실행하는 운영자가 바로 따라 할 수 있도록 설치, 환경변수 설정, 실행 방법만 정리합니다.

## 주요 기능

- 5분 주기 시장 감시와 단발 점검 실행을 지원합니다.
- RSI/EMA/Bollinger 로컬 게이트를 먼저 통과한 경우에만 진입용 AI 판단을 호출해 OpenAI 토큰 사용량을 줄입니다.
- `trading_stats.json`, `virtual_trades.jsonl`, `ai_learning_logs.csv`에 운영 상태와 학습 기록을 저장합니다.
- 카카오 액세스 토큰 만료 시 refresh-token으로 자동 갱신하고, 새 토큰을 `.env`와 `kakao_code.json`에 동기화합니다.
- refresh-token까지 만료되면 터미널에 인가 URL을 표시하고 새 인가 코드를 입력받아 즉시 세션을 복구합니다.
- 실행 시작 시 `btc_live_trading`, `btc_day_strategy` 핵심 모듈이 정상 로드되는지 표 형태로 폴더 연결성 체크를 수행합니다.
- **USDT→KRW**는 CoinGecko `tether` 대비 `krw` 시세를 주기적으로 조회합니다(`btc_live_trading/fx_rates.py`). 수동 `KRW_PER_USDT` 설정은 제거되었습니다.
- **실전(`AI_DRY_RUN=false`)**일 때 카카오 리포트의 잔고·수익률 줄은 Binance **USDT-M 선물 지갑** `futures_account_balance`의 USDT를 사용합니다. 조회 실패 시에만 원장(`virtual_balance_*`)으로 표시합니다.
- 카카오 제목·본문 첫머리는 `[실전 매매 리포트]` / `[가상 매매 리포트]`로 구분됩니다.

## 설치

프로젝트 루트로 이동합니다.

```powershell
Set-Location "c:\Users\1226t\Desktop\Coin"
```

필요한 Python 패키지를 설치합니다.

```powershell
py -3 -m pip install -r btc_live_trading\requirements.txt
py -3 -m pip install -r btc_day_strategy\requirements.txt
```

OpenAI 관련 패키지가 별도로 필요하면 현재 환경에 설치합니다.

```powershell
py -3 -m pip install openai requests
```

## 환경변수 설정

운영 환경변수는 기본적으로 `btc_live_trading\.env`를 사용합니다. 파일이 없다면 같은 경로에 새로 만듭니다.

```env
# ==========================================
# 4. 시스템 운영 설정
# ==========================================

# 9번 라인 모델명 (따옴표, 공백, 주석 절대 금지)
OPENAI_MODEL=gpt-4o
# 거래 모드 (scalping = 단타)
TRADING_MODE=scalping
# 실전 매매 승인 (YES로 설정해야 작동)
SCALPING_LIVE_CONFIRMED=YES
# AI 판단 사용 여부
USE_AI_CONFIRM=true
# 테스트 모드 (true면 가상 매매)
AI_DRY_RUN=true
# 손절폭 배수 (1.5~2.0 권장)
AI_ATR_STOP_MULTIPLIER=1.2
# 월 목표 수익금 (원 단위)
MONTHLY_TARGET_KRW=100000

# ==========================================
# 5. 가상 자산 및 루프 엔진 설정
# ==========================================

# 초기 가상 자산 설정 (50만 원)
AI_VIRTUAL_INITIAL_KRW=500000
# 초기 가상 달러 설정 (약 362 USDT)
AI_VIRTUAL_INITIAL_USDT=362

# 루프 주기 (300초 = 5분마다 시장 감시)
AI_LOOP_SECONDS=300
# 가상 포지션 최대 보유 시간 (15분 후 자동 청산)
AI_PAPER_HOLD_MINUTES=15
# 카카오톡 상태 보고 주기 (60분마다 현재 수익률 보고)
AI_STATUS_REPORT_MINUTES=60
# 1회 실행 후 종료 여부 (무한 루프를 위해 false 설정)
AI_RUN_ONCE=false

# ==========================================
# 6. 운영 비용 및 수익 최적화 설정
# ==========================================

# 예상 일일 서버 비용 (원 단위, 예: 500원)
AI_EST_DAILY_SERVER_COST_KRW=500

# 월간 총 구독료 (커서 $20 + 넷플릭스 + 제미나이 등 합산 원화)
AI_MONTHLY_SUBSCRIPTION_COST_KRW=80000

# 모델 분기 운영 (기본 mini 사용, 진입 시에만 gpt-4o 호출 권장)
AI_MONITOR_MODEL=gpt-4o-mini
AI_ENTRY_MODEL=gpt-4o
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
Set-Location "c:\Users\1226t\Desktop\Coin"
py -3 ai_trading\main_ai.py
```

단발 점검 실행:

```powershell
Set-Location "c:\Users\1226t\Desktop\Coin"
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

## 카카오 401 처리

카카오 401 또는 `expired_or_invalid_refresh_token`이 발생하면 refresh-token까지 만료된 상태입니다. 이는 코드 문제가 아니라 카카오 OAuth 보안 정책에 따른 정상 만료 상황입니다.

이 경우 프로그램은 종료하지 않고 터미널에서 수동 복구 모드로 전환합니다.

1. 터미널에 출력된 카카오 인가 URL을 브라우저에서 엽니다.
2. 카카오 로그인 및 동의를 완료합니다.
3. 리다이렉트된 URL에서 `code=` 뒤 값을 복사합니다.
4. 터미널의 `인가 코드(code):` 입력란에 새 인가 코드를 붙여넣습니다.
5. 새 refresh-token이 저장되면 이후 액세스 토큰 만료는 다시 자동 갱신됩니다.

새 토큰은 `btc_live_trading\.env`와 `btc_live_trading\kakao_code.json`에 함께 저장됩니다. 인가 코드를 입력하지 않고 Enter를 누르면 카카오 알림만 건너뛰고 매매 루프는 계속 진행됩니다.

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

### 비상 수동 청산 스크립트

- `scripts/emergency_exit.py`: `.env` 로드 후 **모든 심볼**의 미결 USDT-M 포지션을 `close_all_usdm_positions`로 시장가 청산합니다. 운영 PC에서 바로 실행할 수 있습니다.

```powershell
Set-Location "c:\Users\1226t\Desktop\Coin"
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

- `ai_trading\data\trading_stats.json`: 원장(가상 잔고·실현 손익 집계 등), 월간 집계, 오픈 포지션 상태. 실전 리포트 표시 잔고는 Binance API가 우선입니다.
- `ai_trading\data\virtual_trades.jsonl`: 진입/청산 이벤트(가상·실전 공통 로그 형식)
- `ai_trading\data\ai_learning_logs.csv`: 손실 거래 사후분석과 재발 방지 메모
- `ai_trading\data\ai_decisions.log`: AI 원판단과 최종 판단 감사 로그

운영 중 카카오 알림이 실패해도 위 로컬 데이터 파일은 계속 저장됩니다.
