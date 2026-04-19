# AI Trading (AN 운영 가이드)

## 시스템 개요

`ai_trading`은 백테스트 인사이트, 실시간 시장 지표, OpenAI 의사결정을 결합한 **자가 학습형 가상 매매 루프 엔진**입니다.  
운영 관점의 핵심 목표는 단순한 신호 생성이 아니라, 다음 3가지를 동시에 만족하는 것입니다.

- **신뢰성**: 단일 실행 실패나 알림 오류가 있어도 원장과 학습 로그는 파일로 영속 보존
- **효율성**: 유사한 실패를 중복 저장하지 않고, 의미 있는 오답 노트만 누적
- **운영 편의성**: 무한 루프 감시, 단발 실행 모드, 카카오 리포트, 월간 백업을 모두 지원

현재 엔진은 아래 요소를 결합해 동작합니다.

- 바이낸스 선물 실시간 데이터(가격/RSI/볼린저 밴드/EMA/ATR)
- `btc_day_strategy` 기반 유사 구간 백테스트 인사이트
- OpenAI `gpt-4o` 기반 JSON 의사결정
- 가상 원장(`trading_stats.json`) 기반 포지션 및 잔고 상태 관리
- 실패 사후분석과 재발 방지용 학습 로그(`ai_learning_logs.csv`)

## 핵심 운영 기능

### 지능형 루프 엔진

- 기본적으로 **5분 주기**로 시장을 감시하며, `AI_LOOP_SECONDS`로 조정할 수 있습니다.
- 가상 시드는 기본 **50만 원**이며, 내부적으로 KRW/USDT 기준 잔고를 함께 유지합니다.
- **단일 포지션 정책**을 사용합니다. 이미 포지션이 열려 있으면 추가 진입하지 않습니다.
- 포지션은 기본적으로 **15분 후 시간 청산**하며, `AI_PAPER_HOLD_MINUTES`로 조정 가능합니다.
- `AI_RUN_ONCE=true`면 1회 사이클만 실행하고 종료하므로 배치/점검/장애 분석에 유용합니다.

### 자가 피드백 학습 시스템

- 손실 청산이 발생하면 AI가 **사후 분석(Post-Mortem)** 을 수행합니다.
- 분석 결과는 `ai_learning_logs.csv`에 다음과 같은 형태로 저장됩니다.
  - 당시 시장 특징
  - 실패 원인
  - 운영자가 바로 읽을 수 있는 반성 한 줄
  - 다음 판단에서 주의해야 할 경고 문구
- 저장 전에는 `SequenceMatcher`로 기존 실패 사례와 비교하여 **유사도 80% 이상이면 기록하지 않습니다.**
- 이 덕분에 로그가 잡음으로 비대해지지 않고, **고효율 오답 노트**처럼 유지됩니다.
- 다음 판단 시 현재 시장과 가장 유사한 실패 사례를 찾아 프롬프트에 주입해, 과거 실수를 재현하지 않도록 유도합니다.

### 리스크 및 운영 안전장치

- AI는 `BUY|SELL|HOLD`와 사유, confidence를 JSON으로 반환합니다.
- ATR 기반 손절가 계산을 사용하며 기본 배수는 `1.8`입니다.
- 손절/진입 간 최소 이격 `0.5%`를 강제 보정합니다.
- 계좌 리스크가 `1%`를 초과하면 진입을 차단하고, 필요 시 카카오 경고를 보냅니다.
- OpenAI 키가 없거나 모델 응답이 비정상이면 시스템은 안전하게 `HOLD`로 폴백합니다.

### 운영 대시보드 및 리포팅

- 카카오톡 리포트에는 다음 정보가 포함됩니다.
  - 현재 잔고 / 누적 수익률
  - AI 학습 상태(누적 유니크 실패 사례 수)
  - 최근 반성 한 줄
  - 오늘 판단 횟수 / 진입 시도 횟수
  - 현재 보유 포지션 또는 최근 청산 결과
- 카카오 알림이 일시적으로 실패하더라도 핵심 상태는 로컬 파일에 계속 저장됩니다.
- `trading_stats.json`은 단순 수익 카운터가 아니라 **가상 원장 + 월간 집계 + 오픈 포지션 상태**를 담는 운영 기준 데이터입니다.

## 주요 데이터 파일

모든 운영 데이터는 `ai_trading/data/` 아래에 저장됩니다.

- `trading_stats.json`
  - 가상 잔고(KRW/USDT), 누적 손익, 승/패 수, 월간 집계, 최근 반성, 현재 오픈 포지션 상태 저장
- `virtual_trades.jsonl`
  - 가상 진입/청산 이벤트를 append-only 형태로 누적 저장
- `ai_learning_logs.csv`
  - 손실 거래 사후분석 로그
  - 중복 제거 후 남은 유의미한 실패 사례만 저장
- `ai_decisions.log`
  - AI 원판단(`RAW`)과 최종판단(`FINAL`)을 감사 로그 형식으로 저장
- `history/trading_stats_YYYY-MM.json`
  - 월 전환 시 자동 백업되는 이전 기간 원장 스냅샷

## 파일별 역할

- `main_ai.py`
  - 전체 오케스트레이션
  - `run_cycle()` 1회 실행과 `run_forever()` 무한 루프 실행 지원
  - 가상 포지션 진입/청산, 학습 로그 저장, 카카오 리포트 전송 담당
- `ai_logic/decision_engine.py`
  - OpenAI 호출 및 JSON 파싱
  - 실패 사례 메모리 주입
  - 손실 거래에 대한 사후 분석 수행
- `reporting.py`
  - `trading_stats.json`의 기본 스키마/정규화/저장
  - 월간 리셋 및 백업
  - 운영용 잔고/성과 요약 계산
- `data/market_data.py`
  - 바이낸스 캔들 조회
  - RSI/EMA/볼린저 밴드/ATR 계산
- `data/backtest_data.py`
  - 백테스트 데이터 탐색 및 요약
  - 현재 시장과 유사한 과거 사례 추출
- `risk_guard.py`
  - 리스크 비율 계산 및 진입 차단
  - 최소 손절 이격 보정

## 환경 변수 가이드

운영 환경에서는 일반적으로 `btc_live_trading/.env`를 기준으로 사용합니다.

### 필수

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

AI_SYMBOL=BTCUSDT
AI_TIMEFRAME=5m
AI_LEVERAGE=3
AI_RISK_PER_TRADE=0.01
AI_ATR_STOP_MULTIPLIER=1.8
AI_DRY_RUN=true

KRW_PER_USDT=1380

KAKAO_REST_API_KEY=...
KAKAO_REDIRECT_URI=https://example.com/oauth
KAKAO_ACCESS_TOKEN=...
KAKAO_REFRESH_TOKEN=...
```

### 루프 및 가상 원장 관련

- `AI_LOOP_SECONDS`
  - 메인 루프 주기(초)
  - 기본값: `300`
- `AI_RUN_ONCE`
  - `true`면 1회 실행 후 종료, `false`면 무한 루프 실행
  - 기본값: `false`
- `AI_PAPER_HOLD_MINUTES`
  - 가상 포지션 시간 청산 기준(분)
  - 기본값: `15`
- `AI_STATUS_REPORT_MINUTES`
  - 거래가 없더라도 주기 보고를 보낼 간격(분)
  - 기본값: `60`
- `AI_VIRTUAL_INITIAL_KRW`
  - 가상 원장의 초기 KRW 시드
  - 기본값: `500000`
- `AI_VIRTUAL_INITIAL_USDT`
  - 가상 원장의 초기 USDT 시드
  - 기본값: `AI_VIRTUAL_INITIAL_KRW / KRW_PER_USDT`

### 전략/리스크 관련

- `AI_SYMBOL`
  - 감시 심볼
- `AI_TIMEFRAME`
  - 실시간 스냅샷 기준 캔들 주기
- `AI_LEVERAGE`
  - 가상 포지션 레버리지 계산 기준
- `AI_RISK_PER_TRADE`
  - 1회 진입당 허용 리스크 비율
- `AI_ATR_STOP_MULTIPLIER`
  - ATR 기반 손절 배수
- `AI_DRY_RUN`
  - 항상 `true`로 운영하는 가상매매 모드 플래그

## 실행 방법

### 무한 루프 운영

```powershell
Set-Location "c:\Users\1226t\Desktop\Coin"
py -3 ai_trading\main_ai.py
```

### 단발 점검 실행

```powershell
Set-Location "c:\Users\1226t\Desktop\Coin"
$env:AI_RUN_ONCE="true"
py -3 ai_trading\main_ai.py
```

## 운영 신뢰성 포인트

- 원장(`trading_stats.json`)이 중심 상태 저장소 역할을 하므로, 프로세스 재시작 후에도 잔고와 오픈 포지션 정보를 이어받습니다.
- 실패 사례는 중복 제거 후 저장되므로, 장기 운영 시에도 학습 로그 품질이 유지됩니다.
- 카카오 리포트는 운영 편의 기능이고, 실제 기준 데이터는 항상 로컬 파일에 남습니다.
- 월간 전환 시 자동 백업이 수행되므로 운영 이력 추적과 장애 복구에 유리합니다.
- `AI_RUN_ONCE` 모드는 배포 직후 점검, 프롬프트 검증, 장애 재현, 스케줄러 연동 테스트에 특히 유용합니다.

## 운영 권장사항

- 실제 자금 투입 전에는 충분히 장기간 `AI_DRY_RUN=true` 상태로 검증하세요.
- 카카오 알림 성공 여부와 별개로 `trading_stats.json`, `virtual_trades.jsonl`, `ai_learning_logs.csv`를 함께 모니터링하세요.
- `KRW_PER_USDT`와 초기 시드 값은 운영 기준 자산과 맞게 명시적으로 고정하는 편이 좋습니다.
- 무한 루프 운영 시에는 별도 프로세스 관리자나 작업 스케줄러와 함께 사용하는 것을 권장합니다.
