"""
TRADING_MODE=scalping — 폴링·AI 컨펌(fail_closed)·금액 익절.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from binance.client import Client

from goal_tracker import GoalTracker
from scalping_position_manager import ScalpingPositionManager
from safety_manager import SafetyManager
from strategy.data_loader import fetch_historical_data
from strategy.indicators import add_scalping_indicators
from strategy.risk_manager import (
    calculate_position_size,
    calculate_position_value,
    should_take_profit_by_pnl_usdt,
    stop_loss_price_long,
    tp_target_usdt_from_krw,
    unrealized_pnl_usdt,
)
from strategy.scalping_strategy import ScalpingAction, predict_signal_stub
from utils.ai_advisor import AiAdvisor
from utils.indicator_prompt import format_indicators_for_ai

logger = logging.getLogger(__name__)


class ScalpingEngine:
    def __init__(
        self,
        config: Dict[str, Any],
        client: Client,
        executors: Dict[str, Any],
        position_manager: ScalpingPositionManager,
        safety_manager: SafetyManager,
        notifier: Any,
        goal_tracker: GoalTracker,
        ai_advisor: Optional[AiAdvisor] = None,
    ):
        self.config = config
        self.client = client
        self.executors = executors
        self.pos = position_manager
        self.safety = safety_manager
        self.notifier = notifier
        self.goals = goal_tracker
        self.ai = ai_advisor
        self._running = False

    def start(self) -> None:
        self._running = True
        snap = self.goals.snapshot()
        self.notifier.notify_scalping_start(
            snap.achievement_pct,
            snap.remaining_krw,
            bool(self.config.get("DRY_RUN")),
        )
        for _, ex in self.executors.items():
            ex.set_leverage(self.config["LEVERAGE"])

    def stop(self) -> None:
        self._running = False
        logger.info("ScalpingEngine 중지")

    def _executor(self, symbol: str):
        return self.executors.get(symbol)

    def _fetch(self, symbol: str):
        return fetch_historical_data(
            client=self.client,
            symbol=symbol,
            interval=self.config["TIMEFRAME"],
            limit=200,
        )

    def run_once(self) -> bool:
        if not self._running:
            return True

        first_ex = next(iter(self.executors.values()))
        balance = first_ex.get_account_balance()
        if balance is None:
            logger.error("잔고 조회 실패")
            return True

        min_bal = self.config["MIN_TRADING_BALANCE_USDT"]
        transfer_net = 0.0
        if balance >= min_bal or self.pos.has_position():
            tr = first_ex.get_futures_net_transfer_usdt_since(
                self.safety.initial_capital_baseline_ms
            )
            transfer_net = tr if tr is not None else 0.0

        can_trade, reason = self.safety.can_trade(
            balance,
            external_transfer_net_usdt=transfer_net,
        )
        if not can_trade:
            logger.warning("거래 불가: %s", reason)
            if self.safety.emergency_stopped:
                snap = self.goals.snapshot()
                self.notifier.notify_emergency_stop_scalping(
                    reason,
                    max(0.0, self.safety.initial_capital - balance),
                    snap.achievement_pct,
                    snap.remaining_krw,
                )
                return False
            return True

        if self.pos.has_position():
            p = self.pos.get()
            assert p is not None
            return self._manage_open_position(p, balance)

        if balance < min_bal:
            logger.warning(
                "최소 잔고 미만 (%.2f < %.2f)",
                balance,
                min_bal,
            )
            return True

        symbols: List[str] = self.config["SCALPING_SYMBOLS"]
        for symbol in symbols:
            ex = self._executor(symbol)
            if ex is None:
                continue
            try:
                data = self._fetch(symbol)
                if data is None or len(data) < max(
                    30, self.config["BB_PERIOD"] + 2
                ):
                    continue
                data = add_scalping_indicators(
                    data,
                    self.config["RSI_PERIOD"],
                    self.config["BB_PERIOD"],
                    self.config["BB_STD"],
                )
                sig = predict_signal_stub(data)
                if sig.action != ScalpingAction.LONG:
                    continue

                last = data.iloc[-1]
                price = float(last["close"])
                snap = self.goals.snapshot()

                if self.config.get("USE_AI_CONFIRM"):
                    if self.ai is None:
                        logger.error(
                            "AI 컨펌 활성화인데 AiAdvisor 없음 — 진입 취소(fail_closed)"
                        )
                        self.notifier.notify_scalping_ai_rejected(
                            symbol,
                            "AiAdvisor 미설정",
                            snap.achievement_pct,
                            snap.remaining_krw,
                        )
                        break
                    prompt = format_indicators_for_ai(
                        symbol,
                        data,
                        self.config["TIMEFRAME"],
                        extra=f"규칙 신호: {sig.reason}",
                    )
                    approved, ai_reason = self.ai.confirm_long_entry(prompt)
                    if not approved:
                        logger.warning(
                            "AI 거절 또는 장애(fail_closed) — 진입 취소: %s",
                            ai_reason,
                        )
                        self.notifier.notify_scalping_ai_rejected(
                            symbol,
                            ai_reason,
                            snap.achievement_pct,
                            snap.remaining_krw,
                        )
                        break

                self.notifier.notify_scalping_signal(
                    symbol,
                    "LONG",
                    price,
                    sig.reason,
                    snap.achievement_pct,
                    snap.remaining_krw,
                )

                if self.config.get("REQUIRE_USER_CONFIRM"):
                    self.notifier.notify_scalping_confirm_wait(
                        self.config["CONFIRM_TIMEOUT_SECONDS"],
                        snap.achievement_pct,
                        snap.remaining_krw,
                    )
                    time.sleep(float(self.config["CONFIRM_TIMEOUT_SECONDS"]))

                self._enter_long(symbol, price, balance)
                break
            except Exception as e:
                logger.error("심볼 처리 오류 %s: %s", symbol, e, exc_info=True)
                snap = self.goals.snapshot()
                self.notifier.notify_error_scalping(
                    str(e),
                    snap.achievement_pct,
                    snap.remaining_krw,
                )

        return True

    def _enter_long(self, symbol: str, entry_price: float, balance: float) -> None:
        ex = self._executor(symbol)
        if ex is None:
            return
        if not self.safety.check_trade_limit():
            return

        sl = stop_loss_price_long(entry_price, self.config["STOP_LOSS_PCT"])
        size = calculate_position_size(
            balance,
            entry_price,
            sl,
            self.config["RISK_PER_TRADE"],
            self.config["LEVERAGE"],
        )
        if size <= 0:
            return
        pv = calculate_position_value(size, entry_price)
        if pv < self.config["MIN_POSITION_SIZE_USDT"]:
            logger.warning("최소 명목 미만")
            return

        tp_usdt = tp_target_usdt_from_krw(
            self.config["TP_PROFIT_KRW"],
            self.config["KRW_PER_USDT"],
        )
        snap = self.goals.snapshot()

        order = ex.open_long_position(size)
        if order is None:
            self.notifier.notify_error_scalping(
                "진입 주문 실패",
                snap.achievement_pct,
                snap.remaining_krw,
            )
            return
        filled = float(order.get("avgPrice", entry_price))
        stop_order = ex.place_stop_loss_order("SELL", size, sl)
        if stop_order is None:
            ex.close_long_position(size)
            self.notifier.notify_error_scalping(
                "손절 주문 실패로 진입 취소",
                snap.achievement_pct,
                snap.remaining_krw,
            )
            return

        self.pos.open_position(
            symbol=symbol,
            side="LONG",
            entry_price=filled,
            size=size,
            stop_loss=sl,
            stop_order_id=str(stop_order.get("orderId")),
            entry_order_id=str(order.get("orderId")),
            tp_target_usdt=tp_usdt,
        )
        self.safety.increment_daily_trades()
        snap2 = self.goals.snapshot()
        self.notifier.notify_scalping_filled(
            symbol,
            filled,
            size,
            sl,
            tp_usdt,
            snap2.achievement_pct,
            snap2.remaining_krw,
        )

    def _manage_open_position(self, p, balance: float) -> bool:
        ex = self._executor(p.symbol)
        if ex is None:
            return True
        mark = ex.get_current_price()
        if mark is None:
            return True
        snap = self.goals.snapshot()

        if p.stop_order_id:
            st = ex.get_order_status(p.stop_order_id)
            if st and st.get("status") == "FILLED":
                exit_p = float(st.get("avgPrice", mark))
                rec = self.pos.close_position(exit_p, "stop_loss")
                if rec:
                    self.safety.update_daily_pnl(rec["pnl"])
                    self.goals.record_realized_pnl_usdt(rec["pnl"])
                    snap2 = self.goals.snapshot()
                    self.notifier.notify_scalping_closed(
                        p.symbol,
                        rec["entry_price"],
                        exit_p,
                        rec["pnl"],
                        "stop_loss",
                        snap2.achievement_pct,
                        snap2.remaining_krw,
                        balance + rec["pnl"],
                    )
                return True

        pnl_u = unrealized_pnl_usdt("LONG", p.entry_price, p.size, mark)
        if should_take_profit_by_pnl_usdt(pnl_u, p.tp_target_usdt):
            if p.stop_order_id:
                ex.cancel_order(p.stop_order_id)
            order = ex.close_long_position(p.size)
            if order is None:
                self.notifier.notify_error_scalping(
                    "익절 청산 실패",
                    snap.achievement_pct,
                    snap.remaining_krw,
                )
                return True
            exit_p = float(order.get("avgPrice", mark))
            rec = self.pos.close_position(exit_p, "take_profit_pnl")
            if rec:
                self.safety.update_daily_pnl(rec["pnl"])
                self.goals.record_realized_pnl_usdt(rec["pnl"])
                snap2 = self.goals.snapshot()
                self.notifier.notify_scalping_closed(
                    p.symbol,
                    rec["entry_price"],
                    exit_p,
                    rec["pnl"],
                    "take_profit_pnl",
                    snap2.achievement_pct,
                    snap2.remaining_krw,
                    balance + rec["pnl"],
                )
        return True
