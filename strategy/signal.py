"""최종 선정 전략: EMA200 + ATR Breakout + ATR Stop 2.0."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class SignalType(Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    EXIT = "EXIT"


@dataclass(frozen=True)
class EntrySignal:
    signal: SignalType
    signal_time: pd.Timestamp
    target_price: float
    reference_close: float
    atr: float
    ema_200: float
    reason: str


def build_entry_signal(data: pd.DataFrame, atr_entry_multiplier: float) -> EntrySignal | None:
    """
    최신 완료 4H 봉이 조건을 만족하면 다음 봉 시작 구간에서 진입 신호를 반환한다.
    조건:
    - close > EMA200
    - close > previous close + previous ATR * K
    """
    if len(data) < 3:
        return None

    current = data.iloc[-1]
    previous = data.iloc[-2]
    if pd.isna(current["ema_200"]) or pd.isna(previous["atr"]) or previous["atr"] <= 0:
        return None

    target_price = float(previous["close"] + (previous["atr"] * atr_entry_multiplier))
    if current["close"] <= current["ema_200"]:
        return None
    if current["close"] <= target_price:
        return None

    return EntrySignal(
        signal=SignalType.BUY,
        signal_time=data.index[-1],
        target_price=target_price,
        reference_close=float(previous["close"]),
        atr=float(current["atr"]),
        ema_200=float(current["ema_200"]),
        reason="Close > EMA200 and close breakout above previous close + ATR",
    )


def latest_confirmed_pivot_low(data: pd.DataFrame) -> tuple[float | None, pd.Timestamp | None]:
    pivot_rows = data[pd.notna(data["confirmed_pivot_low"])]
    if pivot_rows.empty:
        return None, None
    latest = pivot_rows.iloc[-1]
    return float(latest["confirmed_pivot_low"]), latest["confirmed_pivot_low_time"]

