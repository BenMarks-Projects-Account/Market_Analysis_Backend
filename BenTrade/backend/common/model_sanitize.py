"""Centralized model response sanitization for all LLM call paths.

Every model response should pass through ``sanitize_model_text()`` before
JSON extraction or rendering.  This module strips chain-of-thought
traces, reasoning tags, and other artefacts that local models (LM Studio,
llama.cpp, etc.) may emit.

Usage
-----
    from common.model_sanitize import sanitize_model_text, classify_model_error

    clean = sanitize_model_text(raw_assistant_text)
    # then feed *clean* into JSON extraction / rendering

Error classification:

    from common.model_sanitize import classify_model_error

    kind = classify_model_error(exception)
    # returns one of: "timeout", "unreachable", "empty_response", …
"""

from __future__ import annotations

import re
from typing import Any

# ── Think/reasoning tag patterns ────────────────────────────────
_THINK_CLOSED = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_UNCLOSED = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)
_SCRATCHPAD_CLOSED = re.compile(r"<scratchpad>.*?</scratchpad>", re.DOTALL | re.IGNORECASE)
_SCRATCHPAD_UNCLOSED = re.compile(r"<scratchpad>.*$", re.DOTALL | re.IGNORECASE)
_STRAY_TAGS = re.compile(
    r"</?(?:think|scratchpad|reasoning|thought|internal|reflection)>",
    re.IGNORECASE,
)


def sanitize_model_text(text: str | None) -> str:
    """Strip chain-of-thought / reasoning traces from raw LLM output.

    Safe to call on any string — returns empty string for None/empty input.
    """
    if not text:
        return ""
    t = str(text)
    t = _THINK_CLOSED.sub("", t)
    t = _THINK_UNCLOSED.sub("", t)
    t = _SCRATCHPAD_CLOSED.sub("", t)
    t = _SCRATCHPAD_UNCLOSED.sub("", t)
    t = _STRAY_TAGS.sub("", t)
    return t.strip()


def had_think_tags(text: str | None) -> bool:
    """Return True if the text contained <think> or similar reasoning tags."""
    if not text:
        return False
    return bool(
        _THINK_CLOSED.search(text)
        or _THINK_UNCLOSED.search(text)
        or _SCRATCHPAD_CLOSED.search(text)
    )


# ── Error classification ────────────────────────────────────────

_ERROR_KINDS = (
    "timeout",
    "unreachable",
    "empty_response",
    "malformed_response",
    "parse_failure",
    "schema_mismatch",
    "model_unavailable",
    "unknown",
)


def classify_model_error(exc: Exception | None) -> str:
    """Return a stable error kind string from an exception.

    Inspects exception type/message and returns one of:
      timeout, unreachable, empty_response, malformed_response,
      parse_failure, schema_mismatch, model_unavailable, unknown
    """
    if exc is None:
        return "unknown"

    exc_type = type(exc).__name__
    exc_msg = str(exc).lower()

    # Timeout
    try:
        import requests as _requests
        if isinstance(exc, (_requests.exceptions.ReadTimeout, _requests.exceptions.ConnectTimeout)):
            return "timeout"
    except ImportError:
        pass
    try:
        import httpx as _httpx
        if isinstance(exc, (_httpx.ReadTimeout, _httpx.ConnectTimeout)):
            return "timeout"
    except ImportError:
        pass
    if "timeout" in exc_msg or "timed out" in exc_msg:
        return "timeout"

    # Unreachable
    try:
        import requests as _requests
        if isinstance(exc, _requests.exceptions.ConnectionError):
            return "unreachable"
    except ImportError:
        pass
    try:
        import httpx as _httpx
        if isinstance(exc, _httpx.ConnectError):
            return "unreachable"
    except ImportError:
        pass
    if "connection" in exc_msg and ("refused" in exc_msg or "error" in exc_msg):
        return "unreachable"

    # Model unavailable
    if "unavailable" in exc_msg or "not enabled" in exc_msg:
        return "model_unavailable"

    # Parse / schema
    if "json" in exc_msg and ("decode" in exc_msg or "parse" in exc_msg):
        return "parse_failure"
    if "schema" in exc_msg or "validation" in exc_msg:
        return "schema_mismatch"
    if "invalid" in exc_msg and ("payload" in exc_msg or "response" in exc_msg):
        return "malformed_response"
    if "empty" in exc_msg or "no result" in exc_msg:
        return "empty_response"

    return "unknown"


def user_facing_error_message(error_kind: str, *, timeout_seconds: float = 90) -> str:
    """Return a clean, accurate, user-friendly error message for a classified error."""
    messages = {
        "timeout": f"Model request timed out after {int(timeout_seconds)}s. The local LLM may be overloaded or not running.",
        "unreachable": "Cannot reach the model endpoint. Is LM Studio running?",
        "empty_response": "Model returned an empty response. It may be loading or the prompt may be too large.",
        "malformed_response": "Model returned a response that could not be parsed. Try again.",
        "parse_failure": "Model output was not valid JSON. The response may have been truncated or malformed.",
        "schema_mismatch": "Model response did not match the expected format. Try again.",
        "model_unavailable": "No model endpoint is configured or enabled.",
        "unknown": "Model analysis failed due to an unexpected error.",
    }
    return messages.get(error_kind, messages["unknown"])
