# AI Trading (AN 운영 가이드)

## 시스템 개요

`ai_trading`은 다음 3가지를 결합한 하이브리드 AI 단타 판단 시스템입니다.

- 바이낸스 선물 실시간 데이터(가격/RSI/볼린저/EMA/ATR)
- `btc_day_strategy` 기반 백테스트 인사이트(유사 구간 성과 참고)
- OpenAI `gpt-4o` 의사결정(JSON 강제 응답)

핵심 목표는 **AI 판단 + 리스크 제한 + 운영 리포팅 자동화**입니다.

## 핵심 기능

- **AI Decision**
  - JSON 형식: `{"decision":"BUY|SELL|HOLD","reason":"...","confidence":0.0~1.0}`
  - 백테스트 컨텍스트 + 현재 지표를 함께 사용해 판단
- **Risk Guard**
  - ATR 기반 손절가 계산(기본 배수 1.8, 운영 권장 1.5~2.0)
  - 손절/진입 간 최소 이격 0.5% 강제 보정
  - 계좌 리스크 1% 초과 시 진입 차단 + 경고 알림
- **Reporting**
  - 월 목표 10만 원(구독료 자급자족) 달성률 추적
  - 카카오톡 리포트: 결정/사유/리스크/월 진행률/D-day/오늘 판단 통계
- **Data Persistence**
  - `trading_stats.json` 누적 수익 저장
  - 매월 1일 09:00(KST) 이후 첫 실행 시 월간 리셋 + `history/` 백업
  - `ai_decisions.log`를 `|` 구분자 표 형식으로 누적 저장

## 파일별 구성

- `main_ai.py`
  - 전체 오케스트레이션(데이터 수집 → AI 판단 → 리스크 검증 → 리포트/알림)
  - `btc_live_trading` 모듈 import를 위한 `sys.path` 자동 보정
  - 카카오 알림 본문 생성 및 전송
- `ai_logic/decision_engine.py`
  - GPT 호출 및 JSON 파싱
  - `.env` 로드 보조 및 `OPENAI_API_KEY` 누락 시 원인 로그 출력
- `data/market_data.py`
  - 바이낸스 캔들 조회 및 RSI/EMA/BB/ATR 계산
- `data/backtest_data.py`
  - 백테스트 결과 파일 탐색 및 인사이트 요약
  - 유사 구간 추출(현재 지표와 과거 사례 거리 기반)
- `risk_guard.py`
  - 리스크 비율 계산 및 차단 판단
  - 최소 손절 이격(0.5%) 보정 유틸리티
- `reporting.py`
  - 월 목표 달성률 계산
  - 월간 리셋/백업 및 잔여 일수(D-day) 계산
- `data/trading_stats.json`
  - 월간 누적 수익/실행 횟수 저장
- `data/ai_decisions.log`
  - AI 원판단(`RAW`) + 최종판단(`FINAL`) 누적 로그

## 설치 및 실행

## 1) 필수 환경 변수 (`.env`)

루트 또는 `ai_trading/.env`에 최소 아래 값을 설정하세요.

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

AI_SYMBOL=BTCUSDT
AI_TIMEFRAME=5m
AI_ACCOUNT_BALANCE_USDT=1000
AI_LEVERAGE=3
AI_RISK_PER_TRADE=0.01
AI_ATR_STOP_MULTIPLIER=1.8
AI_DRY_RUN=true

KRW_PER_USDT=1350
AI_MONTHLY_PROFIT_KRW=0

KAKAO_REST_API_KEY=...
KAKAO_ACCESS_TOKEN=...
```

## 2) 실행

```powershell
Set-Location "c:\Users\1226t\Desktop\Coin"
py -3 ai_trading\main_ai.py
```

## 운영 주의사항

- 실거래 전에는 반드시 `AI_DRY_RUN=true`로 충분히 검증하세요.
- OpenAI 키가 없으면 시스템은 안전하게 `HOLD`로 처리됩니다.
- 리스크 차단 로직(1%)은 강제 안전장치입니다. 임의 완화 시 손실 변동성이 크게 증가할 수 있습니다.
- 카카오 알림이 실패할 수 있으므로, `ai_decisions.log`와 `trading_stats.json`을 함께 모니터링하세요.
