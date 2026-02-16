from __future__ import annotations

import json
from typing import Any

from requests.exceptions import RequestException

from app.models.trade_contract import TradeContract


class LocalModelUnavailableError(RuntimeError):
    pass


def _extract_json_payload(raw_text: str) -> Any:
    text = str(raw_text or "").strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        pass

    start_idx = None
    for char in ("{", "["):
        idx = text.find(char)
        if idx != -1:
            start_idx = idx
            break
    if start_idx is None:
        return None

    open_char = text[start_idx]
    close_char = "}" if open_char == "{" else "]"
    end_idx = text.rfind(close_char)
    if end_idx == -1:
        return None

    try:
        return json.loads(text[start_idx : end_idx + 1])
    except Exception:
        return None


def _coerce_stock_model_output(candidate: Any) -> dict[str, Any] | None:
    if isinstance(candidate, list) and candidate:
        first = candidate[0]
        if isinstance(first, dict):
            candidate = first
    if not isinstance(candidate, dict):
        return None

    recommendation = str(candidate.get("recommendation") or "WAIT").strip().upper()
    if recommendation not in {"BUY", "SELL", "WAIT"}:
        recommendation = "WAIT"

    confidence_raw = candidate.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.35
    confidence = max(0.0, min(confidence, 1.0))

    summary = str(candidate.get("summary") or "Model returned no summary.").strip()
    key_factors = candidate.get("key_factors") if isinstance(candidate.get("key_factors"), list) else []
    risks = candidate.get("risks") if isinstance(candidate.get("risks"), list) else []
    time_horizon = str(candidate.get("time_horizon") or "1W").strip().upper()
    if time_horizon not in {"1D", "1W", "1M"}:
        time_horizon = "1W"

    trade_ideas_raw = candidate.get("trade_ideas") if isinstance(candidate.get("trade_ideas"), list) else []
    trade_ideas: list[dict[str, Any]] = []
    for row in trade_ideas_raw:
        if isinstance(row, dict):
            trade_ideas.append(dict(row))

    return {
        "recommendation": recommendation,
        "confidence": confidence,
        "summary": summary,
        "key_factors": [str(item) for item in key_factors if str(item or "").strip()],
        "risks": [str(item) for item in risks if str(item or "").strip()],
        "time_horizon": time_horizon,
        "trade_ideas": trade_ideas,
    }


def analyze_trade(
    trade: TradeContract,
    source: str,
    model_url: str = "http://localhost:1234/v1/chat/completions",
    retries: int = 2,
    timeout: int = 30,
) -> dict[str, Any] | None:
    # Keep the legacy JSON contract exactly the same by delegating to the legacy implementation.
    # TODO(architecture): migrate implementation from common.utils into this module and delete legacy shim.
    from common import utils as legacy_utils

    return legacy_utils._analyze_trade_with_model_legacy(
        trade.to_dict(),
        source,
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )


def analyze_stock_idea(
    *,
    symbol: str,
    idea: dict[str, Any],
    source: str,
    model_url: str = "http://localhost:1234/v1/chat/completions",
    retries: int = 1,
    timeout: int = 30,
) -> dict[str, Any]:
    from common import utils as legacy_utils

    prompt = (
        "You are a stock swing-trade analysis assistant.\n"
        "You will receive exactly one stock idea snapshot as JSON.\n"
        "Return valid JSON only (no markdown) with exactly these keys:\n"
        "recommendation (BUY|SELL|WAIT), confidence (0..1), summary (string),\n"
        "key_factors (string array), risks (string array), time_horizon (1D|1W|1M),\n"
        "trade_ideas (array of objects).\n"
        "trade_ideas may include stock action ideas and options strategy ideas.\n"
        "Do not include any keys beyond the required schema."
    )

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbol": str(symbol or "").upper(),
                        "source": str(source or "local_llm"),
                        "idea": idea,
                    },
                    ensure_ascii=False,
                    indent=None,
                ),
            },
        ],
        "max_tokens": 1800,
        "temperature": 0.0,
    }

    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            response = legacy_utils.requests.post(model_url, json=payload, timeout=timeout)
            response.raise_for_status()

            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            assistant_text = None
            if isinstance(response_json, dict):
                choices = response_json.get("choices") or []
                if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                    first = choices[0]
                    message = first.get("message")
                    if isinstance(message, dict) and "content" in message:
                        assistant_text = message.get("content")
                    elif "text" in first:
                        assistant_text = first.get("text")
            if assistant_text is None:
                assistant_text = getattr(response, "text", "")

            parsed = _extract_json_payload(assistant_text)
            normalized = _coerce_stock_model_output(parsed)
            if normalized is None:
                raise ValueError("Model returned invalid stock analysis payload")
            return normalized
        except RequestException as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break

    if isinstance(last_error, RequestException):
        raise LocalModelUnavailableError(f"Local model endpoint unavailable at {model_url}: {last_error}") from last_error
    raise RuntimeError(f"Stock model analysis failed: {last_error}")
