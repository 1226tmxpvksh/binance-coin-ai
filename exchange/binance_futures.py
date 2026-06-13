"""Binance Futures 연결과 주문 실행 래퍼."""

from __future__ import annotations

import logging
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

import pandas as pd
from binance.client import Client

logger = logging.getLogger(__name__)


class BinanceFuturesClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        dry_run: bool,
        paper_balance: float,
        retry_count: int = 3,
        retry_sleep_seconds: float = 1.0,
    ):
        self.client = Client(api_key or None, api_secret or None)
        self.symbol = symbol
        self.dry_run = dry_run
        self.paper_balance = paper_balance
        self.retry_count = retry_count
        self.retry_sleep_seconds = retry_sleep_seconds
        self._rules: dict[str, Decimal] | None = None

    def _with_retry(self, operation_name: str, func):
        last_error = None
        for attempt in range(1, self.retry_count + 1):
            try:
                return func()
            except Exception as exc:
                last_error = exc
                logger.warning("%s 실패: %s/%s %s", operation_name, attempt, self.retry_count, exc)
                if attempt < self.retry_count:
                    time.sleep(self.retry_sleep_seconds)
        raise RuntimeError(f"{operation_name} {self.retry_count}회 실패: {last_error}") from last_error

    def sync_time(self) -> None:
        try:
            server_time = self.client.futures_time()["serverTime"]
            local_time = int(time.time() * 1000)
            self.client.time_offset = server_time - local_time - 1500
            logger.info("Binance Futures 시간 동기화 완료: offset=%sms", self.client.time_offset)
        except Exception as exc:
            logger.warning("Futures 시간 동기화 실패: %s", exc)

    def set_leverage(self, leverage: int) -> None:
        if self.dry_run:
            logger.info("[DRY RUN] 레버리지 설정 생략: %sx", leverage)
            return
        self.client.futures_change_leverage(symbol=self.symbol, leverage=leverage)
        logger.info("레버리지 설정 완료: %sx", leverage)

    def fetch_klines(self, interval: str, limit: int) -> pd.DataFrame:
        rows = self._with_retry(
            "futures_klines",
            lambda: self.client.futures_klines(symbol=self.symbol, interval=interval, limit=limit),
        )
        data = pd.DataFrame(
            rows,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_asset_volume",
                "number_of_trades",
                "taker_buy_base_asset_volume",
                "taker_buy_quote_asset_volume",
                "ignore",
            ],
        )
        data["timestamp"] = pd.to_datetime(data["timestamp"], unit="ms")
        data = data[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        data[["open", "high", "low", "close", "volume"]] = data[
            ["open", "high", "low", "close", "volume"]
        ].astype(float)
        data.drop_duplicates(subset=["timestamp"], inplace=True)
        data.sort_values("timestamp", inplace=True)
        data.set_index("timestamp", inplace=True)
        return data

    def get_balance_usdt(self) -> float:
        if self.dry_run:
            return self.paper_balance
        balances = self._with_retry("futures_account_balance", self.client.futures_account_balance)
        for item in balances:
            if item["asset"] == "USDT":
                return float(item["balance"])
        raise RuntimeError("USDT 선물 잔고를 찾지 못했습니다.")

    def get_position(self) -> dict[str, Any] | None:
        if self.dry_run:
            return None
        positions = self._with_retry(
            "futures_position_information",
            lambda: self.client.futures_position_information(symbol=self.symbol),
        )
        for position in positions:
            amount = float(position["positionAmt"])
            if amount != 0:
                return {
                    "amount": amount,
                    "entry_price": float(position["entryPrice"]),
                    "unrealized_pnl": float(position["unRealizedProfit"]),
                }
        return None

    def get_current_price(self) -> float:
        ticker = self._with_retry(
            "futures_symbol_ticker",
            lambda: self.client.futures_symbol_ticker(symbol=self.symbol),
        )
        return float(ticker["price"])

    def _load_rules(self) -> dict[str, Decimal]:
        if self._rules is not None:
            return self._rules
        exchange_info = self._with_retry("futures_exchange_info", self.client.futures_exchange_info)
        for info in exchange_info["symbols"]:
            if info["symbol"] != self.symbol:
                continue
            rules = {}
            for filter_info in info["filters"]:
                if filter_info["filterType"] == "PRICE_FILTER":
                    rules["tick_size"] = Decimal(filter_info["tickSize"])
                if filter_info["filterType"] == "LOT_SIZE":
                    rules["step_size"] = Decimal(filter_info["stepSize"])
                    rules["min_qty"] = Decimal(filter_info["minQty"])
            self._rules = rules
            return rules
        raise RuntimeError(f"심볼 규칙을 찾지 못했습니다: {self.symbol}")

    def normalize_quantity(self, quantity: float) -> float:
        rules = self._load_rules()
        raw = Decimal(str(quantity))
        step = rules["step_size"]
        normalized = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
        if normalized < rules["min_qty"]:
            raise ValueError(f"수량이 최소 주문 수량보다 작습니다: {normalized}")
        return float(normalized)

    def normalize_price(self, price: float, up: bool = False) -> float:
        rules = self._load_rules()
        raw = Decimal(str(price))
        tick = rules["tick_size"]
        rounding = ROUND_UP if up else ROUND_DOWN
        return float((raw / tick).to_integral_value(rounding=rounding) * tick)

    def place_market_order(self, side: str, quantity: float, reduce_only: bool = False) -> dict[str, Any]:
        quantity = self.normalize_quantity(quantity)
        if self.dry_run:
            price = self.get_current_price()
            logger.info("[DRY RUN] MARKET %s qty=%.6f reduceOnly=%s", side, quantity, reduce_only)
            return {
                "orderId": f"DRY_MARKET_{int(time.time())}",
                "side": side,
                "type": "MARKET",
                "status": "FILLED",
                "executedQty": str(quantity),
                "avgPrice": str(price),
            }
        return self._with_retry(
            "futures_create_market_order",
            lambda: self.client.futures_create_order(
                symbol=self.symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
                reduceOnly=reduce_only,
            ),
        )

    def place_stop_market(self, side: str, quantity: float, stop_price: float) -> dict[str, Any]:
        quantity = self.normalize_quantity(quantity)
        stop_price = self.normalize_price(stop_price, up=False)
        if self.dry_run:
            logger.info("[DRY RUN] STOP_MARKET %s qty=%.6f stop=%.2f", side, quantity, stop_price)
            return {
                "orderId": f"DRY_STOP_{int(time.time())}",
                "side": side,
                "type": "STOP_MARKET",
                "status": "NEW",
                "stopPrice": str(stop_price),
                "origQty": str(quantity),
            }
        return self._with_retry(
            "futures_create_stop_market_order",
            lambda: self.client.futures_create_order(
                symbol=self.symbol,
                side=side,
                type="STOP_MARKET",
                stopPrice=stop_price,
                quantity=quantity,
                reduceOnly=True,
            ),
        )

    def cancel_all_orders(self) -> None:
        if self.dry_run:
            logger.info("[DRY RUN] 모든 미체결 주문 취소")
            return
        self._with_retry(
            "futures_cancel_all_open_orders",
            lambda: self.client.futures_cancel_all_open_orders(symbol=self.symbol),
        )

    def get_open_orders(self) -> list[dict[str, Any]]:
        if self.dry_run:
            return []
        return self._with_retry(
            "futures_get_open_orders",
            lambda: self.client.futures_get_open_orders(symbol=self.symbol),
        )

    def cancel_order(self, order_id: str | int) -> None:
        if self.dry_run:
            logger.info("[DRY RUN] 주문 취소: %s", order_id)
            return
        self._with_retry(
            "futures_cancel_order",
            lambda: self.client.futures_cancel_order(symbol=self.symbol, orderId=order_id),
        )

