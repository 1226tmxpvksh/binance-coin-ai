from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)


def _sanitize_ascii(name: str, value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isascii() and ch.isprintable()).strip()
    if cleaned != value.strip():
        logger.warning(
            "%s contains non-ASCII or invisible chars; sanitized automatically. "
            "Please keep this variable in English ASCII only.",
            name,
        )
    return cleaned


def _load_env_file_safely(path: Path) -> None:
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    logger.warning("Malformed env line %d in %s", i, path)
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if not key:
                    logger.warning("Empty env key at line %d in %s", i, path)
                    continue
                os.environ.setdefault(key, value)
    except Exception as e:
        logger.error("Failed reading env file %s: %s", path, type(e).__name__)


def _build_messages(backtest_context: str, market_snapshot: Dict[str, float], similar_cases: List[Dict[str, float]]) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a crypto scalp trading assistant. "
        "You must output only valid JSON with schema: "
        '{"decision":"BUY|SELL|HOLD","reason":"string","confidence":0.0}. '
        "Decision must use both current indicators and backtest similarity evidence."
    )
    user_prompt = (
        "Context from backtest analysis:\n"
        f"{backtest_context}\n\n"
        "Current market snapshot:\n"
        f"{json.dumps(market_snapshot, ensure_ascii=False)}\n\n"
        "Most similar backtest cases:\n"
        f"{json.dumps(similar_cases, ensure_ascii=False)}\n\n"
        "Return strict JSON only."
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def _parse_decision(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"decision": "HOLD", "reason": "모델 응답 JSON 파싱 실패", "confidence": 0.0}

    decision = str(data.get("decision", "HOLD")).upper()
    if decision not in {"BUY", "SELL", "HOLD"}:
        decision = "HOLD"
    reason = str(data.get("reason", "응답 사유 없음")).strip()[:300]
    confidence = float(data.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))
    return {"decision": decision, "reason": reason, "confidence": confidence}


def get_ai_decision(
    backtest_context: str,
    market_snapshot: Dict[str, float],
    similar_cases: List[Dict[str, float]],
    model: str = "gpt-4o",
) -> Dict[str, Any]:
    base_dir = Path(__file__).resolve().parents[1]
    root_dir = base_dir.parent
    env_candidates = [base_dir / ".env", root_dir / ".env", root_dir / "btc_live_trading" / ".env"]
    for path in env_candidates:
        _load_env_file_safely(path)

    api_key_raw = os.getenv("OPENAI_API_KEY", "")
    api_key = _sanitize_ascii("OPENAI_API_KEY", api_key_raw)
    if not api_key:
        logger.error(
            "OpenAI 호출 불가: 누락 환경변수 OPENAI_API_KEY "
            "(확인 경로: %s)",
            ", ".join(str(p) for p in env_candidates),
        )
        return {
            "decision": "HOLD",
            "reason": "OPENAI_API_KEY 미설정으로 HOLD 처리",
            "confidence": 0.0,
        }

    payload = {
        "model": model,
        "messages": _build_messages(backtest_context, market_snapshot, similar_cases),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            data=json.dumps(payload),
            timeout=25,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        return _parse_decision(content)
    except requests.exceptions.HTTPError as e:
        logger.error("OpenAI request failed: %s", e)
        return {"decision": "HOLD", "reason": "OpenAI 인증/요청 실패로 HOLD 처리", "confidence": 0.0}
    except requests.RequestException as e:
        logger.error("OpenAI network error: %s", type(e).__name__)
        return {"decision": "HOLD", "reason": "OpenAI 네트워크 오류로 HOLD 처리", "confidence": 0.0}
