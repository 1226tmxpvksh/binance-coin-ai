"""후보 C(변동성 돌파+거래량+국면)를 1h/4h/1d로만 바꿔 백테스트.

실거래 판단(main_ai)은 호출하지 않는다.
매매 집계 시작은 2025-04-01 UTC. 일봉 EMA60·SMA200이 그 시점에
유효하도록 15m 캐시를 2024-08-01 UTC까지 앞으로 늘린다.
그 이전 캔들은 지표 워밍업이며 매매 횟수에 넣지 않는다.
청산은 15m 하네스와 같은 봉 수(4봉), ATR 손절·익절, 수수료다.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_entry_candidates import (
    CACHE,
    FX_KRW,
    HOLD_BARS,
    INITIAL_USDT,
    KST,
    LEVERAGE,
    RISK,
    TAKER,
    TRADE_START,
    UTC,
    build_features,
    run,
    signal_c,
)

WARMUP_START = datetime(2024, 8, 1, tzinfo=UTC)
# 15m에서 250은 약 2.6일. 상위 봉에서는 SMA 기울기(205봉)가 준비된 뒤부터 본다.
FEATURE_READY = 205
BAR_MS = {"15m": 15 * 60 * 1000, "1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000, "1d": 24 * 60 * 60 * 1000}
STEP_MS = 15 * 60 * 1000


def load_cache() -> list[list[float]]:
    rows: list[list[float]] = []
    if not CACHE.exists():
        return rows
    for line in CACHE.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        rows.append([float(x) for x in parts[:6]])
    return rows


def save_cache(rows: list[list[float]]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    lines = ["open_time_ms,open,high,low,close,volume"]
    lines.extend(",".join(str(x) for x in row) for row in rows)
    CACHE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fetch_range(start_ms: int, end_ms: int) -> list[list[float]]:
    out: list[list[float]] = []
    url = "https://api.binance.com/api/v3/klines"
    cursor = start_ms
    while cursor < end_ms:
        qs = urllib.parse.urlencode(
            {
                "symbol": "BTCUSDT",
                "interval": "15m",
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        req = urllib.request.Request(f"{url}?{qs}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            batch = json.loads(resp.read().decode())
        if not batch:
            break
        for k in batch:
            out.append([float(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
        nxt = int(batch[-1][0]) + STEP_MS
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.12)
    return out


def ensure_rows() -> list[list[float]]:
    rows = load_cache()
    warmup_ms = int(WARMUP_START.timestamp() * 1000)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    if not rows:
        rows = fetch_range(warmup_ms, now_ms)
        save_cache(rows)
        return rows
    first = int(rows[0][0])
    if first > warmup_ms:
        older = fetch_range(warmup_ms, first - 1)
        if older:
            rows = older + rows
            save_cache(rows)
    return rows


def resample(rows: list[list[float]], bar_ms: int) -> tuple[list[list[float]], int]:
    expected = bar_ms // STEP_MS
    buckets: list[list[list[float]]] = []
    buf: list[list[float]] = []
    cur: int | None = None
    short = 0

    def flush(group: list[list[float]], *, drop_short: bool) -> None:
        nonlocal short
        if not group:
            return
        if len(group) < expected:
            short += 1
            if drop_short:
                return
        buckets.append(group)

    for row in rows:
        key = int(row[0]) - (int(row[0]) % bar_ms)
        if cur is None:
            cur = key
        if key != cur:
            flush(buf, drop_short=False)
            buf = []
            cur = key
        buf.append(row)
    flush(buf, drop_short=True)
    out: list[list[float]] = []
    for group in buckets:
        out.append(
            [
                group[0][0],
                group[0][1],
                max(r[2] for r in group),
                min(r[3] for r in group),
                group[-1][4],
                sum(r[5] for r in group),
            ]
        )
    return out, short


def ready_at_trade_start(feats: dict[str, list[float]]) -> dict:
    trade_ms = int(TRADE_START.timestamp() * 1000)
    idx = next(i for i, t in enumerate(feats["t"]) if t >= trade_ms)
    return {
        "index": idx,
        "time_utc": datetime.fromtimestamp(feats["t"][idx] / 1000, UTC).isoformat(),
        "ema60": feats["ema60"][idx],
        "sma200": feats["sma200"][idx],
        "atr": feats["atr"][idx],
    }


def month_counts(monthly: dict[str, float]) -> dict[str, int]:
    return {
        "positive_months": sum(1 for v in monthly.values() if v > 0),
        "negative_months": sum(1 for v in monthly.values() if v < 0),
        "flat_months": sum(1 for v in monthly.values() if v == 0),
        "months": len(monthly),
    }


def brief(row: dict) -> dict:
    out = {k: row[k] for k in row if k != "monthly_usdt"}
    out["price_pnl_usdt"] = row["net_usdt"] + row["fees_usdt"]
    out["monthly_usdt"] = {m: round(v, 2) for m, v in row["monthly_usdt"].items()}
    out.update(month_counts(row["monthly_usdt"]))
    return out


def buy_and_hold(rows: list[list[float]]) -> dict:
    trade_ms = int(TRADE_START.timestamp() * 1000)
    start = next(i for i, r in enumerate(rows) if int(r[0]) >= trade_ms)
    entry = rows[start][4]
    exit_px = rows[-1][4]
    qty = INITIAL_USDT / entry
    entry_fee = entry * qty * TAKER
    exit_fee = exit_px * qty * TAKER
    gross = (exit_px - entry) * qty
    net = gross - entry_fee - exit_fee
    equity: list[float] = []
    monthly: dict[str, float] = {}
    month_key = ""
    month_base = INITIAL_USDT
    last_mark = INITIAL_USDT
    for row in rows[start:]:
        mark = INITIAL_USDT - entry_fee + (row[4] - entry) * qty
        key = datetime.fromtimestamp(int(row[0]) / 1000, KST).strftime("%Y-%m")
        if not month_key:
            month_key = key
        elif key != month_key:
            monthly[month_key] = last_mark - month_base
            month_key = key
            month_base = last_mark
        last_mark = mark
        equity.append(mark)
    last_mark -= exit_fee
    equity[-1] = last_mark
    monthly[month_key] = last_mark - month_base
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    return {
        "name": "BH",
        "trades": 1,
        "win_rate_pct": 100.0 if net > 0 else 0.0,
        "net_usdt": net,
        "net_krw": net * FX_KRW,
        "avg_win_usdt": net if net > 0 else 0.0,
        "avg_loss_usdt": net if net <= 0 else 0.0,
        "payoff": 0.0,
        "profit_factor": None if net > 0 else 0.0,
        "mdd_pct": max_dd * 100.0,
        "max_loss_streak": 0 if net > 0 else 1,
        "final_usdt": INITIAL_USDT + net,
        "fees_usdt": entry_fee + exit_fee,
        "monthly_usdt": monthly,
        "entry_price": entry,
        "exit_price": exit_px,
        "price_return_pct": (exit_px / entry - 1.0) * 100.0,
        "entry_utc": datetime.fromtimestamp(int(rows[start][0]) / 1000, UTC).isoformat(),
        "exit_utc": datetime.fromtimestamp(int(rows[-1][0]) / 1000, UTC).isoformat(),
    }


def main() -> None:
    rows = ensure_rows()
    specs = [
        ("C", "15m", BAR_MS["15m"]),
        ("D", "1h", BAR_MS["1h"]),
        ("E", "4h", BAR_MS["4h"]),
        ("F", "1d", BAR_MS["1d"]),
    ]
    meta = []
    fixed = []
    compound = []
    for name, label, bar_ms in specs:
        series, short = resample(rows, bar_ms) if bar_ms != STEP_MS else (rows, 0)
        feats = build_features(series)
        ready = ready_at_trade_start(feats)
        meta.append(
            {
                "name": name,
                "timeframe": label,
                "bars": len(series),
                "short_buckets": short,
                "first_utc": datetime.fromtimestamp(feats["t"][0] / 1000, UTC).isoformat(),
                "last_utc": datetime.fromtimestamp(feats["t"][-1] / 1000, UTC).isoformat(),
                "hold_hours": HOLD_BARS * (bar_ms / 3_600_000),
                "ready": ready,
            }
        )
        fixed.append(run(name, feats, signal_c, compound=False, min_index=FEATURE_READY))
        compound.append(run(name, feats, signal_c, compound=True, min_index=FEATURE_READY))
        print(label, "bars", len(series), "ready", json.dumps(ready), flush=True)
    bh = buy_and_hold(rows)
    payload = {
        "source": "https://api.binance.com/api/v3/klines BTCUSDT 15m, resampled",
        "candles_15m": len(rows),
        "warmup_start_utc": WARMUP_START.isoformat(),
        "trade_start_utc": TRADE_START.isoformat(),
        "initial_usdt": INITIAL_USDT,
        "leverage": LEVERAGE,
        "risk_per_trade": RISK,
        "taker_each_side": TAKER,
        "hold_bars": HOLD_BARS,
        "series": meta,
        "results_fixed_initial_risk": fixed,
        "results_compound": compound,
        "buy_and_hold": bh,
    }
    out_path = CACHE.parent / "timeframe_candidates_summary.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("series")
    print(json.dumps(meta, ensure_ascii=False))
    for key in ("results_fixed_initial_risk", "results_compound"):
        print(key)
        for row in payload[key]:
            print(json.dumps(brief(row), ensure_ascii=False))
    print("buy_and_hold")
    shown = brief(bh)
    shown["price_return_pct"] = round(bh["price_return_pct"], 4)
    shown["entry_price"] = bh["entry_price"]
    shown["exit_price"] = bh["exit_price"]
    print(json.dumps(shown, ensure_ascii=False))


if __name__ == "__main__":
    main()
