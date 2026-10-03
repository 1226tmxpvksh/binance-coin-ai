"""buy_hold 분기와 현물 주문 가드. 거래소 주문은 내지 않는다."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ai_trading"))

from binance_spot_tools import SpotBuyError, floor_quote, spot_market_buy_quote  # noqa: E402
from buy_hold import (  # noqa: E402
    OPEN_POSITION_BLOCK,
    build_entry_record,
    enter_block_reason,
    format_hold_status,
    quote_to_spend,
    replace_trading_strategy_line,
    switch_block_reason,
    trading_strategy,
    write_entry,
    load_entry,
)


class _Spot:
    def __init__(self) -> None:
        self.orders: list[dict] = []

    def get_symbol_info(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "quoteAssetPrecision": 2,
            "filters": [{"filterType": "NOTIONAL", "minNotional": "10"}],
        }

    def order_market_buy(self, **params):
        self.orders.append(params)
        return {
            "orderId": 9,
            "executedQty": "0.001",
            "cummulativeQuoteQty": "80.00",
        }


class BuyHoldModeTest(unittest.TestCase):
    def test_default_strategy_is_buy_hold(self) -> None:
        os.environ.pop("TRADING_STRATEGY", None)
        self.assertEqual(trading_strategy(), "buy_hold")
        os.environ["TRADING_STRATEGY"] = "active"
        self.assertEqual(trading_strategy(), "active")
        os.environ["TRADING_STRATEGY"] = "other"
        self.assertEqual(trading_strategy(), "buy_hold")

    def test_open_futures_block_switch_and_buy(self) -> None:
        rows = [{"symbol": "BTCUSDT", "positionAmt": "0.01"}]
        reason = switch_block_reason(check_ok=True, open_rows=rows)
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn(OPEN_POSITION_BLOCK, reason)
        self.assertIn("positionAmt", reason)
        self.assertEqual(switch_block_reason(check_ok=False, open_rows=[]), "선물 포지션 조회에 실패했습니다. 전환하지 않습니다.")
        self.assertIsNone(switch_block_reason(check_ok=True, open_rows=[]))
        self.assertIn("매수하지 않습니다", enter_block_reason(check_ok=False, open_rows=[], entry_exists=False) or "")
        self.assertIn("추가 매수", enter_block_reason(check_ok=True, open_rows=[], entry_exists=True) or "")

    def test_env_line_only_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("AI_DRY_RUN=true\nAI_SYMBOL=BTCUSDT\n", encoding="utf-8")
            ok, _ = replace_trading_strategy_line("buy_hold", path=path)
            self.assertTrue(ok)
            text = path.read_text(encoding="utf-8")
            self.assertIn("AI_DRY_RUN=true", text)
            self.assertIn("AI_SYMBOL=BTCUSDT", text)
            self.assertIn("TRADING_STRATEGY=buy_hold", text)
            ok, message = replace_trading_strategy_line("active", path=path)
            self.assertTrue(ok)
            self.assertEqual(message, "updated")
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("TRADING_STRATEGY="), 1)
            self.assertIn("TRADING_STRATEGY=active", text)
            self.assertNotIn("TRADING_STRATEGY=buy_hold", text)

    def test_quote_buy_rejects_below_minimum_without_order(self) -> None:
        client = _Spot()
        with self.assertRaises(SpotBuyError):
            spot_market_buy_quote(client, "BTCUSDT", 9.99)
        self.assertEqual(client.orders, [])
        self.assertEqual(floor_quote(10.129, 2), 10.12)
        fill = spot_market_buy_quote(client, "BTCUSDT", 80.129)
        self.assertEqual(fill["qty_btc"], 0.001)
        self.assertEqual(fill["price"], 80000.0)
        self.assertEqual(client.orders[0]["quoteOrderQty"], "80.12")
        self.assertNotIn("leverage", client.orders[0])

    def test_report_and_entry_roundtrip(self) -> None:
        self.assertEqual(quote_to_spend(250.0, 100), 250.0)
        record = build_entry_record(
            fill={"symbol": "BTCUSDT", "price": 80000.0, "qty_btc": 0.01, "quote_usdt": 800.0, "order_id": "1"},
            allocation=100,
        )
        text = format_hold_status(record, 82000.0)
        self.assertIn("보유 BTC 수량: 0.01000000", text)
        self.assertIn("평가금액: 820.00 USDT", text)
        self.assertIn("매수가 대비 손익률: +2.50%", text)
        self.assertIn("진입 기록이 없습니다", format_hold_status(None, 1.0))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "buy_hold_entry.json"
            write_entry(record, path)
            loaded = load_entry(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["order_id"], "1")

    def test_active_path_still_present(self) -> None:
        source = (ROOT / "ai_trading" / "main_ai.py").read_text(encoding="utf-8")
        self.assertIn("def _technical_signal_decision", source)
        impl = source.split("def _run_cycle_impl", 1)[1].split("\ndef run_once", 1)[0]
        self.assertLess(impl.find('trading_strategy() != "active"'), impl.find("_technical_signal_decision"))
        shutdown = source.split("def _graceful_shutdown_work", 1)[1].split("\ndef _shutdown_signal_handler", 1)[0]
        self.assertLess(shutdown.find('trading_strategy() != "active"'), shutdown.find("_market_close_symbol"))


if __name__ == "__main__":
    unittest.main()
