"""
Scalping 1차 신호 (규칙 기반). AI 컨펌은 utils.ai_advisor에서 처리.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
import pandas as pd

logger = logging.getLogger(__name__)


class ScalpingAction(Enum):
    NONE = "NONE"
    LONG = "LONG"


@dataclass
class ScalpingSignal:
    action: ScalpingAction
    reason: str


def evaluate_scalping_signal(
    data: pd.DataFrame,
    rsi_oversold: float = 35.0,
    bb_touch_tolerance: float = 0.002,
) -> ScalpingSignal:
    """
    롱 후보:
    - RSI 과매도 근처
    - 종가가 볼린저 하단 근처이거나, 하단 근처에서 직전봉 대비 반등
    """
    if data is None or len(data) < 2:
        return ScalpingSignal(ScalpingAction.NONE, "데이터 부족")

    row = data.iloc[-1]
    prev = data.iloc[-2]

    rsi = row.get("rsi")
    close = float(row["close"])
    bb_lower = row.get("bb_lower")
    if rsi != rsi or bb_lower != bb_lower:
        return ScalpingSignal(ScalpingAction.NONE, "지표 NaN")

    rsi = float(rsi)
    bb_lower = float(bb_lower)

    near_lower = close <= bb_lower * (1.0 + bb_touch_tolerance)
    bounce = close > float(prev["close"]) and rsi < rsi_oversold + 5

    if rsi < rsi_oversold and (near_lower or bounce):
        return ScalpingSignal(
            ScalpingAction.LONG,
            f"RSI={rsi:.1f}, BB하단 근접/반등",
        )

    return ScalpingSignal(ScalpingAction.NONE, f"미충족 RSI={rsi:.1f}")


def predict_signal_stub(data: pd.DataFrame) -> ScalpingSignal:
    """AI/모델 교체용 진입점."""
    return evaluate_scalping_signal(data)
