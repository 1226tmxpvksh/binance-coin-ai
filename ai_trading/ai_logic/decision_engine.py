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


def _build_messages(
    backtest_context: str,
    market_snapshot: Dict[str, float],
    similar_cases: List[Dict[str, float]],
    failure_memory: str = "",
    reflection_digest: str = "",
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a crypto scalp trading assistant. "
        "You must output only valid JSON with schema: "
        '{"decision":"BUY|SELL|HOLD","reason":"string","confidence":0.0}. '
        "Prioritize capital preservation: if recent loss reflections or similar past failures suggest "
        "indicator-only entries failed, prefer HOLD or very low confidence until multiple signals align. "
        "The reason field must be written in Korean for a human operator."
    )
    preamble_parts: List[str] = []
    if reflection_digest.strip():
        preamble_parts.append(
            "=== 절대 반복하지 말아야 할 실수 (최근 손실 반성 요약) ===\n"
            + reflection_digest.strip()
        )
    if failure_memory.strip():
        preamble_parts.append(
            "=== 현재 차트와 유사한 과거 실패 사례(시그니처 매칭) ===\n" + failure_memory.strip()
        )
    preamble = ("\n\n".join(preamble_parts) + "\n\n") if preamble_parts else ""
    compact_snapshot = {
        "price": market_snapshot.get("price"),
        "rsi": market_snapshot.get("rsi"),
        "ema20": market_snapshot.get("ema20"),
        "ema60": market_snapshot.get("ema60"),
        "ema_gap_pct": market_snapshot.get("ema_gap_pct"),
        "bb_position": market_snapshot.get("bb_position"),
        "atr_pct": market_snapshot.get("atr_pct"),
    }
    compact_cases = []
    for case in similar_cases[:3]:
        compact_cases.append(
            {
                "side": case.get("side"),
                "pnl": case.get("pnl"),
                "rsi": case.get("rsi"),
                "ema_gap_pct": case.get("ema_gap_pct"),
                "atr_pct": case.get("atr_pct"),
            }
        )
    user_prompt = (
        f"{preamble}"
        "Backtest context(summary):\n"
        f"{backtest_context[:700]}\n\n"
        "Snapshot JSON:\n"
        f"{json.dumps(compact_snapshot, ensure_ascii=False, separators=(',', ':'))}\n"
        "Similar cases JSON:\n"
        f"{json.dumps(compact_cases, ensure_ascii=False, separators=(',', ':'))}\n"
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


def _load_openai_api_key() -> str:
    base_dir = Path(__file__).resolve().parents[1]
    root_dir = base_dir.parent
    env_candidates = [base_dir / ".env", root_dir / ".env", root_dir / "btc_live_trading" / ".env"]
    for path in env_candidates:
        _load_env_file_safely(path)
    api_key_raw = os.getenv("OPENAI_API_KEY", "")
    return _sanitize_ascii("OPENAI_API_KEY", api_key_raw)


def _call_openai_json(messages: List[Dict[str, str]], model: str, timeout: int = 25) -> Dict[str, Any]:
    api_key = _load_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        data=json.dumps(payload),
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    return json.loads(content)


def get_ai_decision(
    backtest_context: str,
    market_snapshot: Dict[str, float],
    similar_cases: List[Dict[str, float]],
    model: str = "gpt-4o",
    failure_memory: str = "",
    reflection_digest: str = "",
) -> Dict[str, Any]:
    api_key = _load_openai_api_key()
    if not api_key:
        logger.error(
            "OpenAI 호출 불가: 누락 환경변수 OPENAI_API_KEY"
        )
        return {
            "decision": "HOLD",
            "reason": "OPENAI_API_KEY 미설정으로 HOLD 처리",
            "confidence": 0.0,
        }

    try:
        data = _call_openai_json(
            _build_messages(
                backtest_context,
                market_snapshot,
                similar_cases,
                failure_memory,
                reflection_digest,
            ),
            model=model,
            timeout=25,
        )
        return _parse_decision(json.dumps(data, ensure_ascii=False))
    except requests.exceptions.HTTPError as e:
        logger.error("OpenAI request failed: %s", e)
        return {"decision": "HOLD", "reason": "OpenAI 인증/요청 실패로 HOLD 처리", "confidence": 0.0}
    except requests.RequestException as e:
        logger.error("OpenAI network error: %s", type(e).__name__)
        return {"decision": "HOLD", "reason": "OpenAI 네트워크 오류로 HOLD 처리", "confidence": 0.0}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.error("OpenAI invalid JSON response: %s", type(e).__name__)
        return {"decision": "HOLD", "reason": "모델 응답 파싱 실패로 HOLD 처리", "confidence": 0.0}
    except RuntimeError:
        return {"decision": "HOLD", "reason": "OPENAI_API_KEY 미설정으로 HOLD 처리", "confidence": 0.0}


def get_market_monitor_summary(
    market_snapshot: Dict[str, float],
    model: str = "gpt-4o-mini",
) -> Dict[str, str]:
    api_key = _load_openai_api_key()
    if not api_key:
        return {"summary": "OPENAI_API_KEY 미설정으로 시장 요약 생략", "model": model}

    system_prompt = (
        "You are a crypto monitoring assistant. "
        "Return strict JSON only with keys: summary, tone. "
        "summary must be Korean and <= 90 chars."
    )
    compact_snapshot = {
        "price": market_snapshot.get("price"),
        "rsi": market_snapshot.get("rsi"),
        "ema_gap_pct": market_snapshot.get("ema_gap_pct"),
        "bb_position": market_snapshot.get("bb_position"),
        "atr_pct": market_snapshot.get("atr_pct"),
    }
    user_prompt = (
        "Market snapshot JSON:\n"
        f"{json.dumps(compact_snapshot, ensure_ascii=False, separators=(',', ':'))}"
    )
    try:
        data = _call_openai_json(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            model=model,
            timeout=20,
        )
        summary = str(data.get("summary", "")).strip()
        if not summary:
            summary = "시장 요약 생성 실패"
        return {"summary": summary[:90], "model": model}
    except Exception as e:
        logger.warning("Market monitor summary fallback used: %s", type(e).__name__)
        return {"summary": "시장 요약 생성 실패(로컬 모니터링 유지)", "model": model}


def analyze_trade_failure(
    entry_snapshot: Dict[str, Any],
    exit_snapshot: Dict[str, Any],
    trade_context: Dict[str, Any],
    model: str = "gpt-4o",
) -> Dict[str, str]:
    fallback_reason = "시간 청산 결과 예측 방향과 실제 가격 흐름이 어긋남"
    fallback_summary = "추세 확인이 약한 상태에서 진입해 반대 방향 움직임을 허용함"
    price_move_pct = float(trade_context.get("price_move_pct", 0.0))
    side = str(trade_context.get("side", "HOLD")).upper()
    system_prompt = (
        "You are a trading post-mortem analyst. "
        "Return strict JSON only with keys: market_context, failure_reason, reflection_summary, warning. "
        "All values must be written in Korean."
    )
    user_prompt = (
        "Analyze why this virtual scalp trade failed or underperformed.\n\n"
        f"Trade context:\n{json.dumps(trade_context, ensure_ascii=False)}\n\n"
        f"Entry snapshot:\n{json.dumps(entry_snapshot, ensure_ascii=False)}\n\n"
        f"Exit snapshot:\n{json.dumps(exit_snapshot, ensure_ascii=False)}\n\n"
        "Focus on market structure, volatility, trend mismatch, and overfitting to a single indicator. "
        "Write concise Korean sentences for a KakaoTalk report."
    )
    try:
        data = _call_openai_json(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            model=model,
            timeout=30,
        )
        return {
            "market_context": str(data.get("market_context", "")).strip()[:280] or "시장 컨텍스트 분석 실패",
            "failure_reason": str(data.get("failure_reason", "")).strip()[:280] or fallback_reason,
            "reflection_summary": str(data.get("reflection_summary", "")).strip()[:280] or fallback_summary,
            "warning": str(data.get("warning", "")).strip()[:280]
            or "다음 유사 상황에서는 추세와 변동성 확인 후 진입 여부를 재검토할 것",
        }
    except Exception as e:
        logger.warning("Failure reflection fallback used: %s", type(e).__name__)
        direction_text = "상승" if price_move_pct > 0 else "하락" if price_move_pct < 0 else "횡보"
        return {
            "market_context": (
                f"{side} 판단 후 15분 동안 가격이 {price_move_pct:.2f}% {direction_text}했고 "
                f"RSI={float(entry_snapshot.get('rsi', 0.0)):.1f}, "
                f"BB={float(entry_snapshot.get('bb_position', 0.0)):.2f}"
            ),
            "failure_reason": fallback_reason,
            "reflection_summary": fallback_summary,
            "warning": "단일 지표보다 추세 방향과 변동성 확장 여부를 함께 확인할 것",
        }
