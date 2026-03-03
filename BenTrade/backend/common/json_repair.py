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
    """Find the first top-level { ... } or [ ... ] in the text."""
    start_idx = None
    for char in ("{", "["):
        idx = text.find(char)
        if idx != -1 and (start_idx is None or idx < start_idx):
            start_idx = idx

    if start_idx is None:
        return None

    open_char = text[start_idx]
    close_char = "}" if open_char == "{" else "]"
    end_idx = text.rfind(close_char)

    if end_idx <= start_idx:
        return None

    return text[start_idx : end_idx + 1]


def _repair_json_text(text: str) -> str:
    """
    Apply common text repairs for LLM JSON formatting errors.

    Repairs applied (in order):
      1. Replace smart/curly quotes with straight quotes.
      2. Remove single-line // comments.
      3. Remove trailing commas before } or ].
      4. Replace Python-style None/True/False with JSON null/true/false.
      5. Strip control characters.
    """
    # 1. Smart quotes → straight
    text = text.replace("\u201c", '"').replace("\u201d", '"')  # " "
    text = text.replace("\u2018", "'").replace("\u2019", "'")  # ' '
    text = text.replace("\u00ab", '"').replace("\u00bb", '"')  # « »

    # 2. Remove // single-line comments (but not inside strings — best effort)
    text = re.sub(r'(?m)^\s*//.*$', '', text)
    text = re.sub(r',\s*//[^\n]*', ',', text)

    # 3. Trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # 4. Python literals → JSON
    text = re.sub(r'\bNone\b', 'null', text)
    text = re.sub(r'\bTrue\b', 'true', text)
    text = re.sub(r'\bFalse\b', 'false', text)

    # 5. Strip non-printable control chars (except \n, \r, \t)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

    return text
