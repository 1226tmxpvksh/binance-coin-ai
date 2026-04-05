"""
GPT-4o 기반 매수 컨펌. 장애 시 fail_closed (진입 취소).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


def _load_dotenv_btc_live_trading() -> None:
    """
    btc_live_trading/.env 를 CWD 와 무관하게 로드.
    이미 설정된 환경변수는 override 하지 않음 (python-dotenv 기본).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    pkg_root = Path(__file__).resolve().parent.parent
    env_pkg = pkg_root / ".env"
    if env_pkg.is_file():
        load_dotenv(dotenv_path=env_pkg, override=False)
    load_dotenv(override=False)

SYSTEM_PROMPT = """You are a conservative crypto futures trading assistant.
Given market indicator summary in Korean/English, decide ONLY whether to approve a LONG (buy) scalp entry right now.

Respond with a single JSON object, no markdown:
{"approve": true or false, "reason": "short Korean explanation"}

Approve only if indicators suggest favorable long risk/reward; otherwise reject.
If data is incomplete or ambiguous, reject (approve=false)."""


class AiAdvisor:
    """OpenAI Chat Completions (gpt-4o). API 실패·비정상 응답 시 approve=False."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        timeout_sec: float = 45.0,
    ):
        _load_dotenv_btc_live_trading()
        key = (api_key or "").strip()
        if not key:
            key = os.getenv("OPENAI_API_KEY", "").strip()
        self.api_key = key
        m = (model or "").strip()
        if not m:
            m = os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"
        self.model = m
        self.timeout_sec = timeout_sec

    def confirm_long_entry(self, indicator_summary: str) -> Tuple[bool, str]:
        """
        Returns:
            (approved, reason). 장애 시 항상 (False, ...) — fail_closed.
        """
        if not self.api_key:
            logger.error("AI 컨펌 실패(fail_closed): OPENAI_API_KEY 없음")
            return False, "API 키 없음"

        try:
            from openai import OpenAI
        except ImportError:
            logger.error("AI 컨펌 실패(fail_closed): openai 패키지 미설치")
            return False, "openai 패키지 미설치"

        client = OpenAI(api_key=self.api_key, timeout=self.timeout_sec)
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": "다음 시장 요약을 보고 LONG 진입을 승인할지 JSON만 답하세요.\n\n"
                        + indicator_summary,
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        except Exception as e:
            logger.warning(
                "AI API 호출 실패(fail_closed): %s", type(e).__name__, exc_info=True
            )
            return False, f"API 오류: {type(e).__name__}"

        try:
            text = (resp.choices[0].message.content or "").strip()
            data = json.loads(text)
            approve = bool(data.get("approve", False))
            reason = str(data.get("reason", ""))[:500]
            if not approve:
                logger.info("AI 거절: %s", reason)
            else:
                logger.info("AI 승인: %s", reason)
            return approve, reason or ("승인" if approve else "거절")
        except Exception as e:
            logger.warning(
                "AI 응답 파싱 실패(fail_closed): %s", type(e).__name__, exc_info=True
            )
            return False, f"응답 파싱 실패: {type(e).__name__}"


def advisor_from_env() -> AiAdvisor:
    _load_dotenv_btc_live_trading()
    return AiAdvisor(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
    )
