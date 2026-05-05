# AI Trading 운영 가이드

`ai_trading`은 Binance 시장 데이터, `btc_day_strategy` 백테스트 자료, OpenAI 판단 로직을 결합해 BTC 가상 매매를 운영하는 엔진입니다. 처음 실행하는 운영자가 바로 따라 할 수 있도록 설치, 환경변수 설정, 실행 방법만 정리합니다.

## 주요 기능

- 5분 주기 시장 감시와 단발 점검 실행을 지원합니다.
- `trading_stats.json`, `virtual_trades.jsonl`, `ai_learning_logs.csv`에 운영 상태와 학습 기록을 저장합니다.
- 카카오 액세스 토큰 만료 시 refresh-token으로 자동 갱신하고, 새 토큰을 `.env`와 `kakao_code.json`에 동기화합니다.
- refresh-token까지 만료되면 터미널에 인가 URL을 표시하고 새 인가 코드를 입력받아 즉시 세션을 복구합니다.
- 실행 시작 시 `btc_live_trading`, `btc_day_strategy` 핵심 모듈이 정상 로드되는지 표 형태로 폴더 연결성 체크를 수행합니다.
- OpenAI Usage/Costs 조회 권한이 없어도 매매 루프는 중단하지 않고 안내 상태만 남깁니다.

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
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

AI_SYMBOL=BTCUSDT
AI_TIMEFRAME=5m
AI_LOOP_SECONDS=300
AI_RUN_ONCE=false
AI_DRY_RUN=true

AI_LEVERAGE=3
AI_RISK_PER_TRADE=0.015
AI_MAX_RISK_RATIO=0.025
AI_ATR_STOP_MULTIPLIER=1.6
AI_ATR_TAKE_PROFIT_MULTIPLIER=3.2
AI_MIN_TAKE_PROFIT_RATIO=0.012
AI_PAPER_HOLD_MINUTES=30

KRW_PER_USDT=1380
OPENAI_MONTHLY_BUDGET_USD=10

KAKAO_REST_API_KEY=...
KAKAO_REDIRECT_URI=https://your-registered-redirect-uri
KAKAO_ACCESS_TOKEN=...
KAKAO_REFRESH_TOKEN=...
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

1. 터미널에 출력된 카카오 인가 URL을 브라우저에서 엽니다.
2. 카카오 로그인 및 동의를 완료합니다.
3. 리다이렉트된 URL에서 `code=` 뒤 값을 복사합니다.
4. 터미널의 `인가 코드(code):` 입력란에 새 인가 코드를 붙여넣습니다.
5. 새 refresh-token이 저장되면 이후 액세스 토큰 만료는 다시 자동 갱신됩니다.

KOE205가 발생하면 `KAKAO_REDIRECT_URI`와 카카오 개발자 콘솔 Redirect URI가 정확히 같은지 확인한 뒤 새 인가 코드를 다시 발급하세요.

## 운영 데이터

- `ai_trading\data\trading_stats.json`: 가상 잔고, 누적 손익, 월간 집계, 오픈 포지션 상태
- `ai_trading\data\virtual_trades.jsonl`: 가상 진입/청산 이벤트
- `ai_trading\data\ai_learning_logs.csv`: 손실 거래 사후분석과 재발 방지 메모
- `ai_trading\data\ai_decisions.log`: AI 원판단과 최종 판단 감사 로그

운영 중 카카오 알림이나 OpenAI Usage 조회가 실패해도 위 로컬 데이터 파일은 계속 저장됩니다.
