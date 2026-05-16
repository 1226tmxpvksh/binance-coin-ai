# Coin — BTC 트레이딩 모노레포

실전 AI 매매는 **`ai_trading`** 엔진(`main_ai.py`)을 기준으로 운영합니다. 바이낸스 USDT-M 선물 키·카카오 알림·`.env`는 **`btc_live_trading`** 쪽과 공유합니다.

## 디렉터리 요약

| 경로 | 역할 |
|------|------|
| **`ai_trading/`** | 메인 루프, 리포트, 선물 주문·청산(`binance_futures_tools.py`), 학습 로그, `README.md` 상세 가이드 |
| **`btc_live_trading/`** | 공용 `.env`, 카카오·환율(`fx_rates.py`), 스캘핑/`main_live.py` 등 **별도 진입점**(선택) |
| **`btc_day_strategy/`** | 백테스트·전략 라이브러리 (`main_ai` 백테스트 요약 로드) |
| **`scripts/`** | `emergency_exit.py` 등 비상 스크립트 |

레거시 단타 엔진(`scalping_engine.py`, `main_live.py`)은 과거 용도로 남아 있으며, **실전 단일 매매**는 `AI_DRY_RUN=false`로 `py ai_trading\main_ai.py`만 쓰면 됩니다.

자세한 환경변수·데이터 파일·리스크 관리는 **`ai_trading/README.md`** 를 참고하세요.

## 운영 및 모니터링

`main_ai.py` 실행 시 매 루프마다 콘솔에 **구조화된 대시보드**가 출력됩니다(AI 판단, 시장 지표, 잔고, 보유 포지션). 예시와 상세 설명은 [`ai_trading/README.md`](ai_trading/README.md)의 **운영 및 모니터링** 섹션을 참고하세요.
