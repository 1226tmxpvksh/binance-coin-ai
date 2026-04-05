"""스캘핑 단일 포지션 상태."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ScalpingPosition:
    symbol: str
    side: str
    entry_date: datetime
    entry_price: float
    size: float
    stop_loss: float
    stop_order_id: Optional[str] = None
    entry_order_id: Optional[str] = None
    tp_target_usdt: float = 0.0


class ScalpingPositionManager:
    def __init__(self) -> None:
        self._pos: Optional[ScalpingPosition] = None
        self.trade_history: List[Dict[str, Any]] = []

    def has_position(self) -> bool:
        return self._pos is not None

    def get(self) -> Optional[ScalpingPosition]:
        return self._pos

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        size: float,
        stop_loss: float,
        stop_order_id: Optional[str],
        entry_order_id: Optional[str],
        tp_target_usdt: float,
    ) -> ScalpingPosition:
        self._pos = ScalpingPosition(
            symbol=symbol,
            side=side,
            entry_date=datetime.now(),
            entry_price=entry_price,
            size=size,
            stop_loss=stop_loss,
            stop_order_id=stop_order_id,
            entry_order_id=entry_order_id,
            tp_target_usdt=tp_target_usdt,
        )
        logger.info(
            "스캘핑 오픈 %s %s @ %.4f size=%.6f",
            symbol,
            side,
            entry_price,
            size,
        )
        return self._pos

    def close_position(
        self,
        exit_price: float,
        exit_reason: str,
    ) -> Optional[Dict[str, Any]]:
        if self._pos is None:
            return None
        p = self._pos
        if p.side == "LONG":
            pnl = p.size * (exit_price - p.entry_price)
        else:
            pnl = p.size * (p.entry_price - exit_price)
        notional = p.size * p.entry_price
        pr = (pnl / notional * 100.0) if notional else 0.0
        rec = {
            "symbol": p.symbol,
            "side": p.side,
            "entry_price": p.entry_price,
            "exit_price": exit_price,
            "size": p.size,
            "pnl": pnl,
            "profit_rate": pr,
            "exit_reason": exit_reason,
            "exit_date": datetime.now(),
        }
        self.trade_history.append(rec)
        self._pos = None
        return rec
