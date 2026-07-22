from datetime import datetime, timedelta, timezone

from scheduler import next_candle_check_time


KST = timezone(timedelta(hours=9))


def test_next_candle_check_uses_binance_utc_boundary():
    now = datetime(2026, 7, 22, 20, 30, tzinfo=KST)

    next_run = next_candle_check_time(now)

    assert next_run == datetime(2026, 7, 22, 21, 1, tzinfo=KST)


def test_next_candle_check_crosses_local_midnight():
    now = datetime(2026, 7, 22, 21, 30, tzinfo=KST)

    next_run = next_candle_check_time(now)

    assert next_run == datetime(2026, 7, 23, 1, 1, tzinfo=KST)


def test_next_candle_check_waits_for_delay_after_boundary():
    now = datetime(2026, 7, 22, 21, 0, 30, tzinfo=KST)

    next_run = next_candle_check_time(now)

    assert next_run == datetime(2026, 7, 22, 21, 1, tzinfo=KST)
