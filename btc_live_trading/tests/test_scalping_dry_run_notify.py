"""
DRY_RUN 경로에서 스캘핑 알림·AI fail_closed 스텁 검증 (네트워크 없음).
실행: 프로젝트 루트에서  python -m pytest tests/test_scalping_dry_run_notify.py -q
     또는  cd btc_live_trading && python -m unittest tests.test_scalping_dry_run_notify
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# btc_live_trading 를 import path 에 올림
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestScalpingGoalFooter(unittest.TestCase):
    def test_footer_text(self):
        from kakao_notifier import KakaoNotifier

        n = KakaoNotifier(access_token="", enabled=False)
        foot = n._scalping_goal_footer(12.5, 87500.0)
        self.assertIn("10만 원", foot)
        self.assertIn("87,500", foot)


class TestAiAdvisorFailClosed(unittest.TestCase):
    def test_empty_key(self):
        from utils.ai_advisor import AiAdvisor

        adv = AiAdvisor("")
        ok, reason = adv.confirm_long_entry("test")
        self.assertFalse(ok)


class TestScalpingEngineNotifyOnReject(unittest.TestCase):
    def test_ai_reject_calls_notifier(self):
        import pandas as pd
        from scalping_engine import ScalpingEngine
        from scalping_position_manager import ScalpingPositionManager
        from safety_manager import SafetyManager
        from goal_tracker import GoalTracker

        rows = 50
        idx = pd.date_range("2024-01-01", periods=rows, freq="5min")
        df = pd.DataFrame(
            {
                "open": [100.0 + i * 0.01 for i in range(rows)],
                "high": [101.0 + i * 0.01 for i in range(rows)],
                "low": [99.0 + i * 0.01 for i in range(rows)],
                "close": [100.5 + i * 0.01 for i in range(rows)],
                "volume": [1000.0] * rows,
            },
            index=idx,
        )
        df.iloc[-1, df.columns.get_loc("close")] = 50.0
        df.iloc[-2, df.columns.get_loc("close")] = 60.0

        notifier = MagicMock()
        # GoalTracker는 유효한 JSON이거나 파일 없음이어야 함 (빈 파일은 json.load 실패 방지)
        goal_fd, goal_path = tempfile.mkstemp(suffix=".json")
        os.close(goal_fd)
        with open(goal_path, "w", encoding="utf-8") as gf:
            gf.write("{}")
        self.addCleanup(lambda: os.unlink(goal_path) if os.path.isfile(goal_path) else None)
        goals = GoalTracker(100000.0, 1350.0, state_path=goal_path)
        # 잔고 5000과 기준 자본을 맞추고, 긴급 정지 한도를 넉넉히 두어 can_trade 통과
        safety = SafetyManager(5000.0, 10.0, 99.0, 30)
        pos = ScalpingPositionManager()
        ex = MagicMock()
        ex.get_account_balance.return_value = 5000.0
        ex.get_futures_net_transfer_usdt_since.return_value = 0.0
        ex.get_current_price.return_value = 100.0
        executors = {"BTCUSDT": ex}

        ai = MagicMock()
        ai.confirm_long_entry.return_value = (False, "테스트 거절")

        cfg = {
            "SCALPING_SYMBOLS": ["BTCUSDT"],
            "TIMEFRAME": "5m",
            "LEVERAGE": 3,
            "RSI_PERIOD": 14,
            "BB_PERIOD": 20,
            "BB_STD": 2.0,
            "STOP_LOSS_PCT": 5.0,
            "TP_PROFIT_KRW": 10000,
            "KRW_PER_USDT": 1350,
            "RISK_PER_TRADE": 0.01,
            "MIN_POSITION_SIZE_USDT": 20,
            "MIN_TRADING_BALANCE_USDT": 50,
            "DRY_RUN": True,
            "USE_AI_CONFIRM": True,
            "REQUIRE_USER_CONFIRM": False,
            "CONFIRM_TIMEOUT_SECONDS": 0,
        }

        client = MagicMock()
        with patch(
            "scalping_engine.fetch_historical_data", return_value=df
        ), patch(
            "scalping_engine.add_scalping_indicators", side_effect=lambda d, *a, **k: d
        ), patch(
            "scalping_engine.predict_signal_stub",
        ) as mock_sig:
            from strategy.scalping_strategy import ScalpingAction, ScalpingSignal

            mock_sig.return_value = ScalpingSignal(
                ScalpingAction.LONG, "unit"
            )

            eng = ScalpingEngine(
                cfg,
                client,
                executors,
                pos,
                safety,
                notifier,
                goals,
                ai_advisor=ai,
            )
            eng._running = True
            eng.run_once()

        notifier.notify_scalping_ai_rejected.assert_called()
        call_kw = notifier.notify_scalping_ai_rejected.call_args
        self.assertEqual(call_kw[0][0], "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
