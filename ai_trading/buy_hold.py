"""
현물 보유 모드.

사이클은 이 기록이 있을 때만 평가한다. 첫 매수는 scripts/buy_hold_enter.py 가 한다.
선물 청산과 AI 판단은 호출하지 않는다.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ENTRY_PATH = ROOT / "data" / "buy_hold_entry.json"
ENV_PATH = ROOT / "btc_live_trading" / ".env"

OPEN_POSITION_BLOCK = "열린 포지션이 있습니다, 수동으로 정리 후 다시 시도하세요."
_STRATEGY_ASSIGN = re.compile(r"^([ \t]*TRADING_STRATEGY[ \t]*=)(.*)$")


def trading_strategy() -> str:
    """buy_hold 가 기본. active 만 기존 선물/AI 경로로 보낸다."""
    raw = (os.getenv("TRADING_STRATEGY") or "buy_hold").strip().lower()
    if raw == "active":
        return "active"
    return "buy_hold"


def allocation_pct() -> float:
    raw = (os.getenv("BUY_HOLD_ALLOCATION_PCT") or "100").strip()
    try:
        pct = float(raw)
    except ValueError:
        pct = 100.0
    return min(100.0, max(0.0, pct))


def quote_to_spend(free_usdt: float, pct: float) -> float:
    if free_usdt <= 0 or pct <= 0:
        return 0.0
    return free_usdt * min(100.0, pct) / 100.0


def load_entry(path: Path | None = None) -> dict[str, Any] | None:
    target = path or ENTRY_PATH
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_entry(record: dict[str, Any], path: Path | None = None) -> None:
    target = path or ENTRY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def switch_block_reason(*, check_ok: bool, open_rows: list[dict[str, Any]]) -> str | None:
    if not check_ok:
        return "선물 포지션 조회에 실패했습니다. 전환하지 않습니다."
    if open_rows:
        detail = _format_open_rows(open_rows)
        if detail:
            return f"{OPEN_POSITION_BLOCK} {detail}"
        return OPEN_POSITION_BLOCK
    return None


def enter_block_reason(
    *,
    check_ok: bool,
    open_rows: list[dict[str, Any]],
    entry_exists: bool,
) -> str | None:
    blocked = switch_block_reason(check_ok=check_ok, open_rows=open_rows)
    if blocked:
        if not check_ok:
            return "선물 포지션 조회에 실패했습니다. 매수하지 않습니다."
        return blocked
    if entry_exists:
        return "이미 진입 기록이 있습니다. 추가 매수하지 않습니다."
    return None


def replace_trading_strategy_line(value: str, *, path: Path | None = None) -> tuple[bool, str]:
    """TRADING_STRATEGY 할당 줄만 교체하거나, 없으면 파일 끝에 한 줄 추가한다."""
    mode = (value or "").strip().lower()
    if mode not in {"buy_hold", "active"}:
        return False, "TRADING_STRATEGY 값은 buy_hold 또는 active 만 허용합니다."
    target = path or ENV_PATH
    if not target.exists():
        return False, f".env 없음: {target}"
    try:
        original = target.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return False, f".env 읽기 실패: {type(exc).__name__}"
    lines = original.splitlines(keepends=True)
    found = False
    out: list[str] = []
    for line in lines:
        ending, body = _split_ending(line)
        if body.lstrip().startswith("#"):
            out.append(line)
            continue
        match = _STRATEGY_ASSIGN.match(body)
        if match and not found:
            out.append(f"{match.group(1)}{mode}{ending or chr(10)}")
            found = True
            continue
        out.append(line)
    if not found:
        if out and not str(out[-1]).endswith(("\n", "\r")):
            out[-1] = str(out[-1]) + "\n"
        out.append(f"TRADING_STRATEGY={mode}\n")
    new_text = "".join(out)
    if new_text == original:
        return True, "unchanged"
    try:
        target.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return False, f".env 쓰기 실패: {type(exc).__name__}"
    os.environ["TRADING_STRATEGY"] = mode
    return True, "updated"


def format_hold_status(entry: dict[str, Any] | None, price: float) -> str:
    """일일 리포트 본문. 수량, 현재가, 평가금액, 매수가 대비 손익률."""
    if not entry:
        return "\n".join(
            [
                "운영: 현물 보유",
                "진입 기록이 없습니다.",
                "scripts/buy_hold_enter.py 를 실행하기 전에는 매수하지 않습니다.",
            ]
        )
    qty = _float(entry.get("qty_btc"))
    entry_price = _float(entry.get("price"))
    bought = str(entry.get("bought_at_utc") or "").strip() or "-"
    mark = price if price > 0 else 0.0
    value = qty * mark
    if entry_price > 0 and mark > 0:
        pnl_pct = (mark - entry_price) / entry_price * 100.0
        pnl_line = f"매수가 대비 손익률: {pnl_pct:+.2f}%"
    else:
        pnl_line = "매수가 대비 손익률: 계산 불가"
    return "\n".join(
        [
            "운영: 현물 보유",
            f"보유 BTC 수량: {qty:.8f}",
            f"현재가: {mark:,.2f} USDT",
            f"평가금액: {value:,.2f} USDT",
            f"매수가: {entry_price:,.2f} USDT",
            f"매수 시각(UTC): {bought}",
            pnl_line,
        ]
    )


def build_entry_record(
    *,
    fill: dict[str, Any],
    allocation: float,
    bought_at_utc: datetime | None = None,
) -> dict[str, Any]:
    when = bought_at_utc or datetime.now(timezone.utc)
    return {
        "symbol": str(fill.get("symbol") or "BTCUSDT"),
        "bought_at_utc": when.astimezone(timezone.utc).isoformat(),
        "price": float(fill["price"]),
        "qty_btc": float(fill["qty_btc"]),
        "quote_usdt": float(fill["quote_usdt"]),
        "allocation_pct": float(allocation),
        "order_id": str(fill.get("order_id") or ""),
    }


def base_asset(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    if not sym.endswith("USDT") or len(sym) <= 4:
        raise ValueError(f"현물 USDT 심볼만 매수합니다: {symbol}")
    return sym[: -4]


def _format_open_rows(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in rows:
        symbol = str(row.get("symbol") or "")
        amt = row.get("positionAmt")
        if symbol:
            parts.append(f"{symbol} positionAmt={amt}")
    return ", ".join(parts)


def _split_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return "\r\n", line[:-2]
    if line.endswith("\n"):
        return "\n", line[:-1]
    if line.endswith("\r"):
        return "\r", line[:-1]
    return "", line


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
