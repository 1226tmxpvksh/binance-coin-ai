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


def volume_lookback_bars(interval: str, lookback_days: float | None = None) -> int:
    """interval 캔들 기준 lookback_days(기본 7일)에 해당하는 봉 수."""
    if lookback_days is None:
        raw = os.environ.get("AI_VOLUME_LOOKBACK_DAYS", "7").strip()
        try:
            lookback_days = float(raw)
        except ValueError:
            lookback_days = 7.0
    secs = _parse_interval_seconds(interval)
    bars_per_day = max(1, 86400 // secs)
    return max(14, int(lookback_days * bars_per_day))


def _volume_min_ratio() -> float:
    raw = os.environ.get("AI_VOLUME_MIN_RATIO", "1.5").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 1.5


def _compute_volume_metrics(volumes: List[float]) -> Dict[str, float | bool]:
    if not volumes:
        return {
            "volume": 0.0,
            "volume_prev": 0.0,
            "volume_7d_avg": 0.0,
            "volume_ratio": 0.0,
            "volume_ratio_pct": 0.0,
            "volume_surge": False,
        }
    current = float(volumes[-1])
    prev = float(volumes[-2]) if len(volumes) >= 2 else current
    avg = sum(volumes) / len(volumes)
    ratio = (current / avg) if avg > 0 else 0.0
    min_ratio = _volume_min_ratio()
    return {
        "volume": current,
        "volume_prev": prev,
        "volume_7d_avg": avg,
        "volume_ratio": ratio,
        "volume_ratio_pct": ratio * 100.0,
        "volume_surge": ratio >= min_ratio,
    }


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


def fetch_market_snapshot(symbol: str = "BTCUSDT", interval: str = "15m", limit: int | None = None) -> Dict[str, float]:
    lookback = volume_lookback_bars(interval)
    req_limit = max(int(limit or 0), lookback, 250)
    cache_key = (symbol.upper(), interval, int(req_limit))
    now = time.time()
    ttl = _snapshot_cache_ttl_seconds(interval)
    cached = _SNAPSHOT_CACHE.get(cache_key)
    if cached is not None and now < cached[0]:
        return dict(cached[1])

    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": req_limit}
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    klines = response.json()

    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    vol_metrics = _compute_volume_metrics(volumes[-lookback:] if len(volumes) >= lookback else volumes)

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
        "volume": float(vol_metrics["volume"]),
        "volume_prev": float(vol_metrics["volume_prev"]),
        "volume_7d_avg": float(vol_metrics["volume_7d_avg"]),
        "volume_ratio": float(vol_metrics["volume_ratio"]),
        "volume_ratio_pct": float(vol_metrics["volume_ratio_pct"]),
        "volume_surge": bool(vol_metrics["volume_surge"]),
        "volume_lookback_bars": float(lookback),
    }
    _SNAPSHOT_CACHE[cache_key] = (now + ttl, snapshot)
    return snapshot
