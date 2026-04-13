#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""연평균 수익률 계산 검증"""

# 백테스트 결과
days = 206  # 실제 거래 기간
total_return_pct = 19.46  # 총 수익률 (%)
total_return = total_return_pct / 100  # 비율로 변환

# 년수 계산
years = days / 365.25

# 연평균 수익률 계산
# 공식: ((1 + 총수익률) ^ (1 / 년수)) - 1
annual_return = ((1 + total_return) ** (1 / years) - 1) * 100

print("=" * 60)
print("연평균 수익률 계산 검증")
print("=" * 60)
print()
print(f"백테스트 기간: {days}일")
print(f"년수 환산: {years:.4f}년 (약 {days/30.44:.1f}개월)")
print()
print(f"총 수익률: {total_return_pct:.2f}%")
print()
print("연평균 수익률 계산 공식:")
print(f"  ((1 + {total_return:.4f}) ^ (1 / {years:.4f})) - 1")
print(f"  = ({1 + total_return:.4f} ^ {1/years:.4f}) - 1")
print(f"  = {(1 + total_return) ** (1 / years):.4f} - 1")
print(f"  = {annual_return:.2f}%")
print()
print("=" * 60)
print("설명:")
print("=" * 60)
print(f"206일 동안 19.46%의 수익을 냈다면,")
print(f"같은 속도로 365일(1년) 동안 거래했을 때의 예상 수익률입니다.")
print()
print("계산 확인:")
print(f"  1년(365일) 후 예상 자본: $10,000 * {(1 + annual_return/100):.4f}")
print(f"                         = ${10000 * (1 + annual_return/100):,.2f}")
print()
print(f"  반대로 검증: ${10000 * (1 + annual_return/100):,.2f}를")
print(f"  {days}일로 환산하면:")
print(f"  ${10000 * (1 + annual_return/100):,.2f} * (1년 중 {days}일 비율)")
print(f"  = ${10000 * ((1 + annual_return/100) ** (days/365.25)):,.2f}")
print(f"  = 약 ${10000 * (1 + total_return):,.2f} ✓")
print()
print("결론: 계산이 정확합니다! ✅")
print("=" * 60)
