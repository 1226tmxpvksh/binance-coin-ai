from __future__ import annotations

import os
import time
from typing import Dict, List, Tuple

import requests

_SNAPSHOT_CACHE: Dict[Tuple[str, str, int], Tuple[float, Dict[str, float]]] = {}


def _parse_interval_seconds(interval: str) -> int:
    s = (interval or "15m").strip().lower()
    if s.endswith("m"):
        return max(60, int(s[:-1]) * 60)
    if s.endswith("h"):
        return max(3600, int(s[:-1]) * 3600)
    if s.endswith("d"):
        return max(86400, int(s[:-1]) * 86400)
    return 900


def _snapshot_cache_ttl_seconds(interval: str) -> int:
    """15m 추세 매매: 루프(기본 300초)마다 Binance klines를 매번 긁지 않도록 TTL 적용."""
    raw = os.environ.get("AI_MARKET_CACHE_SECONDS", "").strip()
    if raw:
        try:
            return max(30, int(float(raw)))
        except ValueError:
            pass
    interval_secs = _parse_interval_seconds(interval)
    return max(30, min(300, interval_secs // 3))


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


def fetch_market_snapshot(symbol: str = "BTCUSDT", interval: str = "15m", limit: int = 250) -> Dict[str, float]:
    cache_key = (symbol.upper(), interval, int(limit))
    now = time.time()
    ttl = _snapshot_cache_ttl_seconds(interval)
    cached = _SNAPSHOT_CACHE.get(cache_key)
    if cached is not None and now < cached[0]:
        return dict(cached[1])

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

    snapshot = {
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
    _SNAPSHOT_CACHE[cache_key] = (now + ttl, snapshot)
    return snapshot
