# BTC ATR EMA200 Live Trading

최종 선정 전략 `Candidate B - EMA200 + ATR Stop 2.0 + Pivot Trailing` 전용 실전 매매 프로그램입니다.

## 전략

- 대상: Binance Futures `BTCUSDT`
- 시간봉: `4h`
- 방향: Long Only
- 진입: 최신 완료 4H 종가가 `EMA200` 위이고, `previous close + ATR(14) * 1.0` 돌파
- 손절: 진입가 - `ATR(14) * 2.0`
- 추적손절: 새 `Pivot Low(5)` 확정 시 손절선 상향
- 목표가: 사용하지 않음

## 설치

```bash
uv pip install -r requirements.txt
```

또는 기존 pip 환경에서는 다음처럼 설치할 수 있습니다.

```bash
pip install -r requirements.txt
```

## 설정

```bash
copy .env.example .env
```

`.env`에 Binance API, Kakao token을 입력합니다.

기술 스펙 기준 설정은 `config/settings.yaml`을 사용할 수 있습니다.

```bash
copy config\settings.yaml.example config\settings.yaml
```

기본 모드는 `MODE=PAPER`이며 실제 주문이 나가지 않습니다. 기존 안전 플래그인 `DRY_RUN=true`도 함께 유지됩니다.

Telegram을 쓰려면 `.env`에 `TELEGRAM_ENABLED=true`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 입력합니다. 카카오 알림도 계속 지원합니다.

## 실행

```bash
python main.py
```

## 실전 전환

실전 전환 전 최소 30일 Paper Trading을 권장합니다.

`.env`:

```env
MODE=LIVE
DRY_RUN=false
LIVE_TRADING_CONFIRMED=YES
```

## 생성 파일

- `logs/system.log`
- `logs/strategy.log`
- `logs/trade.log`
- `logs/error.log`
- `state/position_state.json`
- `database/live_trading.sqlite3`

SQLite에는 `events`, `positions`, `orders`, `trades`, `signals`, `system_logs` 테이블이 생성됩니다.

## Docker 실행

```bash
docker compose up -d --build
```

## 안전장치

- 기본 레버리지 `1x`
- 최대 레버리지 `3x`
- 거래당 리스크 기본 `1%`
- 일일 최대 손실 `3%`
- 최대 연속 손실 `5회`
- 연속 손실 초과 시 `24시간` 쿨다운
- API 오류는 기본 `3회` 재시도
- OHLCV 데이터 오류나 비정상 캔들 간격 감지 시 거래 금지
- 실전 모드는 `LIVE_TRADING_CONFIRMED=YES` 없이는 실행 불가

