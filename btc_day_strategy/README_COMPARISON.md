# 전략 비교 백테스트 가이드

## 개요

`btc_live_trading` 실전 매매 프로그램의 설정과 원본 `btc_day_strategy` 백테스트 전략의 차이를 비교하고 검증하기 위한 도구입니다.

## 주요 차이점

### 원본 전략 (config.py)
- **RISK_PER_TRADE**: 2% (공격적)
- **MIN_POSITION_SIZE_USDT**: 없음
- **안전장치**: 없음

### 실전 설정 (config_live_settings.py)
- **RISK_PER_TRADE**: 1% (보수적)
- **MIN_POSITION_SIZE_USDT**: $20 (바이낸스 최소 요구사항)
- **안전장치**: 일일 최대 손실 10%, 긴급 정지 15%

## 사용 방법

### 1. 비교 백테스트 실행

```bash
cd btc_day_strategy
python compare_strategies.py
```

### 2. 결과 확인

실행 후 다음 결과가 생성됩니다:

#### 콘솔 출력
- 두 전략의 주요 지표 비교 테이블
- 수익률, 승률, 낙폭, 거래 횟수 등
- 핵심 차이점 설명
- 추천 사항

#### 그래프 출력 (output/ 폴더)
1. **output/original/** - 원본 전략 (RISK 2%) 결과
   - 자본 곡선
   - 연도별 수익률
   - 낙폭 곡선
   - 월별 수익
   - 거래 분포

2. **output/live_settings/** - 실전 설정 (RISK 1%) 결과
   - 자본 곡선
   - 연도별 수익률
   - 낙폭 곡선
   - 월별 수익
   - 거래 분포

3. **output/comparison/** - 비교 차트
   - 자본 곡선 비교
   - 수익률 비교
   - 리스크 비교
   - 거래 통계 비교

#### 로그 파일
- **strategy_comparison.log** - 전체 실행 로그

## 비교 항목

### 수익률 지표
- 총 수익률 (%)
- 연간 수익률 (%)
- 최종 자본 ($)

### 리스크 지표
- 최대 낙폭 (%)
- 샤프 비율
- 평균 손실 ($)

### 거래 통계
- 총 거래 횟수
- 승률 (%)
- 승리/손실 횟수
- 평균 수익/손실
- 손익비

## 예상 결과

### 원본 전략 (RISK 2%)
✅ **장점**:
- 높은 수익률 가능
- 더 많은 거래 기회

⚠️ **단점**:
- 높은 변동성
- 큰 낙폭 위험
- 실전 적용 어려움

### 실전 설정 (RISK 1%)
✅ **장점**:
- 안정적인 수익
- 낮은 낙폭
- 실전 적용 가능
- 안전장치 보호

⚠️ **단점**:
- 상대적으로 낮은 수익률
- 보수적인 접근

## 해석 가이드

### 1. 수익률이 더 중요한 경우
→ 원본 전략 (RISK 2%) 고려
→ 하지만 큰 낙폭 감수 필요

### 2. 안정성이 더 중요한 경우
→ 실전 설정 (RISK 1%) 권장
→ 실제 돈으로 거래하기에 적합

### 3. 샤프 비율이 높은 쪽
→ 위험 대비 수익이 더 효율적
→ 일반적으로 실전 설정이 유리

### 4. 최대 낙폭 비교
→ 실제 계좌에서 견딜 수 있는 수준인가?
→ 10% 이상 낙폭은 심리적 압박 큼

## 주의사항

⚠️ **백테스트는 과거 데이터입니다**
- 미래 수익을 보장하지 않음
- 슬리피지, 수수료 미반영
- 시장 환경 변화 가능

⚠️ **실전 적용 전 확인사항**
1. 백테스트 기간이 충분한가? (최소 1년 이상)
2. 여러 시장 환경을 거쳤는가? (상승/하락/횡보)
3. 최대 낙폭을 견딜 수 있는가?
4. 자금 관리가 적절한가?

## 커스터마이징

### 다른 설정으로 비교하려면

1. `config_live_settings.py` 복사
   ```bash
   cp config_live_settings.py config_custom.py
   ```

2. 설정 수정
   ```python
   # config_custom.py
   RISK_PER_TRADE = 0.015  # 1.5%로 변경
   STOP_LOSS_MULTIPLIER = 0.8  # 손절 타이트하게
   ```

3. `compare_strategies.py`에서 import 추가
   ```python
   import config_custom
   ```

4. 비교 실행
   ```python
   result_custom = run_backtest_with_config(
       data.copy(),
       config_custom,
       "커스텀 설정"
   )
   ```

## 트러블슈팅

### 데이터 로드 실패
```
[ERROR] 데이터 로드 실패
```
→ API 키 확인 (.env 파일)
→ 인터넷 연결 확인
→ 바이낸스 API 상태 확인

### 그래프 생성 실패
```
[ERROR] 한글 폰트 'Malgun Gothic'을 찾을 수 없습니다
```
→ Windows: 자동 설치됨
→ Mac/Linux: 
```bash
# Mac
brew install font-nanum

# Linux
sudo apt-get install fonts-nanum
```

→ `visualization.py` 수정:
```python
plt.rcParams['font.family'] = 'NanumGothic'  # Mac/Linux
```

### 메모리 부족
→ 백테스트 기간 줄이기
→ 데이터 양 확인

## 참고 파일

- `config.py` - 원본 전략 설정
- `config_live_settings.py` - 실전 설정
- `compare_strategies.py` - 비교 스크립트
- `backtest_engine.py` - 백테스트 엔진
- `visualization.py` - 그래프 생성
- `performance.py` - 성과 분석

## 문의

버그나 개선사항이 있다면 이슈로 제보해주세요!
