#!/usr/bin/env python3
"""호환 진입점 → legacy/kakao/auth_kakao.py"""
from pathlib import Path
import runpy

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "legacy" / "kakao" / "auth_kakao.py"),
    run_name="__main__",
)
