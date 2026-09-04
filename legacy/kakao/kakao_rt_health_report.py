#!/usr/bin/env python3
"""kakao_rt_health.jsonl 에서 최근 ok → invalid_grant 전환(사망 구간)을 요약한다.

인자 없이 실행하면 가장 최근 사망 구간 1건만 출력.
로그가 없거나 사망 구간이 없으면 예외 없이 안내만 출력한다.

사용::

    python scripts/kakao_rt_health_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
JSONL_PATH = ROOT / "data" / "kakao_rt_health.jsonl"


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"로그 파일을 읽을 수 없습니다: {path} ({exc})")
        return []
    for line_no, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _find_latest_death(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """시간 순으로 스캔해 마지막 ok 직후 첫 invalid_grant 쌍을 찾는다."""
    last_ok: dict[str, Any] | None = None
    latest_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
    for row in rows:
        result = str(row.get("result") or "")
        if result == "ok":
            last_ok = row
            continue
        if result == "invalid_grant" and last_ok is not None:
            latest_pair = (last_ok, row)
            last_ok = None
    return latest_pair


def main() -> int:
    print("=== kakao_rt_health_report ===")
    print(f"로그: {JSONL_PATH}")
    rows = _load_records(JSONL_PATH)
    if not rows:
        print("아직 사망 구간 없음 - 헬스체크 로그가 비어 있거나 파일이 없습니다.")
        print("먼저: python scripts/kakao_rt_healthcheck.py")
        return 0

    print(f"기록 수: {len(rows)}")
    last = rows[-1]
    print(
        f"최근 1건: {last.get('checked_at_kst')} result={last.get('result')} "
        f"remaining_days={last.get('refresh_remaining_days')}"
    )

    pair = _find_latest_death(rows)
    if pair is None:
        print("아직 사망 구간 없음 - invalid_grant 전환이 기록되지 않았습니다.")
        oks = sum(1 for r in rows if r.get("result") == "ok")
        fails = sum(1 for r in rows if r.get("result") == "invalid_grant")
        print(f"(ok={oks}, invalid_grant={fails})")
        return 0

    ok_row, dead_row = pair
    print()
    print("--- 최근 사망 구간 (ok -> invalid_grant) ---")
    print(f"마지막 성공: {ok_row.get('checked_at_kst')}")
    print(
        f"  remaining_days(갱신 전)={ok_row.get('refresh_remaining_days')} "
        f"pid={ok_row.get('pid')} host={ok_row.get('hostname')}"
    )
    print(f"첫 실패:     {dead_row.get('checked_at_kst')}")
    print(
        f"  code={dead_row.get('error_code') or '(없음)'} "
        f"desc={dead_row.get('error_description') or '(없음)'} "
        f"http={dead_row.get('http_status')}"
    )
    print()
    print(
        "해석: 위 두 시각 사이(헬스체크 간격 이내)에 refresh_token 이 "
        "카카오 서버에서 무효화되었습니다. "
        "data/kakao_auth_events.jsonl 의 같은 구간 재인증 여부와 대조하세요."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
