from types import SimpleNamespace

from execution.engine import LiveTradingEngine
from monitor.state_store import TradingState


class FakeStateStore:
    def save(self, state):
        self.saved_state = state


class FakeExchange:
    def __init__(self):
        self.cancelled = []
        self.orders = [
            self._stop(1, 61_806),
            self._stop(2, 62_505.1),
            self._stop(3, 63_736.1),
        ]

    @staticmethod
    def _stop(algo_id, trigger_price):
        return {
            "algoId": algo_id,
            "symbol": "BTCUSDT",
            "side": "SELL",
            "orderType": "STOP_MARKET",
            "triggerPrice": str(trigger_price),
            "reduceOnly": True,
        }

    def get_protective_stop_orders(self):
        return list(self.orders)

    def get_current_price(self):
        return 65_624.5

    def cancel_algo_order(self, algo_id):
        self.cancelled.append(str(algo_id))
        self.orders = [order for order in self.orders if str(order["algoId"]) != str(algo_id)]


def test_reconcile_keeps_highest_valid_stop_and_cancels_stale_orders():
    engine = LiveTradingEngine.__new__(LiveTradingEngine)
    engine.exchange = FakeExchange()
    engine.config = SimpleNamespace(DRY_RUN=False)
    engine.state = TradingState()
    engine.state.position.has_position = True
    engine.state_store = FakeStateStore()

    engine._reconcile_protective_stops()

    assert engine.exchange.cancelled == ["1", "2"]
    assert engine.state.position.stop_order_id == "3"
    assert engine.state.position.stop_price == 63_736.1
