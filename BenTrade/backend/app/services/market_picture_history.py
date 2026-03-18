"""Market Picture History — compact overtime snapshot storage.

Stores timestamped market-picture snapshots for historical charting.
Each snapshot captures regime, scoreboard engine scores, composite,
and playbook posture in a compact, graph-friendly shape.

Storage format: JSONL (one JSON object per line, newest last).
Location: data/market_state/market_picture_history.jsonl

Retention: rolling window capped at MAX_SNAPSHOTS (default 2000).
When cap is exceeded, the oldest entries are trimmed on next append.

Deduplication: snapshots within DEDUP_WINDOW_SECONDS of the last
entry sharing the same artifact_id are silently skipped.
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

# ── Constants ──
HISTORY_FILENAME = "market_picture_history.jsonl"
SCHEMA_VERSION = 1
MAX_SNAPSHOTS = 2000
DEDUP_WINDOW_SECONDS = 120  # skip re-capture within 2 min of same artifact

_write_lock = threading.Lock()


# ── Snapshot schema ──

def build_snapshot(
    *,
    artifact: dict[str, Any],
    engine_cards: list[dict[str, Any]],
    composite: dict[str, Any],
    model_status: str | None,
    generated_at: str | None,
    model_scores: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize a market-picture state into a compact historical snapshot.

    Input fields:
      artifact   — full market-state artifact dict
      engine_cards — slim engine card list from scoreboard endpoint
      composite  — composite overview dict
      model_status — model interpretation status string
      generated_at — artifact generation timestamp
      model_scores — optional fresh per-engine model scores from durable store
                     { engine_key: { model_score, model_label, confidence, captured_at } }

    Output: compact dict suitable for JSONL append.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Regime: extract from artifact's composite + consumer_summary
    cs = artifact.get("consumer_summary") or {}
    comp = artifact.get("composite") or {}

    ms_lookup = model_scores or {}

    # Per-engine compact entries (score + label only, no summaries)
    engines_compact: list[dict[str, Any]] = []
    for card in engine_cards:
        key = card.get("key")
        # Prefer durable model store over card value (which is typically null)
        ms_entry = ms_lookup.get(key) if key else None
        model_score = ms_entry.get("model_score") if ms_entry else card.get("model_score")
        engines_compact.append({
            "key": key,
            "engine_score": card.get("engine_score"),
            "engine_label": card.get("engine_label"),
            "model_score": model_score,
            "confidence": card.get("confidence"),
            "status": card.get("status"),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": now,
        "artifact_id": artifact.get("artifact_id"),
        "generated_at": generated_at,

        # Regime summary
        "regime_state": comp.get("market_state"),
        "regime_support": comp.get("support_state"),
        "regime_stability": comp.get("stability_state"),
        "regime_confidence": comp.get("confidence"),
        "regime_summary": comp.get("summary"),

        # Composite from consumer_summary (if different from comp)
        "consumer_regime_label": cs.get("regime_label"),
        "consumer_regime_score": cs.get("regime_score"),

        # Per-engine scoreboard entries
        "engines": engines_compact,

        # Model interpretation status
        "model_status": model_status,
    }


def _history_path(data_dir: str | Path) -> Path:
    """Resolve path to the history JSONL file."""
    return Path(data_dir) / "market_state" / HISTORY_FILENAME


def append_snapshot(
    data_dir: str | Path,
    snapshot: dict[str, Any],
) -> bool:
    """Append a snapshot to the history file with dedup + retention.

    Returns True if the snapshot was written, False if skipped (dedup).
    Thread-safe via _write_lock.
    """
    path = _history_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    with _write_lock:
        # Dedup: check last entry
        if path.exists():
            try:
                last_line = _read_last_line(path)
                if last_line:
                    last = json.loads(last_line)
                    if _is_duplicate(last, snapshot):
                        return False
            except Exception:
                pass  # proceed with append on any read error

        # Append
        line = json.dumps(snapshot, ensure_ascii=False, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

        # Retention: trim if over cap
        _trim_if_needed(path)

    return True


def load_history(
    data_dir: str | Path,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Load historical snapshots, newest last.

    Args:
        data_dir: backend data directory
        limit: if set, return only the N most recent entries

    Returns: list of snapshot dicts in chronological order (oldest first).
    """
    path = _history_path(data_dir)
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # skip corrupt lines
    except Exception as exc:
        logger.warning("[MarketPictureHistory] read failed: %s", exc)
        return []

    if limit and limit > 0:
        entries = entries[-limit:]

    return entries


# ── Internal helpers ──

def _read_last_line(path: Path) -> str | None:
    """Read the last non-empty line of a file efficiently."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            # Read up to 8KB from the end to find last line
            read_size = min(size, 8192)
            f.seek(-read_size, 2)
            chunk = f.read().decode("utf-8", errors="replace")
            lines = chunk.strip().split("\n")
            return lines[-1] if lines else None
    except Exception:
        return None


def _is_duplicate(last: dict[str, Any], new: dict[str, Any]) -> bool:
    """Check if the new snapshot is a duplicate of the last entry."""
    if last.get("artifact_id") != new.get("artifact_id"):
        return False
    # Same artifact — check time window
    last_ts = last.get("captured_at", "")
    new_ts = new.get("captured_at", "")
    try:
        t1 = datetime.fromisoformat(last_ts)
        t2 = datetime.fromisoformat(new_ts)
        return abs((t2 - t1).total_seconds()) < DEDUP_WINDOW_SECONDS
    except (ValueError, TypeError):
        return False


def _trim_if_needed(path: Path) -> None:
    """If the file exceeds MAX_SNAPSHOTS lines, trim to keep newest entries."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= MAX_SNAPSHOTS:
            return
        # Keep the newest MAX_SNAPSHOTS entries
        keep = lines[-MAX_SNAPSHOTS:]
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(str(tmp), str(path))
        logger.info("[MarketPictureHistory] trimmed %d → %d entries", len(lines), len(keep))
    except Exception as exc:
        logger.warning("[MarketPictureHistory] trim failed: %s", exc)
