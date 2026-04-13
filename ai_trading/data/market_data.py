from __future__ import annotations

from typing import Dict, List

import requests


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def _sma(values: List[float], period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    subset = values[-period:]
    return sum(subset) / period


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return var ** 0.5


def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def fetch_market_snapshot(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 250) -> Dict[str, float]:
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    klines = response.json()

    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]

    ema20 = _ema(closes[-120:], 20)
    ema60 = _ema(closes[-180:], 60)
    sma20 = _sma(closes, 20)
    std20 = _std(closes[-20:]) if len(closes) >= 20 else 0.0
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    rsi14 = _rsi(closes, 14)

    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    atr14 = sum(trs[-14:]) / 14 if len(trs) >= 14 else (sum(trs) / len(trs) if trs else 0.0)

    last_price = closes[-1]
    atr_pct = (atr14 / last_price * 100) if last_price else 0.0
    ema_gap_pct = ((ema20 - ema60) / last_price * 100) if last_price else 0.0
    bb_pos = 0.5
    if bb_upper > bb_lower:
        bb_pos = (last_price - bb_lower) / (bb_upper - bb_lower)

    return {
        "symbol": symbol,
        "interval": interval,
        "price": last_price,
        "rsi": rsi14,
        "ema20": ema20,
        "ema60": ema60,
        "ema_gap_pct": ema_gap_pct,
        "atr14": atr14,
        "atr_pct": atr_pct,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_position": bb_pos,
    }
