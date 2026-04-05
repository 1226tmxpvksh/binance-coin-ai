@echo off
chcp 65001 > nul
echo ================================================
echo 전략 비교 백테스트 실행
echo ================================================
echo.
echo 원본 전략 (RISK 2%%) vs 실전 설정 (RISK 1%%) 비교
echo.
echo ⏳ 실행 중... (1-2분 소요)
echo.

python compare_strategies.py

if %errorlevel% equ 0 (
    echo.
    echo ================================================
    echo ✅ 비교 완료!
    echo ================================================
    echo.
    echo 📊 결과 확인:
    echo    - output\original\         원본 전략 그래프
    echo    - output\live_settings\    실전 설정 그래프
    echo    - output\comparison\       비교 차트
    echo    - strategy_comparison.log  실행 로그
    echo.
    pause
) else (
    echo.
    echo ================================================
    echo ❌ 오류 발생
    echo ================================================
    echo.
    echo 로그 확인: strategy_comparison.log
    echo.
    pause
)
