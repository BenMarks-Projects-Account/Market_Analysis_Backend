"""Home-dashboard notes service.

Persists timestamped free-text notes keyed by a ``section_id`` (one of a
fixed, server-side allow-list). Storage is a single JSON file under the
backend data directory, written atomically under an ``asyncio.Lock`` so
concurrent appends never corrupt the file.

Contract (see the spec in the v1 prompt):

* ``ALLOWED_HOME_SECTIONS`` — frozen set of section IDs the API accepts.
  Must stay in sync with ``frontend/assets/js/config/home_note_sections.js``
  (parity enforced by ``tests/test_routes_notes.py``).
* Notes are stored newest-first in memory and on disk.
* Body: max 8000 chars, trimmed of trailing whitespace; empty is rejected.
* ``note_id`` format: ``nt_<uuid4-hex>``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOG = logging.getLogger(__name__)

# Keep in sync with frontend/assets/js/config/home_note_sections.js.
ALLOWED_HOME_SECTIONS: frozenset[str] = frozenset(
    {
        "pre_market_intelligence",
        "pre_market_indicators",
        "index_futures_continuous_48h",
        "market_regime",
        "macro_market_proxies",
    }
)

MAX_BODY_LEN: int = 8000

# Resolve the notes file lazily so tests can monkeypatch ``NOTES_FILE``.
# Backend data dir follows the project convention: BenTrade/backend/data/.
_BACKEND_DIR = Path(__file__).resolve().parents[2]
NOTES_FILE: Path = _BACKEND_DIR / "data" / "notes" / "home_notes.json"

# One lock guards all read/write operations on the notes file. A single
# global lock is sufficient: the file is tiny and writes are rare.
_FILE_LOCK = asyncio.Lock()


class UnknownSectionError(ValueError):
    """Raised when a caller references a section_id outside the allow-list."""


class InvalidNoteBodyError(ValueError):
    """Raised when a note body is empty or exceeds ``MAX_BODY_LEN``."""


def _now_iso() -> str:
    """ISO-8601 UTC with millisecond precision + trailing ``Z``."""
    now = datetime.now(timezone.utc)
    # datetime.isoformat() gives microseconds; trim to ms and swap +00:00 → Z.
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _new_note_id() -> str:
    return "nt_" + uuid.uuid4().hex


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "sections": {}}


def _load_store_sync() -> dict[str, Any]:
    """Load the store from disk, returning an empty structure on miss/error."""
    try:
        with NOTES_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return _empty_store()
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("notes.load_failed path=%s error=%s", NOTES_FILE, exc)
        return _empty_store()

    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("version", 1)
    sections = data.get("sections")
    if not isinstance(sections, dict):
        data["sections"] = {}
    return data


def _write_store_sync(store: dict[str, Any]) -> None:
    """Atomic write: tmp file → ``os.replace`` to target path."""
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = NOTES_FILE.with_suffix(NOTES_FILE.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, NOTES_FILE)


def _section_notes(store: dict[str, Any], section_id: str) -> list[dict[str, Any]]:
    section = store["sections"].get(section_id)
    if not isinstance(section, dict):
        return []
    notes = section.get("notes")
    if not isinstance(notes, list):
        return []
    return notes


async def list_notes(section_id: str) -> list[dict[str, Any]]:
    """Return notes for ``section_id`` newest-first. Empty list if missing.

    Unknown section IDs return an empty list (no mutation), matching the
    GET contract in the spec.
    """
    async with _FILE_LOCK:
        store = _load_store_sync()
        notes = list(_section_notes(store, section_id))
    _LOG.info("notes.list section=%s count=%d", section_id, len(notes))
    return notes


async def append_note(section_id: str, body: str) -> dict[str, Any]:
    """Append a note. Returns the created note dict.

    Raises:
        UnknownSectionError: ``section_id`` not in ``ALLOWED_HOME_SECTIONS``.
        InvalidNoteBodyError: body empty after strip or > ``MAX_BODY_LEN``.
    """
    if section_id not in ALLOWED_HOME_SECTIONS:
        raise UnknownSectionError(f"unknown section_id: {section_id!r}")

    if not isinstance(body, str):
        raise InvalidNoteBodyError("body must be a string")
    # Spec: "server trims trailing whitespace only".
    trimmed = body.rstrip()
    if not trimmed.strip():
        raise InvalidNoteBodyError("body must not be empty")
    if len(trimmed) > MAX_BODY_LEN:
        raise InvalidNoteBodyError(f"body exceeds max length {MAX_BODY_LEN}")

    note = {
        "note_id": _new_note_id(),
        "created_at": _now_iso(),
        "body": trimmed,
    }

    async with _FILE_LOCK:
        store = _load_store_sync()
        section = store["sections"].setdefault(section_id, {"notes": []})
        if not isinstance(section.get("notes"), list):
            section["notes"] = []
        # Newest first.
        section["notes"].insert(0, note)
        _write_store_sync(store)

    _LOG.info(
        "notes.append section=%s note_id=%s body_len=%d",
        section_id,
        note["note_id"],
        len(trimmed),
    )
    return note


async def delete_note(section_id: str, note_id: str) -> bool:
    """Delete a note by id. Returns ``True`` if found and removed."""
    async with _FILE_LOCK:
        store = _load_store_sync()
        section = store["sections"].get(section_id)
        if not isinstance(section, dict):
            _LOG.info(
                "notes.delete section=%s note_id=%s result=missing_section",
                section_id,
                note_id,
            )
            return False
        notes = section.get("notes")
        if not isinstance(notes, list):
            return False
        original_len = len(notes)
        section["notes"] = [n for n in notes if n.get("note_id") != note_id]
        if len(section["notes"]) == original_len:
            _LOG.info(
                "notes.delete section=%s note_id=%s result=not_found",
                section_id,
                note_id,
            )
            return False
        _write_store_sync(store)

    _LOG.info(
        "notes.delete section=%s note_id=%s result=deleted",
        section_id,
        note_id,
    )
    return True
