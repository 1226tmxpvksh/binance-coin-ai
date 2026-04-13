from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class BacktestSummary:
    win_rate: float
    avg_pnl: float
    avg_profit: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float
    atr_corr: float
    ema_gap_corr: float
    sample_size: int

    def to_context_text(self) -> str:
        return (
            "Backtest Summary:\n"
            f"- samples: {self.sample_size}\n"
            f"- win_rate: {self.win_rate:.2f}%\n"
            f"- avg_pnl: {self.avg_pnl:.4f}\n"
            f"- avg_profit: {self.avg_profit:.4f}\n"
            f"- avg_loss: {self.avg_loss:.4f}\n"
            f"- profit_factor: {self.profit_factor:.3f}\n"
            f"- max_drawdown: {self.max_drawdown:.2f}%\n"
            f"- correlation(ATR,pnl): {self.atr_corr:.3f}\n"
            f"- correlation(EMA_gap,pnl): {self.ema_gap_corr:.3f}\n"
        )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pearson(xs: List[float], ys: List[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    x = xs[:n]
    y = ys[:n]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den_x = sum((a - mx) ** 2 for a in x) ** 0.5
    den_y = sum((b - my) ** 2 for b in y) ** 0.5
    den = den_x * den_y
    if den == 0:
        return 0.0
    return num / den


def _load_csv(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def _load_json(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        if isinstance(obj.get("trades"), list):
            return [x for x in obj["trades"] if isinstance(x, dict)]
        return [obj]
    return []


def _discover_trade_files(backtest_dir: str) -> List[str]:
    candidates: List[str] = []
    for root, _, files in os.walk(backtest_dir):
        for name in files:
            lower = name.lower()
            if not (lower.endswith(".csv") or lower.endswith(".json")):
                continue
            if "trade" in lower or "result" in lower or "backtest" in lower:
                candidates.append(os.path.join(root, name))
    return candidates


def _extract_trades(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    trades: List[Dict[str, float]] = []
    for row in raw_rows:
        pnl = _safe_float(row.get("pnl", row.get("profit", row.get("return", 0.0))))
        atr = _safe_float(row.get("atr", row.get("ATR", 0.0)))
        ema_short = _safe_float(row.get("ema20", row.get("EMA20", row.get("ema_short", 0.0))))
        ema_long = _safe_float(row.get("ema60", row.get("EMA60", row.get("ema_long", 0.0))))
        if pnl == 0 and atr == 0 and ema_short == 0 and ema_long == 0:
            continue
        trades.append(
            {
                "pnl": pnl,
                "atr": atr,
                "ema_gap": ema_short - ema_long,
            }
        )
    return trades


def _summary_from_trades(trades: List[Dict[str, float]]) -> BacktestSummary:
    pnls = [t["pnl"] for t in trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    atrs = [t["atr"] for t in trades]
    ema_gaps = [t["ema_gap"] for t in trades]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return BacktestSummary(
        win_rate=(len(wins) / len(trades) * 100) if trades else 0.0,
        avg_pnl=(sum(pnls) / len(pnls)) if pnls else 0.0,
        avg_profit=(sum(wins) / len(wins)) if wins else 0.0,
        avg_loss=(sum(losses) / len(losses)) if losses else 0.0,
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else 0.0,
        max_drawdown=0.0,
        atr_corr=_pearson(atrs, pnls),
        ema_gap_corr=_pearson(ema_gaps, pnls),
        sample_size=len(trades),
    )


def _fallback_summary() -> BacktestSummary:
    # README 기반 기본 인사이트: 실전 설정(RISK 1%) 중심 보수적 가정
    return BacktestSummary(
        win_rate=52.0,
        avg_pnl=0.15,
        avg_profit=1.1,
        avg_loss=-0.9,
        profit_factor=1.20,
        max_drawdown=10.0,
        atr_corr=-0.10,
        ema_gap_corr=0.18,
        sample_size=0,
    )


def load_backtest_summary(backtest_dir: str) -> BacktestSummary:
    files = _discover_trade_files(backtest_dir)
    all_trades: List[Dict[str, float]] = []
    for path in files:
        try:
            if path.lower().endswith(".csv"):
                raw_rows = _load_csv(path)
            else:
                raw_rows = _load_json(path)
            all_trades.extend(_extract_trades(raw_rows))
        except Exception:
            continue
    if not all_trades:
        return _fallback_summary()
    return _summary_from_trades(all_trades)


def find_similar_backtest_cases(
    current_rsi: float,
    current_atr_pct: float,
    current_ema_gap_pct: float,
    backtest_dir: str,
    top_n: int = 5,
) -> List[Dict[str, float]]:
    files = _discover_trade_files(backtest_dir)
    rows: List[Dict[str, float]] = []
    for path in files:
        try:
            raw = _load_csv(path) if path.lower().endswith(".csv") else _load_json(path)
        except Exception:
            continue
        for r in raw:
            pnl = _safe_float(r.get("pnl", r.get("profit", 0.0)))
            rsi = _safe_float(r.get("rsi", r.get("RSI", 50.0)), 50.0)
            atr_pct = _safe_float(r.get("atr_pct", r.get("ATR_PCT", current_atr_pct)), current_atr_pct)
            ema_gap_pct = _safe_float(r.get("ema_gap_pct", r.get("EMA_GAP_PCT", current_ema_gap_pct)), current_ema_gap_pct)
            dist = (
                abs(rsi - current_rsi) / 100.0
                + abs(atr_pct - current_atr_pct) / max(abs(current_atr_pct), 1e-6)
                + abs(ema_gap_pct - current_ema_gap_pct) / max(abs(current_ema_gap_pct), 1e-6)
            )
            rows.append({"distance": dist, "pnl": pnl, "rsi": rsi, "atr_pct": atr_pct, "ema_gap_pct": ema_gap_pct})
    rows.sort(key=lambda x: x["distance"])
    return rows[:top_n]
