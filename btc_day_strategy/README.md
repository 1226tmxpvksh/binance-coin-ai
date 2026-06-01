# 🎯 BTC 데이 확장형 전략 백테스트

바이낸스 선물 시장에서 BTC 일봉 기반 데이 확장형 트레이딩 전략을 백테스트하는 프로그램입니다.

## 📂 프로젝트 구조

이 프로젝트는 두 개의 분리된 폴더로 구성됩니다:

- **btc_day_strategy/** - 백테스트 전용 (이 폴더)
- **btc_live_trading/** - 실전 매매 전용

> ⚠️ **이 폴더는 백테스트 전용입니다!**
> 
> 실전 매매는 `btc_live_trading` 폴더에서 수행하세요.
> 전략 모듈은 두 폴더가 공유합니다.

## 📋 전략 개요

### 기본 전략
- **대상 자산**: BTCUSDT 선물
- **타임프레임**: 일봉 (1D)
- **거래 방향**:
  - 상승장 (EMA20 > EMA60): LONG만
  - 하락장 (close < SMA200 AND 기울기 < 0): SHORT만
- **보유 기간**:
  - SHORT: 무조건 익일 청산
  - LONG: 조건 충족 시 최대 3일 연장
- **진입 방식**: 변동성 돌파 (ATR 기반 K값)

## ✨ 주요 기능

### 백테스트 기능
- **백테스트 엔진**: 과거 데이터로 전략 검증
- **성과 분석**: 수익률, 승률, 최대 낙폭, 샤프 비율 등
- **시각화**: 자본 곡선, 거래 분포, 월별 수익 등
- **전략 비교**: 원본 설정 vs 실전 설정 비교 (`compare_strategies.py`)

### 공유 모듈 (실전 매매와 공유)
- `indicators.py` - 지표 계산 (EMA, SMA, ATR 등)
- `market_regime.py` - 시장 상태 판단
- `strategy_entry.py` - 진입 전략
- `strategy_exit.py` - 청산 전략
- `risk_manager.py` - 리스크 관리
- `data_loader.py` - 데이터 로드

## 🏗️ 파일 구조

```
btc_day_strategy/              # 백테스트 전용 (이 폴더)
├── config.py                  # 원본 전략 설정 (RISK 2%)
├── config_live_settings.py    # 실전 설정 비교용 (RISK 1%)
├── compare_strategies.py      # 전략 비교 도구
├── run_comparison.bat         # 비교 실행 (Windows)
├── backtest_engine.py         # 백테스트 엔진
├── performance.py             # 성과 분석
├── visualization.py           # 그래프 생성
├── main.py                    # 원본 설정 백테스트
├── indicators.py              # 지표 계산 (공유)
├── market_regime.py           # 시장 상태 (공유)
├── strategy_entry.py          # 진입 전략 (공유)
├── strategy_exit.py           # 청산 전략 (공유)
├── risk_manager.py            # 리스크 관리 (공유)
├── data_loader.py             # 데이터 로드 (공유)
├── .env.example               # 환경 변수 예제
├── README.md                  # 이 문서
└── README_COMPARISON.md       # 비교 상세 가이드

../ai_trading/                 # 실전 AI 매매 엔진 (main_ai.py)
../btc_live_trading/           # 공용 .env·카카오·환율·전략 모듈
```

## 🚀 빠른 시작

### 1. 설치

```bash
cd btc_day_strategy
pip install python-binance pandas numpy matplotlib python-dotenv
```

### 2. API 키 설정

`.env.example`을 `.env`로 복사하고 바이낸스 API 키를 입력:

```bash
copy .env.example .env  # Windows
cp .env.example .env    # Mac/Linux
```

`.env` 파일 내용:
```env
API_KEY=your_actual_api_key
API_SECRET=your_actual_api_secret
```

### 3. 백테스트 실행

#### 원본 설정 백테스트

```bash
python main.py
```

#### 실전 설정 비교 (권장!)

```bash
python compare_strategies.py
```

또는 Windows:

```bash
run_comparison.bat
```

> ⚠️ **실전 매매를 시작하기 전에 반드시 `compare_strategies.py`로 실전 설정의 성과를 확인하세요!**

### 4. 결과 확인

백테스트 완료 후 다음 결과가 생성됩니다:

#### 원본 설정 백테스트
- **콘솔**: 성과 요약 출력
- **output/**: 그래프 저장
  - `01_equity_curve.png` - 자본 곡선
  - `02_yearly_returns.png` - 연도별 수익률
  - `03_drawdown.png` - 낙폭 곡선
  - `04_monthly_returns.png` - 월별 수익
  - `05_trade_distribution.png` - 거래 분포
- **backtest.log**: 상세 로그

#### 전략 비교
- **콘솔**: 비교 테이블 출력
- **output/original/**: 원본 전략 그래프
- **output/live_settings/**: 실전 설정 그래프
- **output/comparison/**: 비교 차트
- **strategy_comparison.log**: 비교 로그

## ⚙️ 설정 차이

### 원본 설정 (config.py)
```python
RISK_PER_TRADE = 0.02        # 2% (공격적)
LEVERAGE = 3
STOP_LOSS_MULTIPLIER = 1.0
TAKE_PROFIT_MULTIPLIER = 1.5
MAX_HOLD_DAYS = 3
# MIN_POSITION_SIZE_USDT 없음
```

### 실전 설정 (config_live_settings.py)
```python
RISK_PER_TRADE = 0.01        # 1% (보수적)
LEVERAGE = 3
STOP_LOSS_MULTIPLIER = 1.0
TAKE_PROFIT_MULTIPLIER = 1.5
MAX_HOLD_DAYS = 3
MIN_POSITION_SIZE_USDT = 20  # 바이낸스 최소
```

## 📊 주요 성과 지표

백테스트 결과에서 확인할 수 있는 지표:

- **총 수익률**: 전체 기간 수익률
- **연간 수익률**: 연평균 수익률
- **승률**: 수익 거래 비율
- **평균 수익/손실**: 거래당 평균 금액
- **최대 낙폭**: 최대 손실 폭
- **손익비 (Profit Factor)**: 총 수익 / 총 손실
- **샤프 비율**: 위험 대비 수익 효율

## 🔍 전략 상세

### 시장 상태 판단

1. **상승장 (BULL)**: EMA20 > EMA60 → LONG만 허용
2. **하락장 (BEAR)**: close < SMA200 AND SMA200 기울기 < 0 → SHORT만 허용
3. **중립 (NEUTRAL)**: 거래 안 함

### 진입 조건

#### LONG 진입
- 시장 상태: BULL
- 변동성 돌파: `high > open + (prev_high - prev_low) * k`
- 거래량 확인: `prev_volume > VOL_MA20`

#### SHORT 진입
- 시장 상태: BEAR
- 변동성 돌파: `low < open - (prev_high - prev_low) * k`
- 거래량 확인: `prev_volume > VOL_MA20`

### 청산 조건

#### 기본 청산 (진입 당일)
- 손절: `entry_price ± ATR * 1.0`
- 익절: `entry_price ± ATR * 1.5`

#### SHORT 청산
- 무조건 익일 청산 (확장 금지)

#### LONG 확장 보유
- 조건: 수익 상태 + EMA20 > EMA60
- 트레일링 스탑: `EMA20 - ATR * 0.5`
- 최대 3일까지 연장
- 청산 조건:
  - 가격이 트레일링 스탑 아래
  - EMA 정배열 깨짐
  - 거래량 급감
  - 3일 도달

### 리스크 관리

- 거래당 리스크: 계좌의 1% (실전) 또는 2% (원본)
- 포지션 사이즈 자동 계산
- 단일 포지션만 허용
- 하루 1회 진입 제한

## 📚 관련 문서

- `README_COMPARISON.md` - 전략 비교 상세 가이드
- `../ai_trading/README.md` - 실전 AI 매매 가이드

## ❓ 자주 묻는 질문

### Q1: 실전 매매는 어디서 하나요?
→ 저장소 루트에서 `py ai_trading\main_ai.py` 실행 (`ai_trading/README.md` 참고)

### Q2: 실전 설정으로 백테스트하려면?
→ `python compare_strategies.py` 실행

### Q3: 백테스트와 실전 전략이 같나요?
→ 네! 전략 모듈(indicators, strategy_entry, strategy_exit 등)을 공유합니다.

### Q4: config.py와 config_live_settings.py 차이는?
→ `config.py`: 원본 공격적 설정 (RISK 2%)
→ `config_live_settings.py`: 실전 보수적 설정 (RISK 1%)

### Q5: 백테스트 결과가 좋으면 바로 실전 가능한가요?
→ 실전 설정으로도 백테스트 후, DRY_RUN 모드로 1주일 이상 테스트 권장!

### Q6: 다른 코인도 가능한가요?
→ `config.py`에서 `SYMBOL = "ETHUSDT"` 등으로 변경 가능

### Q7: 백테스트 기간을 바꾸려면?
→ `config.py`에서 `START_DATE`와 `END_DATE` 수정

## ⚠️ 주의사항

**이 프로그램은 백테스트 전용입니다.**

- 과거 성과가 미래 수익을 보장하지 않습니다
- 슬리피지, 수수료 등은 단순화되어 있습니다
- 실전 적용 전 충분한 검증이 필요합니다
- 백테스트와 실전 결과는 차이가 날 수 있습니다

## 🔗 참고 자료

- [바이낸스 API 문서](https://binance-docs.github.io/apidocs/futures/en/)

## 📝 라이선스

개인 및 교육 목적으로만 사용하세요.

---

**행운을 빕니다! 📈**
