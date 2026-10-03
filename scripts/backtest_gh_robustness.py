"""G/H가 2025-11 없이, 그리고 기간을 반으로 잘라도 보유를 이기는지 확인.

main_ai는 호출하지 않는다.
월 버킷은 청산 시각의 KST다. 기간 분할 경계는 UTC다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_entry_candidates import (  # noqa: E402
    CACHE,
    INITIAL_USDT,
    TAKER,
    signal_c,
)
from backtest_timeframe_candidates import (  # noqa: E402
    BAR_MS,
    FEATURE_READY,
    KST,
    UTC,
    build_features,
    buy_and_hold,
    ensure_rows,
    resample,
    run,
)

FIRST_START = datetime(2025, 4, 1, tzinfo=timezone.utc)
SPLIT = datetime(2026, 1, 1, tzinfo=timezone.utc)
SECOND_END = datetime(2026, 10, 1, tzinfo=timezone.utc)


def skip_november(feats, i):
    dt = datetime.fromtimestamp(feats["t"][i] / 1000, KST)
    if dt.year == 2025 and dt.month == 11:
        return None
    return signal_c(feats, i)


def hold_window(rows: list[list[float]], start_ms: int, end_ms: int | None) -> dict:
    chosen = [r for r in rows if int(r[0]) >= start_ms and (end_ms is None or int(r[0]) < end_ms)]
    entry = chosen[0][4]
    exit_px = chosen[-1][4]
    qty = INITIAL_USDT / entry
    entry_fee = entry * qty * TAKER
    exit_fee = exit_px * qty * TAKER
    gross = (exit_px - entry) * qty
    net = gross - entry_fee - exit_fee
    return {
        "entry_utc": datetime.fromtimestamp(int(chosen[0][0]) / 1000, UTC).isoformat(),
        "exit_utc": datetime.fromtimestamp(int(chosen[-1][0]) / 1000, UTC).isoformat(),
        "entry_price": entry,
        "exit_price": exit_px,
        "net_usdt": net,
        "final_usdt": INITIAL_USDT + net,
        "fees_usdt": entry_fee + exit_fee,
    }


def round_months(monthly: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 2) for k, v in monthly.items()}


def excluding(monthly: dict[str, float], drop: str) -> dict:
    kept = {k: v for k, v in monthly.items() if k != drop}
    net = sum(kept.values())
    ranked = sorted(kept.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "net_usdt": net,
        "final_usdt": INITIAL_USDT + net,
        "top3": ranked[:3],
        "bottom3": ranked[-3:],
    }


def main() -> None:
    rows = ensure_rows()
    h4, _ = resample(rows, BAR_MS["4h"])
    feats = build_features(h4)
    bh = buy_and_hold(rows)
    specs = {
        "G": dict(leverage=1, sizing="risk"),
        "H": dict(leverage=1, sizing="fixed_fraction", fraction=0.30),
    }
    full = {}
    skipped = {}
    for name, opts in specs.items():
        full[name] = run(name, feats, signal_c, compound=True, min_index=FEATURE_READY, **opts)
        skipped[name] = run(name, feats, skip_november, compound=True, min_index=FEATURE_READY, **opts)

    first_ms = int(FIRST_START.timestamp() * 1000)
    split_ms = int(SPLIT.timestamp() * 1000)
    second_end_ms = int(SECOND_END.timestamp() * 1000)
    windows = {
        "first_9m": (first_ms, split_ms),
        "second_9m": (split_ms, second_end_ms),
    }
    halves = {}
    for label, (start_ms, end_ms) in windows.items():
        hold = hold_window(rows, start_ms, end_ms)
        runs = {}
        for name, opts in specs.items():
            row = run(
                name,
                feats,
                signal_c,
                compound=True,
                min_index=FEATURE_READY,
                trade_start_ms=start_ms,
                trade_end_ms=end_ms,
                **opts,
            )
            runs[name] = {
                "trades": row["trades"],
                "net_usdt": row["net_usdt"],
                "final_usdt": row["final_usdt"],
                "win_rate_pct": row["win_rate_pct"],
                "excess_vs_window_bh": row["final_usdt"] - hold["final_usdt"],
                "beats": row["final_usdt"] > hold["final_usdt"],
            }
        halves[label] = {"buy_hold": hold, "runs": runs}

    ledger = {}
    for name, row in full.items():
        cut = excluding(row["monthly_usdt"], "2025-11")
        bh_cut_net = bh["net_usdt"] - bh["monthly_usdt"]["2025-11"]
        bh_cut_final = INITIAL_USDT + bh_cut_net
        ledger[name] = {
            "monthly_usdt": round_months(row["monthly_usdt"]),
            "full_net": row["net_usdt"],
            "full_final": row["final_usdt"],
            "nov_usdt": row["monthly_usdt"]["2025-11"],
            "ex_nov_net": cut["net_usdt"],
            "ex_nov_final": cut["final_usdt"],
            "top3": [(k, round(v, 2)) for k, v in cut["top3"]],
            "bottom3": [(k, round(v, 2)) for k, v in cut["bottom3"]],
            "beats_full_bh": cut["final_usdt"] > bh["final_usdt"],
            "beats_ex_nov_bh": cut["final_usdt"] > bh_cut_final,
            "excess_vs_full_bh": cut["final_usdt"] - bh["final_usdt"],
            "excess_vs_ex_nov_bh": cut["final_usdt"] - bh_cut_final,
        }
    bh_nov = bh["monthly_usdt"]["2025-11"]
    payload = {
        "initial_usdt": INITIAL_USDT,
        "bh_final": bh["final_usdt"],
        "bh_net": bh["net_usdt"],
        "bh_nov": bh_nov,
        "bh_ex_nov_net": bh["net_usdt"] - bh_nov,
        "bh_ex_nov_final": INITIAL_USDT + (bh["net_usdt"] - bh_nov),
        "bh_monthly": round_months(bh["monthly_usdt"]),
        "ledger_drop_nov": ledger,
        "resim_skip_nov_entries": {
            name: {
                "trades": row["trades"],
                "net_usdt": row["net_usdt"],
                "final_usdt": row["final_usdt"],
                "beats_full_bh": row["final_usdt"] > bh["final_usdt"],
                "excess_vs_full_bh": row["final_usdt"] - bh["final_usdt"],
                "monthly_usdt": round_months(row["monthly_usdt"]),
            }
            for name, row in skipped.items()
        },
        "halves": halves,
        "windows_utc": {
            "first_9m": [FIRST_START.isoformat(), SPLIT.isoformat()],
            "second_9m": [SPLIT.isoformat(), SECOND_END.isoformat()],
        },
    }
    out = CACHE.parent / "gh_robustness_summary.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
