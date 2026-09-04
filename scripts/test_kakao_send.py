#!/usr/bin/env python3
"""호환 진입점 → legacy/kakao/test_kakao_send.py"""
from pathlib import Path
import runpy

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "legacy" / "kakao" / "test_kakao_send.py"),
    run_name="__main__",
)
