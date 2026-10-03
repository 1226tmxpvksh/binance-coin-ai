"""A~I와 매수 후 보유를 같은 18개월 창에서 비교한다.

합격선은 보유 최종 잔고다. 초과수익 = 복리 최종 잔고 - 보유 최종 잔고.
main_ai는 호출하지 않는다.

후보 I의 진입은 거래 횟수만 보고 고른다. 손익이 더 나은 규칙을 고르지 않는다.
느슨한 쪽부터 거래량 배수와 돌파 계수 하한을 올리고, 체결 수가
E(184)의 절반 이하가 되는 첫 규칙을 I로 쓴다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_entry_candidates import (  # noqa: E402
    CACHE,
    INITIAL_USDT,
    signal_a,
    signal_b,
    signal_c,
)
from backtest_timeframe_candidates import (  # noqa: E402
    BAR_MS,
    FEATURE_READY,
    buy_and_hold,
    ensure_rows,
    month_counts,
    resample,
    run,
    build_features,
)

# 거래 수만 보고 좁힌다. 위가 더 느슨하다.
I_LADDER = (
    {"vol_mult": 1.5, "k_floor": 0.0},
    {"vol_mult": 1.5, "k_floor": 0.35},
    {"vol_mult": 2.0, "k_floor": 0.35},
    {"vol_mult": 2.0, "k_floor": 0.50},
    {"vol_mult": 2.0, "k_floor": 0.70},
)


def make_signal(vol_mult: float, k_floor: float):
    def _signal(feats, i):
        return signal_c(feats, i, vol_mult=vol_mult, k_floor=k_floor)

    return _signal


def pack(row: dict, bh_final: float) -> dict:
    counts = month_counts(row["monthly_usdt"])
    final = row["final_usdt"]
    return {
        "name": row["name"],
        "trades": row["trades"],
        "win_rate_pct": round(row["win_rate_pct"], 2),
        "price_pnl_usdt": round(row["net_usdt"] + row["fees_usdt"], 2),
        "fees_usdt": round(row["fees_usdt"], 2),
        "net_usdt": round(row["net_usdt"], 2),
        "final_usdt": round(final, 2),
        "excess_vs_bh_usdt": round(final - bh_final, 2),
        "profit_factor": None if row["profit_factor"] is None else round(row["profit_factor"], 3),
        "mdd_pct": round(row["mdd_pct"], 2),
        "positive_months": counts["positive_months"],
        "negative_months": counts["negative_months"],
        "months": counts["months"],
        "monthly_usdt": {m: round(v, 2) for m, v in row["monthly_usdt"].items()},
    }


def main() -> None:
    rows = ensure_rows()
    series = {}
    for label, bar_ms in BAR_MS.items():
        candles, _short = resample(rows, bar_ms) if bar_ms != 15 * 60 * 1000 else (rows, 0)
        series[label] = build_features(candles)
    bh = buy_and_hold(rows)
    bh_final = bh["final_usdt"]
    h4 = series["4h"]

    ladder = []
    chosen = None
    for step in I_LADDER:
        probe = run(
            "I",
            h4,
            make_signal(step["vol_mult"], step["k_floor"]),
            compound=True,
            min_index=FEATURE_READY,
        )
        item = {"rule": step, "trades": probe["trades"]}
        ladder.append(item)
        if chosen is None and probe["trades"] <= 184 / 2:
            chosen = step
    if chosen is None:
        chosen = I_LADDER[-1]

    jobs = [
        ("A", "15m", signal_a, dict(leverage=3, sizing="risk")),
        ("B", "15m", signal_b, dict(leverage=3, sizing="risk")),
        ("C", "15m", signal_c, dict(leverage=3, sizing="risk")),
        ("D", "1h", signal_c, dict(leverage=3, sizing="risk")),
        ("E", "4h", signal_c, dict(leverage=3, sizing="risk")),
        ("F", "1d", signal_c, dict(leverage=3, sizing="risk")),
        ("G", "4h", signal_c, dict(leverage=1, sizing="risk")),
        ("H", "4h", signal_c, dict(leverage=1, sizing="fixed_fraction", fraction=0.30)),
        ("I", "4h", make_signal(chosen["vol_mult"], chosen["k_floor"]), dict(leverage=3, sizing="risk")),
    ]
    compound = []
    fixed = []
    for name, tf, signal, opts in jobs:
        compound.append(pack(run(name, series[tf], signal, compound=True, min_index=FEATURE_READY, **opts), bh_final))
        if name != "H":
            fixed.append(pack(run(name, series[tf], signal, compound=False, min_index=FEATURE_READY, **opts), bh_final))
    bh_row = pack(bh, bh_final)
    bh_row["excess_vs_bh_usdt"] = 0.0
    payload = {
        "buy_hold_final_usdt": bh_final,
        "buy_hold_rounded_usdt": 372.4,
        "initial_usdt": INITIAL_USDT,
        "i_rule": chosen,
        "i_ladder_trade_counts": ladder,
        "compound": compound,
        "fixed_initial_risk": fixed,
        "buy_and_hold": bh_row,
        "beats_buy_hold": [row["name"] for row in compound if row["final_usdt"] > bh_final],
    }
    out = CACHE.parent / "vs_buyhold_summary.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("buy_hold_final_usdt", "i_rule", "i_ladder_trade_counts", "beats_buy_hold")}, ensure_ascii=False))
    print("compound")
    for row in compound + [bh_row]:
        brief = {k: row[k] for k in row if k != "monthly_usdt"}
        print(json.dumps(brief, ensure_ascii=False))
    print("fixed")
    for row in fixed:
        if row["name"] in {"E", "G", "I"}:
            brief = {k: row[k] for k in row if k != "monthly_usdt"}
            print(json.dumps(brief, ensure_ascii=False))


if __name__ == "__main__":
    main()
