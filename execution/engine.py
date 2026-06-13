"""BTC ATR EMA200 실전 매매 엔진."""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from database.trade_db import TradeDatabase
from exchange.binance_futures import BinanceFuturesClient
from monitor.safety import SafetyManager
from monitor.state_store import StateStore, TradingState
from risk.position_sizing import calculate_position_size, calculate_position_value
from strategy.market_data import build_market_data
from strategy.signal import build_entry_signal, latest_confirmed_pivot_low

logger = logging.getLogger(__name__)


class LiveTradingEngine:
    def __init__(
        self,
        exchange: BinanceFuturesClient,
        state_store: StateStore,
        safety: SafetyManager,
        database: TradeDatabase,
        notifier,
        config,
    ):
        self.exchange = exchange
        self.state_store = state_store
        self.safety = safety
        self.database = database
        self.notifier = notifier
        self.config = config
        self.state: TradingState = state_store.load()

    def start(self) -> None:
        self.exchange.sync_time()
        self.exchange.set_leverage(self.config.LEVERAGE)
        self.notifier.notify_start(self.config.DRY_RUN)
        self.database.record_event("engine_start", {"dry_run": self.config.DRY_RUN})

    def run_once(self) -> None:
        try:
            raw_data = self.exchange.fetch_klines(self.config.INTERVAL, self.config.LOOKBACK_BARS)
            if len(raw_data) < self.config.EMA_FILTER_PERIOD:
                self._no_entry(f"데이터 부족: {len(raw_data)} bars")
                return

            data = build_market_data(
                raw_data,
                interval=self.config.INTERVAL,
                max_gap_multiplier=self.config.MAX_CANDLE_GAP_MULTIPLIER,
                ema_period=self.config.EMA_FILTER_PERIOD,
                atr_period=self.config.ATR_PERIOD,
                pivot_length=self.config.PIVOT_LENGTH,
            )
            balance = self.exchange.get_balance_usdt()

            self._sync_external_position()
            if self.state.position.has_position:
                self._manage_position(data)
            else:
                self._check_entry(data, balance)
            self.state_store.save(self.state)
        except Exception as exc:
            logger.exception("엔진 실행 오류")
            self.database.record_event("error", {"message": str(exc)})
            self.notifier.notify_error(str(exc))
            self.state_store.save(self.state)

    def _sync_external_position(self) -> None:
        live_position = self.exchange.get_position()
        if live_position is None:
            if not self.config.DRY_RUN and self.state.position.has_position:
                logger.warning("거래소 포지션이 없어 외부 청산으로 판단하고 로컬 상태를 초기화합니다.")
                exit_price = self.exchange.get_current_price()
                pnl = (exit_price - self.state.position.entry_price) * self.state.position.quantity
                self.safety.record_trade_close(self.state.safety, pnl)
                self.database.record_event(
                    "external_close_detected",
                    {
                        "exit_price": exit_price,
                        "entry_price": self.state.position.entry_price,
                        "quantity": self.state.position.quantity,
                        "pnl": pnl,
                    },
                )
                self.database.record_trade(
                    {
                        "entry_time": self.state.position.entry_time,
                        "exit_time": datetime.now().isoformat(),
                        "entry_price": self.state.position.entry_price,
                        "exit_price": exit_price,
                        "quantity": self.state.position.quantity,
                        "pnl": pnl,
                        "reason": "exchange_position_closed",
                    }
                )
                self.database.record_position(
                    self.config.SYMBOL,
                    "CLOSED",
                    {
                        "entry_time": self.state.position.entry_time,
                        "entry_price": self.state.position.entry_price,
                        "quantity": self.state.position.quantity,
                        "stop_price": self.state.position.stop_price,
                        "exit_price": exit_price,
                        "pnl": pnl,
                    },
                )
                self.notifier.notify_close(exit_price, pnl, "exchange_position_closed")
                self.state.position.has_position = False
                self.state.position.entry_time = None
                self.state.position.entry_price = 0.0
                self.state.position.quantity = 0.0
                self.state.position.stop_price = 0.0
                self.state.position.stop_order_id = None
            return

        if live_position["amount"] > 0 and not self.state.position.has_position:
            self.state.position.has_position = True
            self.state.position.entry_time = datetime.now().isoformat()
            self.state.position.entry_price = live_position["entry_price"]
            self.state.position.quantity = abs(live_position["amount"])
            logger.warning("거래소 기존 롱 포지션을 로컬 상태로 동기화했습니다.")

    def _check_entry(self, data: pd.DataFrame, balance: float) -> None:
        can_trade, reason = self.safety.can_open_new_trade(self.state.safety, balance)
        if not can_trade:
            self._no_entry(reason)
            return
        if balance < self.config.MIN_TRADING_BALANCE_USDT:
            self._no_entry(f"잔고 부족: {balance:.2f} USDT")
            return

        signal = build_entry_signal(data, self.config.ATR_ENTRY_MULTIPLIER)
        if signal is None:
            self._no_entry("EMA200 또는 ATR breakout 조건 미충족")
            return
        self.database.record_signal(
            "BUY",
            signal.reason,
            signal.target_price,
            {
                "signal_time": signal.signal_time,
                "target_price": signal.target_price,
                "reference_close": signal.reference_close,
                "atr": signal.atr,
                "ema_200": signal.ema_200,
            },
        )
        signal_time = signal.signal_time.isoformat()
        if self.state.position.last_signal_time == signal_time:
            self._no_entry(f"이미 처리한 신호: {signal_time}")
            return

        current_price = self.exchange.get_current_price()
        entry_price = max(current_price, signal.target_price)
        stop_price = entry_price - (signal.atr * self.config.ATR_STOP_MULTIPLIER)
        quantity = calculate_position_size(balance, entry_price, stop_price, self.config.RISK_PER_TRADE)
        position_value = calculate_position_value(quantity, entry_price)
        if quantity <= 0 or position_value < self.config.MIN_POSITION_VALUE_USDT:
            self._no_entry("포지션 크기가 최소 주문 가치보다 작음")
            return

        self.notifier.notify_signal(signal.target_price, stop_price, quantity, signal.reason)
        entry_order = self.exchange.place_market_order("BUY", quantity, reduce_only=False)
        self.database.record_order(self.config.SYMBOL, entry_order)
        executed_qty = float(entry_order.get("executedQty") or entry_order.get("origQty") or quantity)
        avg_price = float(entry_order.get("avgPrice") or current_price)
        stop_order = self.exchange.place_stop_market("SELL", executed_qty, stop_price)
        self.database.record_order(self.config.SYMBOL, stop_order)

        self.state.position.has_position = True
        self.state.position.entry_time = datetime.now().isoformat()
        self.state.position.entry_price = avg_price
        self.state.position.quantity = executed_qty
        self.state.position.stop_price = stop_price
        self.state.position.stop_order_id = str(stop_order.get("orderId"))
        self.state.position.last_signal_time = signal_time
        self.state_store.save(self.state)

        self.database.record_event(
            "entry",
            {
                "entry_order": entry_order,
                "stop_order": stop_order,
                "entry_price": avg_price,
                "stop_price": stop_price,
                "quantity": executed_qty,
            },
        )
        self.database.record_position(
            self.config.SYMBOL,
            "LONG",
            {
                "entry_time": self.state.position.entry_time,
                "entry_price": avg_price,
                "quantity": executed_qty,
                "stop_price": stop_price,
            },
        )
        self.notifier.notify_entry(avg_price, stop_price, executed_qty)

    def _manage_position(self, data: pd.DataFrame) -> None:
        position = self.state.position
        current_price = self.exchange.get_current_price()
        if current_price <= position.stop_price:
            self._close_position(current_price, "local_stop_reached")
            return

        pivot_low, pivot_time = latest_confirmed_pivot_low(data)
        if pivot_low is None:
            return
        if position.last_pivot_low_time == str(pivot_time):
            return
        if pivot_low > position.stop_price:
            old_stop = position.stop_price
            self.exchange.cancel_all_orders()
            stop_order = self.exchange.place_stop_market("SELL", position.quantity, pivot_low)
            self.database.record_order(self.config.SYMBOL, stop_order)
            position.stop_price = pivot_low
            position.stop_order_id = str(stop_order.get("orderId"))
            position.last_pivot_low = pivot_low
            position.last_pivot_low_time = str(pivot_time)
            self.database.record_event(
                "stop_update",
                {"old_stop": old_stop, "new_stop": pivot_low, "pivot_time": str(pivot_time)},
            )
            self.notifier.notify_stop_update(old_stop, pivot_low)

    def _close_position(self, exit_price: float, reason: str) -> None:
        position = self.state.position
        if not position.has_position:
            return
        self.exchange.cancel_all_orders()
        close_order = self.exchange.place_market_order("SELL", position.quantity, reduce_only=True)
        self.database.record_order(self.config.SYMBOL, close_order)
        pnl = (exit_price - position.entry_price) * position.quantity
        exit_time = datetime.now().isoformat()
        self.safety.record_trade_close(self.state.safety, pnl)
        self.database.record_event(
            "close",
            {
                "exit_price": exit_price,
                "entry_price": position.entry_price,
                "quantity": position.quantity,
                "pnl": pnl,
                "reason": reason,
            },
        )
        self.database.record_trade(
            {
                "entry_time": position.entry_time,
                "exit_time": exit_time,
                "entry_price": position.entry_price,
                "exit_price": exit_price,
                "quantity": position.quantity,
                "pnl": pnl,
                "reason": reason,
            }
        )
        self.database.record_position(
            self.config.SYMBOL,
            "CLOSED",
            {
                "entry_time": position.entry_time,
                "entry_price": position.entry_price,
                "quantity": position.quantity,
                "stop_price": position.stop_price,
                "exit_time": exit_time,
                "exit_price": exit_price,
                "pnl": pnl,
            },
        )
        self.notifier.notify_close(exit_price, pnl, reason)
        position.has_position = False
        position.entry_time = None
        position.entry_price = 0.0
        position.quantity = 0.0
        position.stop_price = 0.0
        position.stop_order_id = None

    def _no_entry(self, reason: str) -> None:
        logger.info("진입 없음: %s", reason)
        self.database.record_event("no_entry", {"reason": reason})
        self.database.record_signal("HOLD", reason, 0.0, {"reason": reason})
        self.notifier.notify_no_entry(reason)

