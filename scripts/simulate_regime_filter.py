"""최근 손실 거래에 market_regime + EMA 갭 최소값 필터를 소급 적용하는 시뮬레이션.

실제 매매 로직(main_ai.py 판단)은 변경하지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except OSError:
        pass
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "btc_live_trading"))

from btc_live_trading.strategy.market_regime import (  # noqa: E402
    MarketState,
    determine_market_state,
    is_long_allowed,
    is_short_allowed,
)

LOG_CANDIDATES = [
    ROOT / "ai_trading" / "data" / "ai_learning_logs.csv",
    ROOT / "btc_live_trading" / "ai_learning_logs.csv",
    ROOT / "ai_learning_logs.csv",
]
RECENT_LOSSES = 18
DEFAULT_MIN_GAP_PCT = 0.3

_SIG_RE = re.compile(r"(?:^|\|)([^=]+)=([^|]*)")
_GAP_RES = [
    re.compile(r"EMA\s*갭(?:이|은)?\s*([+-]?\d+(?:\.\d+)?)\s*%", re.I),
    re.compile(r"EMA갭\s*([+-]?\d+(?:\.\d+)?)\s*%", re.I),
    re.compile(
        r"EMA(?:20)?[^%]{0,24}EMA(?:60)?[^0-9+\-]{0,16}([+-]?\d+(?:\.\d+)?)\s*%",
        re.I,
    ),
    re.compile(r"갭(?:이|은)?\s*([+-]?\d+(?:\.\d+)?)\s*%", re.I),
]
_ATR_RE = re.compile(r"ATR(?:은|이)?\s*([+-]?\d+(?:\.\d+)?)\s*%", re.I)
_RSI_RE = re.compile(r"RSI(?:는|가)?\s*([+-]?\d+(?:\.\d+)?)", re.I)


@dataclass
class LossTrade:
    trade_id: str
    entry_at: str
    side: str
    entry_price: float
    pnl_krw: float
    pnl_usdt: float
    trend: str
    rsi: float
    atr_pct: float
    ema_gap_pct: float
    gap_source: str


@dataclass
class SimRow:
    trade: LossTrade
    state: MarketState
    blocked: bool
    block_kind: str  # weak_gap / ranging / side_mismatch / allowed
    detail: str


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _parse_signature(raw: str) -> dict[str, str]:
    return {m.group(1).strip(): m.group(2).strip() for m in _SIG_RE.finditer(raw or "")}


def _first_float(patterns: list[re.Pattern[str]], text: str) -> float | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return _to_float(m.group(1))
    return None


def _resolve_log_path() -> Path:
    existing = [p for p in LOG_CANDIDATES if p.exists()]
    if not existing:
        raise FileNotFoundError(
            "ai_learning_logs.csv 를 찾지 못했습니다. 확인 경로: "
            + ", ".join(str(p) for p in LOG_CANDIDATES)
        )
    return max(existing, key=lambda p: (p.stat().st_size, p.stat().st_mtime))


def _load_recent_losses(path: Path, limit: int = RECENT_LOSSES) -> list[LossTrade]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    losses: list[LossTrade] = []
    for row in rows:
        pnl_krw = _to_float(row.get("pnl_krw"))
        pnl_usdt = _to_float(row.get("pnl_usdt"))
        if pnl_krw >= 0 and pnl_usdt >= 0:
            continue
        sig = _parse_signature(row.get("market_signature") or "")
        # 진입 시점 수치는 market_context만 쓴다. failure_reason에는 청산 후 갭이 섞인다.
        context = row.get("market_context") or ""
        sig_gap = _to_float(sig.get("ema_gap_pct"))
        ctx_gap = _first_float(_GAP_RES, context)
        if ctx_gap is not None:
            ema_gap, gap_source = ctx_gap, "context"
        else:
            ema_gap, gap_source = sig_gap, "signature"
        ctx_atr = _first_float([_ATR_RE], context)
        ctx_rsi = _first_float([_RSI_RE], context)
        losses.append(
            LossTrade(
                trade_id=str(row.get("trade_id") or ""),
                entry_at=str(row.get("entry_at_kst") or "")[:19],
                side=str(row.get("side") or "").upper(),
                entry_price=_to_float(row.get("entry_price")),
                pnl_krw=pnl_krw,
                pnl_usdt=pnl_usdt,
                trend=sig.get("trend") or "",
                rsi=ctx_rsi if ctx_rsi is not None else _to_float(sig.get("rsi"), 50.0),
                atr_pct=ctx_atr if ctx_atr is not None else _to_float(sig.get("atr_pct")),
                ema_gap_pct=ema_gap,
                gap_source=gap_source,
            )
        )
    return losses[-limit:]


def _emas_from_gap(close: float, ema_gap_pct: float) -> tuple[float, float]:
    """부호만 맞으면 된다: ema_short > ema_long  <=>  gap > 0."""
    ema_long = close if close > 0 else 1.0
    ema_short = ema_long * (1.0 + ema_gap_pct / 100.0)
    return ema_short, ema_long


def _simulate(
    trade: LossTrade,
    *,
    assume_bear_on_negative_gap: bool,
    min_gap_pct: float = 0.0,
) -> SimRow:
    close = trade.entry_price if trade.entry_price > 0 else 1.0
    ema_short, ema_long = _emas_from_gap(close, trade.ema_gap_pct)
    if assume_bear_on_negative_gap and trade.ema_gap_pct < 0:
        sma_long = close + 1.0
        sma_slope = -1.0
        sma_note = "SMA200 하락 가정"
    else:
        sma_long = close
        sma_slope = 0.0
        sma_note = "SMA200 미기록→BEAR 불가"

    state = determine_market_state(close, ema_short, ema_long, sma_long, sma_slope)
    if min_gap_pct > 0 and abs(trade.ema_gap_pct) < min_gap_pct:
        return SimRow(
            trade=trade,
            state=state,
            blocked=True,
            block_kind="weak_gap",
            detail=(
                f"|EMA갭| {abs(trade.ema_gap_pct):.3f}% < {min_gap_pct:.2f}% "
                f"(국면은 {state.value})"
            ),
        )

    long_ok = is_long_allowed(state)
    short_ok = is_short_allowed(state)
    if trade.side == "BUY":
        allowed = long_ok
    elif trade.side == "SELL":
        allowed = short_ok
    else:
        allowed = False

    if state == MarketState.NEUTRAL:
        kind = "ranging"
        detail = f"NEUTRAL({sma_note}) — 거래 금지"
    elif not allowed:
        kind = "side_mismatch"
        detail = f"{state.value} — {trade.side} 불허"
    else:
        kind = "allowed"
        detail = f"{state.value} — {trade.side} 허용"

    return SimRow(trade=trade, state=state, blocked=not allowed, block_kind=kind, detail=detail)


def _fmt_krw(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}{abs(value):,.0f}"


def _print_table(title: str, rows: list[SimRow]) -> None:
    print()
    print(title)
    headers = [
        "#",
        "entry_at",
        "side",
        "ema_gap%",
        "atr%",
        "rsi",
        "state",
        "차단",
        "구분",
        "pnl_krw",
    ]
    body: list[list[str]] = []
    for i, row in enumerate(rows, 1):
        t = row.trade
        body.append(
            [
                str(i),
                t.entry_at.replace("T", " "),
                t.side,
                f"{t.ema_gap_pct:+.3f}",
                f"{t.atr_pct:.2f}",
                f"{t.rsi:.1f}",
                row.state.value,
                "YES" if row.blocked else "no",
                {"ranging": "횡보", "side_mismatch": "방향불일치", "allowed": "통과", "weak_gap": "갭부족"}[row.block_kind],
                _fmt_krw(t.pnl_krw),
            ]
        )
    widths = [max(len(headers[c]), *(len(r[c]) for r in body)) for c in range(len(headers))]
    line = "-+-".join("-" * w for w in widths)
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print(line)
    for r in body:
        print(" | ".join(c.rjust(w) if i != 1 else c.ljust(w) for i, (c, w) in enumerate(zip(r, widths))))


def _summarize(label: str, rows: list[SimRow]) -> None:
    total_n = len(rows)
    total_loss = sum(r.trade.pnl_krw for r in rows)
    ranging = [r for r in rows if r.block_kind == "ranging"]
    mismatch = [r for r in rows if r.block_kind == "side_mismatch"]
    weak_gap = [r for r in rows if r.block_kind == "weak_gap"]
    blocked = [r for r in rows if r.blocked]
    ranging_loss = sum(r.trade.pnl_krw for r in ranging)
    weak_gap_loss = sum(r.trade.pnl_krw for r in weak_gap)
    blocked_loss = sum(r.trade.pnl_krw for r in blocked)

    def pct_n(part: list[SimRow]) -> str:
        return f"{len(part)}/{total_n} ({(100.0 * len(part) / total_n) if total_n else 0:.1f}%)"

    def pct_krw(part_loss: float) -> str:
        if total_loss == 0:
            return "n/a"
        return f"{_fmt_krw(part_loss)} / {_fmt_krw(total_loss)} ({100.0 * part_loss / total_loss:.1f}%)"

    print()
    print(f"[요약] {label}")
    print(f"  갭 부족 차단           : {pct_n(weak_gap)}")
    print(f"  갭 부족 차단 손실 비중 : {pct_krw(weak_gap_loss)}")
    print(f"  횡보(NEUTRAL) 차단     : {pct_n(ranging)}")
    print(f"  횡보 차단 손실 비중    : {pct_krw(ranging_loss)}")
    print(f"  방향 불일치 차단       : {pct_n(mismatch)}")
    print(f"  진입 자체 차단(합계)   : {pct_n(blocked)}")
    print(f"  차단됐을 손실 합 비중  : {pct_krw(blocked_loss)}")


def _print_logic(min_gap_pct: float) -> None:
    print("=== market_regime 판단 기준 (코드 그대로, 실거래 미반영) ===")
    print("  BULL    : EMA20 > EMA60          → LONG만 허용  (갭 최소값 없음)")
    print("  BEAR    : close < SMA200 AND slope < 0 → SHORT만 허용")
    print("  NEUTRAL : 그 외                  → 거래 금지")
    print()
    print(f"=== 추가 시뮬레이션 조건 (실거래 미반영) ===")
    print(f"  |ema_gap_pct| >= {min_gap_pct:.2f}% 가 아니면 약추세/횡보로 차단")
    print("  기존 로그에는 SMA200이 없어 A/B 가정을 유지한다.")


def main() -> int:
    parser = argparse.ArgumentParser(description="market_regime + EMA 갭 최소값 소급 시뮬레이션")
    parser.add_argument(
        "--min-gap",
        type=float,
        default=DEFAULT_MIN_GAP_PCT,
        help=f"차단 기준 |ema_gap_pct| (기본 {DEFAULT_MIN_GAP_PCT})",
    )
    args = parser.parse_args()
    min_gap = float(args.min_gap)

    log_path = _resolve_log_path()
    trades = _load_recent_losses(log_path)
    if not trades:
        print(f"손실 행이 없습니다: {log_path}")
        return 1

    print("=== simulate_regime_filter ===")
    print(f"로그: {log_path}")
    print(f"최근 손실 {len(trades)}건  |  총 손실 {_fmt_krw(sum(t.pnl_krw for t in trades))} KRW")
    print()
    _print_logic(min_gap)

    rows_a = [_simulate(t, assume_bear_on_negative_gap=False) for t in trades]
    rows_b = [_simulate(t, assume_bear_on_negative_gap=True) for t in trades]
    rows_c = [_simulate(t, assume_bear_on_negative_gap=True, min_gap_pct=min_gap) for t in trades]
    rows_ac = [_simulate(t, assume_bear_on_negative_gap=False, min_gap_pct=min_gap) for t in trades]

    _print_table(
        f"=== 시나리오 C: |EMA갭| < {min_gap:.2f}% 차단 + BEAR 가정 (모듈에 갭 최소값을 붙인 경우) ===",
        rows_c,
    )
    _summarize(f"시나리오 C (B + 갭{min_gap:.2f}%)", rows_c)
    _summarize(f"시나리오 A+C (SMA 미기록 + 갭{min_gap:.2f}%)", rows_ac)
    _summarize("시나리오 A (참고, 갭 조건 없음)", rows_a)
    _summarize("시나리오 B (참고, 갭 조건 없음)", rows_b)
    print()
    print("실거래 판단 로직은 변경하지 않았다. 위 숫자는 소급 시뮬레이션이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
