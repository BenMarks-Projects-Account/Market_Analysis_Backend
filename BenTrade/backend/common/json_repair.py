"""
BenTrade — JSON Repair Pipeline
================================

Robust JSON extraction + repair for LLM outputs that are *almost* valid JSON
but contain common formatting errors (markdown fences, trailing commas,
smart quotes, comments, etc.).

Pipeline order:
  1. Try ``json.loads(text)`` directly.
  2. Strip markdown code fences and retry.
  3. Extract first top-level ``{…}`` or ``[…]`` block.
  4. Apply text repairs (smart quotes, trailing commas, comments, etc.).
  5. Return parsed dict/list, or ``None`` on failure.

Metrics are tracked in a module-level counter dict for diagnostics.

Usage::

    from common.json_repair import extract_and_repair_json, REPAIR_METRICS

    obj, method = extract_and_repair_json(raw_text)
    # method is one of: "direct", "strip_fences", "extract_block",
    #                    "repaired", or None (failure)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("bentrade.json_repair")


# ── Module-level metrics counters (in-process, reset on restart) ──────────
REPAIR_METRICS: dict[str, int] = {
    "parse_ok": 0,
    "parse_repaired": 0,
    "parse_failed": 0,
}


def extract_and_repair_json(raw_text: str) -> tuple[Any | None, str | None]:
    """
    Extract + repair a JSON object from raw LLM output.

    Returns:
        (parsed_object, method_used)  where method_used is one of:
        "direct", "strip_fences", "extract_block", "repaired", or None on failure.
    """
    text = str(raw_text or "").strip()
    if not text:
        REPAIR_METRICS["parse_failed"] += 1
        return None, None

    # ── 1. Direct parse ──────────────────────────────────────────
    obj = _try_parse(text)
    if obj is not None:
        REPAIR_METRICS["parse_ok"] += 1
        return obj, "direct"

    # ── 2. Strip markdown code fences ────────────────────────────
    stripped = _strip_code_fences(text)
    if stripped != text:
        obj = _try_parse(stripped)
        if obj is not None:
            REPAIR_METRICS["parse_ok"] += 1
            return obj, "strip_fences"

    # ── 3. Extract first top-level { ... } or [ ... ] ────────────
    block = _extract_json_block(stripped)
    if block:
        obj = _try_parse(block)
        if obj is not None:
            REPAIR_METRICS["parse_ok"] += 1
            return obj, "extract_block"

        # ── 4. Apply text repairs on the extracted block ─────────
        repaired = _repair_json_text(block)
        obj = _try_parse(repaired)
        if obj is not None:
            REPAIR_METRICS["parse_repaired"] += 1
            logger.info("[JSON_REPAIR] repaired successfully (len=%d)", len(block))
            return obj, "repaired"

    # ── 5. Last resort: repair the full stripped text ─────────────
    repaired_full = _repair_json_text(stripped)
    block2 = _extract_json_block(repaired_full)
    if block2:
        obj = _try_parse(block2)
        if obj is not None:
            REPAIR_METRICS["parse_repaired"] += 1
            logger.info("[JSON_REPAIR] repaired (full-text) successfully")
            return obj, "repaired"

    REPAIR_METRICS["parse_failed"] += 1
    logger.warning(
        "[JSON_REPAIR] all repair attempts failed (text_len=%d, first_100=%r)",
        len(text),
        text[:100],
    )
    return None, None


# ── Internal helpers ──────────────────────────────────────────────────────

def _try_parse(text: str) -> Any | None:
    """Try json.loads; return parsed object or None."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences like ```json ... ``` or ``` ... ```."""
    # Multi-line fences: ```json\n...\n```
    text = re.sub(r"```(?:json|JSON)?\s*\n?", "", text)
    # Single backtick wrapping (rare but seen)
    text = text.strip("`").strip()
    return text


def _extract_json_block(text: str) -> str | None:
    """Find the first balanced top-level { ... } or [ ... ] in the text.

    Uses depth-tracking with string awareness so that braces inside
    JSON string values are not counted.  Falls back to ``rfind`` when
    no balanced close is found (handles truncated output).
    """
    # Locate the first opening brace/bracket
    start_idx = None
    for char in ("{", "["):
        idx = text.find(char)
        if idx != -1 and (start_idx is None or idx < start_idx):
            start_idx = idx

    if start_idx is None:
        return None

    open_char = text[start_idx]
    close_char = "}" if open_char == "{" else "]"

    # Walk forward with depth tracking, skipping string interiors
    depth = 0
    in_string = False
    escape = False
    end_idx = None

    for i in range(start_idx, len(text)):
        ch = text[i]

        if escape:
            escape = False
            continue

        if ch == "\\":
            if in_string:
                escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                end_idx = i
                break

    # Fallback: if brace-matching didn't find a close (truncated output),
    # use rfind as a last resort so we still recover partial JSON.
    if end_idx is None:
        end_idx = text.rfind(close_char)
        if end_idx <= start_idx:
            return None

    return text[start_idx : end_idx + 1]


def diagnose_json_failure(raw: str) -> dict[str, Any]:
    """Inspect raw LLM output and identify why JSON parsing failed.

    Returns a dict with boolean flags and a human-readable ``diagnosis``
    string suitable for embedding in a retry prompt.
    """
    text = str(raw or "")
    flags: dict[str, Any] = {
        "has_think_tags": bool(re.search(r"</?think", text, re.IGNORECASE)),
        "has_fences": bool(re.search(r"```", text)),
        "starts_with_brace": text.lstrip().startswith("{") or text.lstrip().startswith("["),
        "has_trailing_text": False,
        "length": len(text),
        "first_50": text[:50],
        "last_50": text[-50:] if len(text) > 50 else text,
    }

    # Check for text after the last closing brace
    last_close = max(text.rfind("}"), text.rfind("]"))
    if last_close != -1:
        after = text[last_close + 1:].strip()
        if after:
            flags["has_trailing_text"] = True

    # Build human-readable diagnosis
    issues: list[str] = []
    if flags["has_think_tags"]:
        issues.append("Response contained <think> tags — remove ALL XML-style tags")
    if flags["has_fences"]:
        issues.append("Response was wrapped in markdown code fences (```) — do not use fences")
    if not flags["starts_with_brace"]:
        issues.append("Response did not start with { — the very first character must be {")
    if flags["has_trailing_text"]:
        issues.append("Response had text after the closing } — nothing must follow the JSON")

    flags["diagnosis"] = "; ".join(issues) if issues else "Unknown formatting issue"
    return flags


def build_retry_prompt(raw_response: str) -> str:
    """Build a specific retry prompt that tells the LLM exactly what it did wrong."""
    diag = diagnose_json_failure(raw_response)
    return (
        f"Your previous response could not be parsed as JSON. "
        f"Specific problems: {diag['diagnosis']}. "
        f"Return ONLY the corrected JSON object. "
        f"Start with {{ and end with }}. "
        f"No markdown fences, no <think> tags, no commentary before or after."
    )


def _repair_json_text(text: str) -> str:
    """
    Apply common text repairs for LLM JSON formatting errors.

    Repairs applied (in order):
      1. Strip <think>/<scratchpad> reasoning blocks.
      2. Replace smart/curly quotes with straight quotes.
      3. Remove single-line // comments.
      4. Remove trailing commas before } or ].
      5. Replace Python-style None/True/False with JSON null/true/false.
      6. Strip control characters.
    """
    # 1. Strip <think>/<scratchpad> reasoning blocks
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<think>.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<scratchpad>.*?</scratchpad>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<scratchpad>.*$', '', text, flags=re.DOTALL | re.IGNORECASE)

    # 2. Smart quotes → straight
    text = text.replace("\u201c", '"').replace("\u201d", '"')  # " "
    text = text.replace("\u2018", "'").replace("\u2019", "'")  # ' '
    text = text.replace("\u00ab", '"').replace("\u00bb", '"')  # « »

    # 3. Remove // single-line comments (but not inside strings — best effort)
    text = re.sub(r'(?m)^\s*//.*$', '', text)
    text = re.sub(r',\s*//[^\n]*', ',', text)

    # 4. Trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # 5. Python literals → JSON
    text = re.sub(r'\bNone\b', 'null', text)
    text = re.sub(r'\bTrue\b', 'true', text)
    text = re.sub(r'\bFalse\b', 'false', text)

    # 6. Strip non-printable control chars (except \n, \r, \t)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

    return text
