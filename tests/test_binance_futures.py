from exchange.binance_futures import BinanceFuturesClient


class FakeBinanceClient:
    time_offset = 0

    def futures_klines(self, **kwargs):
        return [
            [0, "1", "2", "0.5", "1.5", "10", 1000, "0", 1, "0", "0", "0"],
            [1001, "1.5", "2.5", "1", "2", "11", 3000, "0", 1, "0", "0", "0"],
        ]


def build_client() -> BinanceFuturesClient:
    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client.client = FakeBinanceClient()
    client.symbol = "BTCUSDT"
    client.dry_run = False
    client.paper_balance = 0
    client.retry_count = 1
    client.retry_sleep_seconds = 0
    client._rules = None
    return client


def test_fetch_klines_excludes_incomplete_candle():
    client = build_client()
    client._server_now_ms = lambda: 2000

    data = client.fetch_klines("4h", 2)

    assert len(data) == 1
    assert data.iloc[-1]["close"] == 1.5


def test_protective_stops_require_true_reduce_only():
    client = build_client()
    client.get_open_algo_orders = lambda: [
        {
            "symbol": "BTCUSDT",
            "side": "SELL",
            "orderType": "STOP_MARKET",
            "reduceOnly": "false",
        },
        {
            "symbol": "BTCUSDT",
            "side": "SELL",
            "orderType": "STOP_MARKET",
            "reduceOnly": "true",
        },
    ]

    orders = client.get_protective_stop_orders()

    assert len(orders) == 1
    assert orders[0]["reduceOnly"] == "true"
