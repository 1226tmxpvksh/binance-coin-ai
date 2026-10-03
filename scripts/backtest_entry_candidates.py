"""15m 진입 후보 A/B/C 백테스트. 실거래 판단(main_ai)은 호출하지 않는다.

데이터: Binance 현물 공개 klines (api.binance.com), live 스냅샷과 같은 출처.
지표: market_data.py 의 구간 EMA / 단순 RSI / SMA 볼린저 / ATR 평균과 동일.
청산·사이징·수수료는 세 후보가 같다. 차이는 진입 신호뿐.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "backtest" / "btcusdt_15m_spot.csv"
KST = timezone(timedelta(hours=9))
UTC = timezone.utc

# 거래 구간. 지표 워밍업을 위해 그 이전 캔들도 받는다.
TRADE_START = datetime(2025, 4, 1, tzinfo=UTC)
FETCH_START = datetime(2025, 3, 1, tzinfo=UTC)
FX_KRW = 1380.0
INITIAL_KRW = 500_000.0
INITIAL_USDT = INITIAL_KRW / FX_KRW
LEVERAGE = 3
RISK = 0.015
TAKER = 0.0004
HOLD_BARS = 4  # AI_PAPER_HOLD_MINUTES=60 / 15m
ATR_STOP_MULT = 1.2
ATR_TP_MULT = 2.25
MIN_RR = 1.75
MIN_TP_RATIO = 0.012
SCORE_THRESHOLD = 0.60
SCORE_GAP = 0.12
ATR_BONUS_MIN_PCT = 0.15  # 현재 .env AI_ATR_MIN_ENTRY_PCT. 점수 가산만.


def fetch_klines() -> list[list[float]]:
    if CACHE.exists() and CACHE.stat().st_size > 0:
        rows = []
        for line in CACHE.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            rows.append([float(x) for x in parts[:6]])
        if rows:
            return rows
    start = int(FETCH_START.timestamp() * 1000)
    end = int(datetime.now(UTC).timestamp() * 1000)
    out: list[list[float]] = []
    url = "https://api.binance.com/api/v3/klines"
    while start < end:
        qs = urllib.parse.urlencode(
            {
                "symbol": "BTCUSDT",
                "interval": "15m",
                "startTime": start,
                "endTime": end,
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
        start = int(batch[-1][0]) + 15 * 60 * 1000
        time.sleep(0.12)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    lines = ["open_time_ms,open,high,low,close,volume"]
    lines.extend(",".join(str(x) for x in row) for row in out)
    CACHE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def ema_window(closes: list[float], end: int, length: int, period: int) -> float:
    start = end - length + 1
    if start < 0:
        start = 0
    k = 2 / (period + 1)
    out = closes[start]
    for v in closes[start + 1 : end + 1]:
        out = v * k + out * (1 - k)
    return out


def rsi14(closes: list[float], end: int) -> float:
    if end < 14:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(end - 13, end + 1):
        diff = closes[i] - closes[i - 1]
        gains += max(diff, 0.0)
        losses += abs(min(diff, 0.0))
    avg_gain = gains / 14
    avg_loss = losses / 14
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def build_features(rows: list[list[float]]) -> dict[str, list[float]]:
    n = len(rows)
    opens = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    vols = [r[5] for r in rows]
    times = [int(r[0]) for r in rows]
    ema20 = [0.0] * n
    ema60 = [0.0] * n
    gap = [0.0] * n
    rsi = [50.0] * n
    bb = [0.5] * n
    atr = [0.0] * n
    atr_pct = [0.0] * n
    sma200 = [0.0] * n
    sma_slope = [0.0] * n
    vol_ma = [0.0] * n
    trs = [0.0] * n
    for i in range(1, n):
        trs[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    for i in range(n):
        if i >= 119:
            ema20[i] = ema_window(closes, i, 120, 20)
        if i >= 179:
            ema60[i] = ema_window(closes, i, 180, 60)
        if closes[i]:
            gap[i] = (ema20[i] - ema60[i]) / closes[i] * 100.0
        rsi[i] = rsi14(closes, i)
        if i >= 19:
            window = closes[i - 19 : i + 1]
            mean = sum(window) / 20
            var = sum((x - mean) ** 2 for x in window) / 19
            std = var ** 0.5
            upper = mean + 2 * std
            lower = mean - 2 * std
            if upper > lower:
                bb[i] = (closes[i] - lower) / (upper - lower)
        if i >= 14:
            atr[i] = sum(trs[i - 13 : i + 1]) / 14
            atr_pct[i] = (atr[i] / closes[i] * 100.0) if closes[i] else 0.0
        if i >= 199:
            sma200[i] = sum(closes[i - 199 : i + 1]) / 200
        if i >= 204 and sma200[i] > 0:
            prev = sum(closes[i - 204 : i - 4]) / 200
            sma_slope[i] = sma200[i] - prev
        if i >= 19:
            vol_ma[i] = sum(vols[i - 19 : i + 1]) / 20
    return {
        "t": times,
        "o": opens,
        "h": highs,
        "l": lows,
        "c": closes,
        "v": vols,
        "ema20": ema20,
        "ema60": ema60,
        "gap": gap,
        "rsi": rsi,
        "bb": bb,
        "atr": atr,
        "atr_pct": atr_pct,
        "sma200": sma200,
        "sma_slope": sma_slope,
        "vol_ma": vol_ma,
    }


def momentum_scores(f: dict[str, list[float]], i: int, *, drop_extremes: bool) -> tuple[float, float]:
    rsi = f["rsi"][i]
    bb = f["bb"][i]
    gap = f["gap"][i]
    atr_pct = f["atr_pct"][i]
    ema20 = f["ema20"][i]
    ema60 = f["ema60"][i]
    buy = 0.0
    sell = 0.0
    if ema20 > ema60 and gap >= 0.03:
        buy += 0.34
    elif ema20 < ema60 and gap <= -0.03:
        sell += 0.34
    if 52.0 <= rsi <= 74.0:
        buy += 0.26
    elif 26.0 <= rsi <= 48.0:
        sell += 0.26
    elif not drop_extremes and rsi > 74.0 and gap > 0:
        buy += 0.16
    elif not drop_extremes and rsi < 26.0 and gap < 0:
        sell += 0.16
    if 0.58 <= bb <= 1.08:
        buy += 0.22
    elif -0.08 <= bb <= 0.42:
        sell += 0.22
    if atr_pct >= ATR_BONUS_MIN_PCT:
        buy += 0.08
        sell += 0.08
    return buy, sell


def decide(buy: float, sell: float) -> str | None:
    side = "BUY" if buy >= sell else "SELL"
    score = max(buy, sell)
    if score < SCORE_THRESHOLD or abs(buy - sell) < SCORE_GAP:
        return None
    return side


def signal_a(f: dict[str, list[float]], i: int) -> str | None:
    return decide(*momentum_scores(f, i, drop_extremes=False))


def signal_b(f: dict[str, list[float]], i: int) -> str | None:
    rsi = f["rsi"][i]
    prev = f["rsi"][i - 1]
    # 극단에서 되돌아오는 봉은 반대 방향(평균회귀)을 그 봉의 신호로 쓴다.
    if prev > 70.0 and rsi <= 70.0 and rsi < prev:
        return "SELL"
    if prev < 30.0 and rsi >= 30.0 and rsi > prev:
        return "BUY"
    return decide(*momentum_scores(f, i, drop_extremes=True))


def signal_c(
    f: dict[str, list[float]],
    i: int,
    *,
    vol_mult: float = 1.0,
    k_floor: float = 0.0,
) -> str | None:
    ema20 = f["ema20"][i]
    ema60 = f["ema60"][i]
    close = f["c"][i]
    sma = f["sma200"][i]
    slope = f["sma_slope"][i]
    if ema20 > ema60:
        state = "BULL"
    elif sma > 0 and close < sma and slope < 0:
        state = "BEAR"
    else:
        return None
    atr_now = f["atr"][i]
    atr_max = max(f["atr"][i - 13 : i + 1]) if i >= 13 else atr_now
    k_raw = 0.0 if atr_max <= 0 else min(atr_now / atr_max, 0.7)
    k = min(max(k_raw, k_floor), 0.7)
    prev_range = f["h"][i - 1] - f["l"][i - 1]
    vol_ok = f["v"][i - 1] > vol_mult * f["vol_ma"][i - 1] > 0
    if not vol_ok:
        return None
    if state == "BULL":
        trigger = f["o"][i] + prev_range * k
        if f["h"][i] > trigger:
            return "BUY"
    elif state == "BEAR":
        trigger = f["o"][i] - prev_range * k
        if f["l"][i] < trigger:
            return "SELL"
    return None


def tp_distance(price: float, atr: float) -> float:
    stop = atr * ATR_STOP_MULT
    dist = max(atr * ATR_TP_MULT, price * MIN_TP_RATIO)
    if stop > 0:
        dist = max(dist, stop * MIN_RR)
    return dist


def run(
    name: str,
    f: dict[str, list[float]],
    signal,
    *,
    compound: bool,
    min_index: int = 250,
    leverage: float = LEVERAGE,
    sizing: str = "risk",
    fraction: float = 0.30,
    trade_start_ms: int | None = None,
    trade_end_ms: int | None = None,
) -> dict:
    n = len(f["c"])
    cash = INITIAL_USDT
    equity_curve: list[float] = []
    trades: list[dict] = []
    i = min_index
    if trade_start_ms is None:
        trade_start_ms = int(TRADE_START.timestamp() * 1000)
    while i < n - 1:
        equity_curve.append(cash)
        if f["t"][i] < trade_start_ms or (trade_end_ms is not None and f["t"][i] >= trade_end_ms) or f["atr"][i] <= 0:
            i += 1
            continue
        side = signal(f, i)
        if side is None:
            i += 1
            continue
        entry = f["c"][i]
        atr = f["atr"][i]
        stop_dist = atr * ATR_STOP_MULT
        if entry <= 0 or stop_dist <= 0:
            i += 1
            continue
        if compound and cash <= 0:
            i += 1
            continue
        base = cash if compound else INITIAL_USDT
        if base <= 0:
            i += 1
            continue
        if sizing == "fixed_fraction":
            qty = (base * fraction) / entry
        else:
            qty = (base * RISK) / stop_dist
        qty = min(qty, (base * leverage) / entry)
        if qty <= 0:
            i += 1
            continue
        if side == "BUY":
            sl = entry - stop_dist
            tp = entry + tp_distance(entry, atr)
        else:
            sl = entry + stop_dist
            tp = entry - tp_distance(entry, atr)
        entry_fee = entry * qty * TAKER
        cash -= entry_fee
        exit_price = entry
        exit_i = min(i + HOLD_BARS, n - 1)
        for j in range(i + 1, exit_i + 1):
            hi = f["h"][j]
            lo = f["l"][j]
            mark = f["c"][j]
            hit_sl = lo <= sl if side == "BUY" else hi >= sl
            hit_tp = hi >= tp if side == "BUY" else lo <= tp
            if hit_sl:
                exit_price = sl
                exit_i = j
                mark = sl
            elif hit_tp:
                exit_price = tp
                exit_i = j
                mark = tp
            else:
                exit_price = mark
            if side == "BUY":
                unrealized = (mark - entry) * qty
            else:
                unrealized = (entry - mark) * qty
            equity_curve.append(cash + unrealized)
            if hit_sl or hit_tp:
                break
        if side == "BUY":
            gross = (exit_price - entry) * qty
        else:
            gross = (entry - exit_price) * qty
        exit_fee = abs(exit_price * qty) * TAKER
        cash += gross - exit_fee
        trades.append(
            {
                "exit_ms": f["t"][exit_i],
                "net": gross - entry_fee - exit_fee,
                "gross": gross,
                "fee": entry_fee + exit_fee,
            }
        )
        i = exit_i + 1
        equity_curve.append(cash)
    if cash > 0:
        equity_curve.append(cash)
    return summarize(name, trades, equity_curve)


def summarize(name: str, trades: list[dict], equity: list[float]) -> dict:
    nets = [t["net"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    peak = equity[0] if equity else INITIAL_USDT
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    streak = 0
    max_streak = 0
    for x in nets:
        if x <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    monthly: dict[str, float] = {}
    for t in trades:
        dt = datetime.fromtimestamp(t["exit_ms"] / 1000, KST)
        key = dt.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0.0) + t["net"]
    net = sum(nets)
    return {
        "name": name,
        "trades": len(nets),
        "win_rate_pct": (len(wins) / len(nets) * 100.0) if nets else 0.0,
        "net_usdt": net,
        "net_krw": net * FX_KRW,
        "avg_win_usdt": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss_usdt": (sum(losses) / len(losses)) if losses else 0.0,
        "payoff": (sum(wins) / len(wins) / abs(sum(losses) / len(losses))) if wins and losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss else 0.0,
        "mdd_pct": max_dd * 100.0,
        "max_loss_streak": max_streak,
        "final_usdt": INITIAL_USDT + net,
        "fees_usdt": sum(t["fee"] for t in trades),
        "monthly_usdt": monthly,
    }


def main() -> None:
    rows = fetch_klines()
    feats = build_features(rows)
    first = datetime.fromtimestamp(feats["t"][0] / 1000, UTC)
    last = datetime.fromtimestamp(feats["t"][-1] / 1000, UTC)
    results = [
        run("A", feats, signal_a, compound=True),
        run("B", feats, signal_b, compound=True),
        run("C", feats, signal_c, compound=True),
    ]
    fixed = [
        run("A", feats, signal_a, compound=False),
        run("B", feats, signal_b, compound=False),
        run("C", feats, signal_c, compound=False),
    ]
    payload = {
        "source": "https://api.binance.com/api/v3/klines BTCUSDT 15m",
        "candles": len(rows),
        "fetched_utc": [first.isoformat(), last.isoformat()],
        "trade_start_utc": TRADE_START.isoformat(),
        "initial_krw": INITIAL_KRW,
        "fx_krw_per_usdt": FX_KRW,
        "initial_usdt": INITIAL_USDT,
        "leverage": LEVERAGE,
        "risk_per_trade": RISK,
        "taker_each_side": TAKER,
        "hold_bars": HOLD_BARS,
        "results_compound": results,
        "results_fixed_initial_risk": fixed,
    }
    out_path = CACHE.parent / "entry_candidates_summary.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in payload if k not in ("results_compound", "results_fixed_initial_risk")}, ensure_ascii=False))
    for key in ("results_compound", "results_fixed_initial_risk"):
        print(key)
        for row in payload[key]:
            brief = {k: row[k] for k in row if k != "monthly_usdt"}
            brief["positive_months"] = sum(1 for v in row["monthly_usdt"].values() if v > 0)
            brief["monthly_usdt"] = {m: round(v, 2) for m, v in row["monthly_usdt"].items()}
            print(json.dumps(brief, ensure_ascii=False))


if __name__ == "__main__":
    main()
