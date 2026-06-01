# Coin — BTC 트레이딩 모노레포

실전 AI 매매는 **`ai_trading`** 엔진(`main_ai.py`)을 기준으로 운영합니다. 바이낸스 USDT-M 선물 키·카카오 알림·`.env`는 **`btc_live_trading`** 쪽과 공유합니다.

## 디렉터리 요약

| 경로 | 역할 |
|------|------|
| **`ai_trading/`** | 메인 루프, 리포트, 선물 주문·청산(`binance_futures_tools.py`), 학습 로그, `README.md` 상세 가이드 |
| **`btc_live_trading/`** | 공용 `.env`, 카카오 토큰(`kakao_utils.py`/`kakao_notifier.py`), 환율(`fx_rates.py`), 공용 전략 모듈(`strategy/`) |
| **`btc_day_strategy/`** | 백테스트·전략 라이브러리 (`main_ai` 시작 시 연결 점검) |
| **`scripts/`** | `auth_kakao.py`(카카오 토큰 발급), `reset_live_ledger.py`(원장 리셋), `emergency_exit.py`(비상 청산) |

**실전 매매**는 `AI_DRY_RUN=false`로 `py ai_trading\main_ai.py` 하나만 실행하면 됩니다. (레거시 단타·`main_live` 엔진은 제거됨)

자세한 환경변수·데이터 파일·리스크 관리는 **`ai_trading/README.md`** 를 참고하세요.

## 운영 및 모니터링

`main_ai.py` 실행 시 매 루프마다 콘솔에 **구조화된 대시보드**가 출력됩니다(AI 판단, 시장 지표, 잔고, 보유 포지션). 예시와 상세 설명은 [`ai_trading/README.md`](ai_trading/README.md)의 **운영 및 모니터링** 섹션을 참고하세요.

## 전략 요약 및 아키텍처 (15m 추세 매매)

| 단계 | 구현 | 설명 |
|------|------|------|
| 시장 데이터 | `ai_trading/data/market_data.py` | Binance 15m klines → RSI/EMA/BB/ATR + **7일(672봉) 평균 거래량 대비 현재 거래량** |
| 거래량 필터 | `main_ai._apply_volume_entry_filter`, `decision_engine` | `volume_ratio ≥ AI_VOLUME_MIN_RATIO`(기본 1.5)일 때만 진입 허용; AI 프롬프트에 `volume_surge` 전달 |
| 변동성 필터 | `main_ai._apply_atr_entry_filter` | `atr_pct`가 `AI_ATR_MIN_ENTRY_PCT` 미만이면 횡보장으로 HOLD |
| 주말 차단 | `main_ai._is_weekend_trading_blocked` | `AI_TRADE_ON_WEEKENDS=false` 시 토·일 신규 진입·AI 분석 생략, 보유 포지션 청산만 |
| AI 판단 | `ai_trading/ai_logic/decision_engine.py` | OpenAI 진입/모니터링 (거래량 동반 돌파 우선) |
| 주문·청산 | `ai_trading/binance_futures_tools.py` | Binance USDT-M 선물 API |
| 원장·리포트 | `ai_trading/reporting.py` | `trading_stats.json`; **원금 복구 중**이면 월 실현손익 착시 방지 표기 |

### 원금 복구 우선 리포팅

`현재 잔고(총 평가금) < initial_balance_krw`(기본 500,000원)이면 카카오·콘솔에서 **「원금 복구 중」** 상태를 표시하고, 월 실현손익·순수익은 **실질 0원**으로 보여 줍니다. 잔고가 원금을 회복한 뒤부터만 양수 월손익을 표기합니다. (`reporting.build_monthly_pnl_display`)

### 주요 환경변수 (`.env`)

```env
AI_TIMEFRAME=15m
AI_VOLUME_LOOKBACK_DAYS=7
AI_VOLUME_MIN_RATIO=1.5          # 7일 평균 대비 150% 이상 = 거래량 급증
AI_VOLUME_GATE_ENABLED=true      # 로컬 거래량 게이트
AI_TRADE_ON_WEEKENDS=false
```

데이터 흐름: **15m 스냅샷 → ATR·거래량 게이트 → AI(거래량 확인) → 리스크·주문 → 원금 기준 리포트**
