"""Model Score Store — durable per-engine model-score persistence.

Persists the latest model analysis score and summary per market-picture
engine to a single JSON file so the history capture seam can include real
model scores in overtime snapshots.

File: data/market_state/model_scores_latest.json

Each entry:
  { "model_score": float, "model_label": str, "confidence": float,
    "model_summary": str | None, "captured_at": ISO-timestamp }

Thread-safe via _write_lock.  Atomic writes via tmp + os.replace.

Freshness: load_fresh_scores() returns only entries newer than
max_age_seconds (default 6 h = 21600 s).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STORE_FILENAME = "model_scores_latest.json"
DEFAULT_MAX_AGE_SECONDS = 21600  # 6 hours
MAX_SUMMARY_LENGTH = 500  # truncate model summaries to this length

_write_lock = threading.Lock()


def sanitize_model_summary(raw: str | None) -> str | None:
    """Extract a concise, dashboard-safe model summary.

    Rules:
    - None / empty → None
    - If the string is a JSON object, extract the "summary" field
    - Uses json_repair pipeline for malformed JSON before falling back
    - Strip whitespace, collapse internal whitespace runs
    - Truncate to MAX_SUMMARY_LENGTH characters
    - Returns final-answer text only (no reasoning traces)
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    # If the raw summary looks like JSON, try to extract the actual summary text
    if text.startswith("{") or text.startswith("```"):
        extracted_text = None
        # First try standard json.loads
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                extracted_text = (
                    parsed.get("summary")
                    or parsed.get("executive_summary")
                    or parsed.get("description")
                )
        except (json.JSONDecodeError, TypeError):
            # Fall back to json_repair for malformed JSON
            try:
                from common.json_repair import extract_and_repair_json
                parsed, _method = extract_and_repair_json(text)
                if isinstance(parsed, dict):
                    extracted_text = (
                        parsed.get("summary")
                        or parsed.get("executive_summary")
                        or parsed.get("description")
                    )
            except Exception:
                pass
        if isinstance(extracted_text, str) and extracted_text.strip():
            text = extracted_text.strip()
    text = " ".join(text.split())  # collapse whitespace
    if not text:
        return None
    if len(text) > MAX_SUMMARY_LENGTH:
        text = text[:MAX_SUMMARY_LENGTH].rsplit(" ", 1)[0] + "…"
    return text


def _store_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "market_state" / STORE_FILENAME


def save_model_score(
    data_dir: str | Path,
    engine_key: str,
    model_analysis: dict[str, Any] | None,
    as_of: str | None = None,
) -> bool:
    """Persist latest model score for one engine.

    Input fields (from service.run_model_analysis()["model_analysis"]):
      score      — 0-100 float
      label      — enum string (e.g. BROAD_RALLY)
      confidence — 0-1 float

    Returns True if written, False if model_analysis was empty/None.
    """
    if not model_analysis:
        return False

    path = _store_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "model_score": model_analysis.get("score"),
        "model_label": model_analysis.get("label"),
        "confidence": model_analysis.get("confidence"),
        "model_summary": sanitize_model_summary(model_analysis.get("summary")),
        "captured_at": as_of or datetime.now(timezone.utc).isoformat(),
    }

    with _write_lock:
        store: dict[str, Any] = {}
        if path.exists():
            try:
                store = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                store = {}

        store[engine_key] = entry

        # Atomic write
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(store, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(path))

    logger.debug(
        "[ModelScoreStore] saved engine=%s score=%s",
        engine_key, entry["model_score"],
    )
    return True


def load_fresh_scores(
    data_dir: str | Path,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Load model scores within the freshness window.

    Returns: { engine_key: { model_score, model_label, confidence, captured_at } }
    Only includes entries whose captured_at is within max_age_seconds of now.
    """
    path = _store_path(data_dir)
    if not path.exists():
        return {}

    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    now = datetime.now(timezone.utc)
    fresh: dict[str, dict[str, Any]] = {}

    for key, entry in store.items():
        if not isinstance(entry, dict):
            continue
        captured = entry.get("captured_at")
        if not captured:
            continue
        try:
            ts = datetime.fromisoformat(captured)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (now - ts).total_seconds()
            if age <= max_age_seconds:
                fresh[key] = entry
        except (ValueError, TypeError):
            continue

    return fresh


def load_all_scores(
    data_dir: str | Path,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Load all model scores with freshness metadata.

    Returns: { engine_key: { model_score, model_label, confidence,
                             captured_at, age_seconds, is_fresh } }
    Every persisted entry is returned; is_fresh indicates whether the
    entry is within max_age_seconds of now.
    """
    path = _store_path(data_dir)
    if not path.exists():
        return {}

    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    now = datetime.now(timezone.utc)
    result: dict[str, dict[str, Any]] = {}

    for key, entry in store.items():
        if not isinstance(entry, dict):
            continue
        captured = entry.get("captured_at")
        age_seconds: float | None = None
        is_fresh = False
        if captured:
            try:
                ts = datetime.fromisoformat(captured)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_seconds = (now - ts).total_seconds()
                is_fresh = age_seconds <= max_age_seconds
            except (ValueError, TypeError):
                pass

        result[key] = {
            **entry,
            "age_seconds": age_seconds,
            "is_fresh": is_fresh,
        }

    return result
