"""
최신 캔들·지표를 GPT 프롬프트용 텍스트로 요약.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd


def format_indicators_for_ai(
    symbol: str,
    data: pd.DataFrame,
    timeframe: str,
    extra: Optional[str] = None,
) -> str:
    """
    마지막 1~2봉 OHLCV + RSI·볼린저 요약 문자열.
    """
    if data is None or data.empty:
        return f"[{symbol}] 데이터 없음."

    last = data.iloc[-1]
    prev = data.iloc[-2] if len(data) > 1 else last

    def fnum(x: Any, nd: int = 4) -> str:
        try:
            v = float(x)
            if v != v:
                return "N/A"
            return f"{v:.{nd}f}"
        except (TypeError, ValueError):
            return "N/A"

    lines = [
        f"심볼: {symbol} ({timeframe})",
        f"최신봉 시각(인덱스): {last.name}",
        f"OHLCV(최신): O={fnum(last['open'])} H={fnum(last['high'])} "
        f"L={fnum(last['low'])} C={fnum(last['close'])} V={fnum(last['volume'], 2)}",
        f"이전봉 종가: {fnum(prev['close'])}",
        f"RSI(14): {fnum(last.get('rsi', float('nan')), 2)}",
        f"볼린저: 중심={fnum(last.get('bb_mid'))} 상단={fnum(last.get('bb_upper'))} "
        f"하단={fnum(last.get('bb_lower'))}",
        "규칙 기반 시스템이 이 구간에서 LONG(매수) 진입 후보로 표시했습니다. "
        "리스크가 크면 거절해 주세요.",
    ]
    if extra:
        lines.append(f"추가: {extra}")
    return "\n".join(lines)
