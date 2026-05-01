"""Tests for ``app.services.notes_service`` (home-dashboard notes v1)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import notes_service  # noqa: E402
from app.services.notes_service import (  # noqa: E402
    ALLOWED_HOME_SECTIONS,
    InvalidNoteBodyError,
    UnknownSectionError,
)


SECTION = "market_regime"


@pytest.fixture(autouse=True)
def _isolated_notes_file(tmp_path, monkeypatch):
    """Point ``NOTES_FILE`` at a temp file for each test."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    target = notes_dir / "home_notes.json"
    monkeypatch.setattr(notes_service, "NOTES_FILE", target)
    # Fresh lock per test to avoid leaking state between async runs.
    monkeypatch.setattr(notes_service, "_FILE_LOCK", asyncio.Lock())
    yield target


def test_append_then_list_returns_note():
    note = asyncio.run(notes_service.append_note(SECTION, "hello world"))
    assert note["note_id"].startswith("nt_")
    assert note["body"] == "hello world"

    listed = asyncio.run(notes_service.list_notes(SECTION))
    assert len(listed) == 1
    assert listed[0]["note_id"] == note["note_id"]
    assert listed[0]["body"] == "hello world"


def test_append_trims_trailing_whitespace():
    note = asyncio.run(notes_service.append_note(SECTION, "body text   \n\n"))
    assert note["body"] == "body text"


def test_empty_body_raises():
    with pytest.raises(InvalidNoteBodyError):
        asyncio.run(notes_service.append_note(SECTION, ""))
    with pytest.raises(InvalidNoteBodyError):
        asyncio.run(notes_service.append_note(SECTION, "   \n  "))


def test_oversize_body_raises():
    huge = "x" * (notes_service.MAX_BODY_LEN + 1)
    with pytest.raises(InvalidNoteBodyError):
        asyncio.run(notes_service.append_note(SECTION, huge))


def test_unknown_section_on_append_raises():
    with pytest.raises(UnknownSectionError):
        asyncio.run(notes_service.append_note("not_a_section", "x"))


def test_unknown_section_on_list_returns_empty():
    # GET contract: unknown section -> empty list, no mutation, no error.
    listed = asyncio.run(notes_service.list_notes("not_a_section"))
    assert listed == []


def test_delete_existing_returns_true_and_list_empty():
    note = asyncio.run(notes_service.append_note(SECTION, "to delete"))
    ok = asyncio.run(notes_service.delete_note(SECTION, note["note_id"]))
    assert ok is True
    listed = asyncio.run(notes_service.list_notes(SECTION))
    assert listed == []


def test_delete_missing_returns_false():
    ok = asyncio.run(notes_service.delete_note(SECTION, "nt_does_not_exist"))
    assert ok is False


def test_concurrent_appends_are_atomic(_isolated_notes_file):
    async def _run():
        await asyncio.gather(
            notes_service.append_note(SECTION, "first"),
            notes_service.append_note(SECTION, "second"),
        )

    asyncio.run(_run())

    with _isolated_notes_file.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    bodies = sorted(n["body"] for n in data["sections"][SECTION]["notes"])
    assert bodies == ["first", "second"]


def test_allowed_sections_is_nonempty_frozenset():
    assert isinstance(ALLOWED_HOME_SECTIONS, frozenset)
    assert len(ALLOWED_HOME_SECTIONS) >= 5
