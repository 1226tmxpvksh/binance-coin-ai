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

## 카카오톡 토큰 관리 및 트러블슈팅

카카오 액세스 토큰은 발급 후 약 **6시간**이면 만료되므로, 봇은 `refresh_token`으로 자동 갱신합니다. 갱신 로직과 저장소는 모두 `btc_live_trading/` 안에 있습니다.

### 자동 갱신 동작 방식

- **토큰 저장 위치**: `btc_live_trading/kakao_code.json`(access/refresh) + `btc_live_trading/.env`. 두 곳이 항상 동기화됩니다.
- **절대경로 처리**: `kakao_utils.py`가 실행 위치(CWD)와 무관하게 모듈 기준 절대경로(`os.path.abspath`)로 파일을 읽고 씁니다. systemd `WorkingDirectory`가 달라도 다른 파일을 건드리지 않습니다.
- **원자적 저장**: 토큰 파일은 임시 파일에 먼저 쓴 뒤 `os.replace`로 교체합니다. 저장 중 프로세스가 죽거나 재시작돼도 `kakao_code.json`이 깨지지 않습니다. (이전에는 쓰기 도중 중단 시 파일이 손상돼 다음 갱신이 영구 실패했음)
- **refresh_token(마스터 열쇠) 회전 처리 — 응답 유무에 따른 완전 분기**: 카카오는 토큰 갱신 시 보안상 새 `refresh_token`을 함께 발급(회전)하기도 합니다. `apply_token_response`/`persist_kakao_tokens`가 응답의 `refresh_token` 필드를 다음과 같이 엄격히 구분 처리합니다.
  - **새 `refresh_token`이 있고 비어있지 않으면** → 회전으로 간주하고 **무조건 새 값으로 기존 값을 대체** 저장(`.env`+`kakao_code.json`+메모리 3곳).
  - **필드가 없거나 빈 문자열이면** → 회전 없음으로 간주하고 **기존 유효 `refresh_token`을 절대 유실하지 않고 유지**. (이전 버그: 빈 문자열을 "회전됨"으로 오인해 기존 마스터 열쇠를 지우던 구멍을 막음)
- **저장 검증(write-after-read)**: 저장 직후 `kakao_code.json`을 다시 읽어 의도한 `refresh_token`이 실제로 기록됐는지 검증하고 성공/실패를 로그로 남깁니다. 검증 실패 시 즉시 `ERROR`로 경고합니다.
- **회전 빈도 최소화**: 매 알림마다 토큰을 갱신하면 회전이 과도하게 일어나 위험하므로, **유효한 액세스 토큰이 있으면 그대로 사용**하고 실제로 만료된 6시간 주기에만 갱신/회전이 일어나도록 했습니다(`_ensure_kakao_access_token` 검증 우선).
- **봇 방어막**: 카카오 **발송 실패**(토큰은 유효하나 일시적 네트워크/타임아웃 등)는 `logger.error`로만 남기고 매매 루프는 멈추지 않습니다.
- **인증 실패 시 매매 차단**: 반대로 유효한 카카오 **액세스 토큰 자체를 확보하지 못하면**(인증 실패/만료) 해당 사이클의 **매매 로직을 실행하지 않습니다**. 알림 없이 깜깜이 매매가 되는 것을 막기 위한 안전장치입니다. 재인증 후 서비스를 재시작하면 자동으로 매매가 재개됩니다. (`_kakao_auth_ready()`) 알림을 의도적으로 끄려면 `KAKAO_ALERTS_ENABLED=false`로 두면 이 차단도 우회되어 매매만 진행됩니다.

### 토큰 갱신 로그 확인 (Vultr / systemd)

```bash
# 실시간 카카오 토큰/알림 관련 로그만 필터링
journalctl -u coinbot.service -f | grep -iE "kakao|토큰|refresh|갱신"
```

정상 동작 시 6시간 주기로 아래와 같은 로그가 보입니다.

- `카카오 액세스 토큰 자동 갱신 완료`
- `kakao_code.json 갱신 완료: /.../kakao_code.json`
- `✅ refresh_token 저장 검증 통과: ...(kakao_code.json)` ← 마스터 열쇠가 파일에 확실히 기록됨
- (실제 회전 시) `🔑 카카오 마스터 열쇠 회전 감지: <기존>… → <신규>… (새 refresh_token 저장)`

반대로 아래 로그가 보이면 즉시 재인증이 필요합니다.

- `❌ refresh_token 저장 검증 실패!` ← 파일 기록이 어긋남(권한/디스크 점검)
- `카카오 리프레시 토큰 만료/무효: 새 인가 코드 발급이 필요합니다.`

### 알림이 끊겼을 때 (수동 재발급)

`refresh_token`까지 만료되면(약 2개월 미사용) 새 인가 코드가 필요합니다.

1. 수동 갱신 스크립트: **`scripts/auth_kakao.py`**
   ```bash
   py scripts\auth_kakao.py          # 브라우저 URL 안내 → code 입력
   py scripts\auth_kakao.py --auto   # 로컬 콜백(127.0.0.1:8765)으로 자동 수신
   ```
2. **경로 주의점**: 스크립트는 `btc_live_trading/kakao_code.json` / `.env`에 저장합니다. 서버에서 발급했다면 그 파일을 그대로 두고, 로컬에서 발급했다면 **`kakao_code.json`을 서버의 `btc_live_trading/`로 업로드**(WinSCP)한 뒤 `systemctl restart coinbot.service`로 재시작하세요. 경로가 어긋나면 토큰을 못 찾습니다.
3. `KAKAO_REDIRECT_URI`는 카카오 개발자 콘솔 등록값과 **1글자도 다르면 안 됩니다**(불일치 시 KOE205/KOE006).
4. 잠시 알림만 끄려면 `.env`에 `KAKAO_ALERTS_ENABLED=false`를 두면 매매는 그대로, 카카오 호출만 생략됩니다.

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
