"""월간 목표(구독료) 대비 실현 손익 추적."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)

STATE_FILENAME = "scalping_goal_state.json"


def _month_key_now() -> str:
    if ZoneInfo:
        dt = datetime.now(ZoneInfo("Asia/Seoul"))
    else:
        dt = datetime.now()
    return f"{dt.year:04d}-{dt.month:02d}"


@dataclass
class GoalSnapshot:
    month_key: str
    monthly_realized_usdt: float
    monthly_target_krw: float
    krw_per_usdt: float
    achievement_pct: float
    remaining_krw: float


class GoalTracker:
    def __init__(
        self,
        monthly_target_krw: float,
        krw_per_usdt: float,
        state_path: Optional[str] = None,
    ):
        self.monthly_target_krw = monthly_target_krw
        self.krw_per_usdt = krw_per_usdt
        base = os.path.dirname(os.path.abspath(__file__))
        self.state_path = state_path or os.path.join(base, STATE_FILENAME)
        self._month_key = _month_key_now()
        self._monthly_realized_usdt = 0.0
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.isfile(self.state_path):
            return
        try:
            if os.path.getsize(self.state_path) == 0:
                logger.debug("목표 상태 파일 크기 0 — 새로 시작")
                return
            with open(self.state_path, "r", encoding="utf-8") as f:
                raw = f.read()
            if not raw.strip():
                logger.debug("목표 상태 파일 내용 없음 — 새로 시작")
                return
            data: Dict[str, Any] = json.loads(raw)
            if not isinstance(data, dict):
                logger.warning("목표 상태 형식 오류(객체 아님) — 무시")
                return
            mk = data.get("month_key", "")
            if mk == _month_key_now():
                self._monthly_realized_usdt = float(
                    data.get("monthly_realized_usdt", 0.0)
                )
                self._month_key = mk
        except json.JSONDecodeError as e:
            logger.warning(
                "목표 상태 JSON 파싱 실패 — 이번 실행에서는 누적 0부터 시작: %s",
                e,
            )
        except (OSError, TypeError, ValueError) as e:
            logger.warning("목표 상태 로드 실패 — 무시: %s", e)
        except Exception as e:
            logger.warning("목표 상태 로드 실패: %s", e)

    def _save_state(self) -> None:
        try:
            payload = {
                "month_key": self._month_key,
                "monthly_realized_usdt": self._monthly_realized_usdt,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning("목표 상태 저장 실패: %s", e)

    def _rollover_if_needed(self) -> None:
        mk = _month_key_now()
        if mk != self._month_key:
            self._month_key = mk
            self._monthly_realized_usdt = 0.0
            self._save_state()

    def record_realized_pnl_usdt(self, pnl_usdt: float) -> None:
        self._rollover_if_needed()
        self._monthly_realized_usdt += pnl_usdt
        self._save_state()

    def snapshot(self) -> GoalSnapshot:
        self._rollover_if_needed()
        realized_krw = self._monthly_realized_usdt * self.krw_per_usdt
        target = self.monthly_target_krw
        if target <= 0:
            achievement = 0.0
            remaining = 0.0
        else:
            achievement = (realized_krw / target) * 100.0
            remaining = max(0.0, target - realized_krw)
        return GoalSnapshot(
            month_key=self._month_key,
            monthly_realized_usdt=self._monthly_realized_usdt,
            monthly_target_krw=target,
            krw_per_usdt=self.krw_per_usdt,
            achievement_pct=achievement,
            remaining_krw=remaining,
        )
