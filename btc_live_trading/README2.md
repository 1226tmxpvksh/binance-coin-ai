# BTC 변동성 돌파 실전 매매 프로그램

> **단일 문서**: 설치, 설정, 전략, 보안, 알림, 트러블슈팅을 한 곳에 정리

---

## ⚠️ 중요 경고

- **실제 돈이 움직이는 자동 매매 프로그램**입니다.
- 반드시 **DRY_RUN=True**로 최소 1주일 이상 테스트 후 실전 전환
- 백테스트 성과 ≠ 실전 성과 (슬리피지, 수수료 등)
- 손실 가능성을 인지하고 사용하세요

---

## 1. 프로그램 개요

### 1.1 전략

- **상승장** (EMA20 > EMA60): LONG만 진입
- **하락장** (close < SMA200, 기울기 < 0): SHORT만 진입
- **변동성 돌파**: ATR 기반 K값으로 진입가 계산
- **SHORT**: 익일 강제 청산
- **LONG**: 조건 충족 시 최대 3일 보유, 트레일링 스탑

### 1.2 특징

- 일봉 기반, 매일 9시(한국시간) 거래 체크
- 계좌 대비 1% 리스크
- 일일 최대 손실 10%, 긴급 정지 15%
- 카카오톡 알림 (비동기, 거래 지연 없음)
- **btc_day_strategy 없이 단독 실행** (strategy/ 폴더에 전략 포함)

### 1.3 폴더 구조

```
btc_live_trading/
├── strategy/                  # 전략 모듈 (독립)
│   ├── indicators.py
│   ├── market_regime.py
│   ├── strategy_entry.py
│   ├── strategy_exit.py
│   ├── risk_manager.py
│   └── data_loader.py
├── main_live.py               # 메인 실행
├── daily_order_scheduler.py   # 일일 LONG 전용 스케줄러
├── live_trading_engine.py
├── order_executor.py
├── position_manager.py
├── safety_manager.py
├── config_live.py             # 설정
├── .env                       # API 키 (git 제외)
└── README2.md
```

---

## 2. 시작하기

### 2.1 패키지 설치

```bash
cd btc_live_trading
pip install -r requirements.txt
# 또는
pip install python-binance python-dotenv requests pandas numpy
```

### 2.2 바이낸스 API

1. 바이낸스 → 계정 → API Management
2. 새 API 키 생성
3. **선물 거래**, **읽기** 권한 활성화, **출금 비활성화**
4. IP 화이트리스트 권장

### 2.3 환경 변수 (.env)

`.env.example`을 `.env`로 복사 후 입력:

```env
BINANCE_API_KEY=실제_키
BINANCE_API_SECRET=실제_시크릿
KAKAO_REST_API_KEY=카카오_REST_API_키   # 선택
KAKAO_ACCESS_TOKEN=                     # 비워두면 자동 발급 안내
```

### 2.4 config_live.py 주요 설정

```python
DRY_RUN = True              # 테스트 모드 (실제 주문 안 함)
RISK_PER_TRADE = 0.01       # 1% 리스크
LEVERAGE = 3                # 레버리지
DAILY_MAX_LOSS_PERCENT = 10.0
EMERGENCY_STOP_LOSS_PERCENT = 15.0
DAILY_REPORT_HOUR = 9       # 9시에 거래 + 일일 요약
```

### 2.5 실행

```bash
python main_live.py
```

실전: `DRY_RUN = False` 변경 후 실행, `YES` 입력 필요

---

## 3. 카카오톡 알림

### 3.1 기본 설정

1. https://developers.kakao.com/console/app 에서 앱 생성
2. REST API 키 복사 → `.env`의 `KAKAO_REST_API_KEY`
3. 카카오 로그인 ON, Redirect URI: `https://example.com/oauth`
4. **동의항목** → "카카오톡 메시지 전송" 활성화

### 3.2 토큰 만료 시

- 401 발생 시 콘솔에 재발급 URL 표시
- 브라우저에서 접속 → code 복사 → 프로그램에 입력
- 리프레시 토큰이 있으면 자동 갱신 (최대 2개월)
- 알림 끄기: `config_live.py`에서 `KAKAO_ENABLED = False`

---

## 4. 거래 스케줄

- **9시**: 데이터 로드, 지표 계산, 진입/청산 판단, 일일 요약
- **그 외**: 대기 (손절/익절은 바이낸스가 24시간 자동 처리)

### daily_order_scheduler

- **LONG 전용**: 상승장(BULL)에서만 주문
- 하락장(BEAR)에서는 주문 없음
- `main_live.py`는 LONG/SHORT 모두 지원

---

## 5. 주문 전략

| 구분 | 전략 |
|------|------|
| 진입 | LIMIT → 5초 대기 → 미체결 시 MARKET |
| 손절/익절 | 바이낸스 STOP_MARKET, TAKE_PROFIT_MARKET (24시간 자동) |
| 전략적 청산 | LIMIT → 60초 대기 → MARKET |
| 트레일링 스탑 | 기존 손절 취소 후 새 손절가 재등록 |

---

## 6. 안전장치

| 항목 | 설정 |
|------|------|
| 일일 최대 손실 | 10% |
| 긴급 정지 | 초기 자본 대비 15% |
| 최대 포지션 | 1개 |
| 일일 거래 | 최대 1회 |
| 최소 포지션 | $20 |

### 보안

- `.env`는 Git 제외, 로그에서 API 키 마스킹
- 실전 전환 시 `YES` 입력 (constant-time 비교)
- API 키: IP 화이트리스트, 출금 비활성화 권장

---

## 7. 일일 요약

- **9시**에 카카오톡으로 전날 거래 요약
- 총 거래, 승률, 일일 손익, 현재 잔고
- 전송 후 통계 초기화

---

## 8. 실전 유의사항

- **NaN 검증**: 지표 계산 미완료 시 거래 보류
- **슬리피지**: 시장가 주문 시 체결가 차이 가능
- **DRY_RUN**: 지정가 주문 50% 확률 체결/미체결 시뮬레이션
- **실제 연결**: .env에 키 있으면 바이낸스와 실시간 연결 (잔고/가격 조회 포함)

---

## 9. 트러블슈팅

| 증상 | 확인 |
|------|------|
| API 키 오류 | .env, 선물 권한, IP 화이트리스트 |
| 잔고 부족 | MIN_POSITION_SIZE_USDT ($20) |
| 알림 실패 | 동의항목, REST API 키, 거래는 영향 없음 |
| ModuleNotFoundError | `btc_live_trading`에서 실행, strategy/ 확인 |

---

## 10. FAQ

- **백테스트**: `btc_day_strategy` 폴더에서 별도 실행
- **전략 수정**: `btc_live_trading/strategy/` 수정
- **알림 없어도 거래**: 가능 (비동기 처리)
- **여러 코인**: 현재 BTCUSDT 단일

---

## 11. 면책

교육·연구 목적. 금융 손실 책임은 사용자에게 있습니다.

---

**행운을 빕니다**
