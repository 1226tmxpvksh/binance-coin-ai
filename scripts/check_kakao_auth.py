#!/usr/bin/env python3
"""호환 진입점 → legacy/kakao/check_kakao_auth.py"""
from pathlib import Path
import runpy

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "legacy" / "kakao" / "check_kakao_auth.py"),
    run_name="__main__",
)
