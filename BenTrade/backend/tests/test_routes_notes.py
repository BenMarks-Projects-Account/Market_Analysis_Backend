"""Tests for ``/api/notes`` routes (home-dashboard notes v1)."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import create_app  # noqa: E402
from app.services import notes_service  # noqa: E402
from app.services.notes_service import ALLOWED_HOME_SECTIONS  # noqa: E402


SECTION = "market_regime"


@pytest.fixture(autouse=True)
def _isolated_notes_file(tmp_path, monkeypatch):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    target = notes_dir / "home_notes.json"
    monkeypatch.setattr(notes_service, "NOTES_FILE", target)
    monkeypatch.setattr(notes_service, "_FILE_LOCK", asyncio.Lock())
    yield target


@pytest.fixture
def client():
    return TestClient(create_app())


def test_get_unknown_section_returns_empty_list(client):
    resp = client.get("/api/notes/sections/unknown_section_id")
    assert resp.status_code == 200
    assert resp.json() == {"section_id": "unknown_section_id", "notes": []}


def test_append_returns_new_note(client):
    resp = client.post(
        f"/api/notes/sections/{SECTION}/append",
        json={"body": "hello from test"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert "note" in payload
    note = payload["note"]
    assert note["body"] == "hello from test"
    assert note["note_id"].startswith("nt_")
    assert note["created_at"].endswith("Z")


def test_append_empty_body_returns_400(client):
    resp = client.post(
        f"/api/notes/sections/{SECTION}/append",
        json={"body": ""},
    )
    # Pydantic rejects min_length=1 with 422; a whitespace-only body is
    # rejected by the service with 400. Both are client errors — either is
    # acceptable per the spec.
    assert resp.status_code in (400, 422)


def test_append_whitespace_only_body_returns_400(client):
    resp = client.post(
        f"/api/notes/sections/{SECTION}/append",
        json={"body": "   \n  "},
    )
    assert resp.status_code == 400


def test_append_unknown_section_returns_404(client):
    resp = client.post(
        "/api/notes/sections/not_a_real_section/append",
        json={"body": "hi"},
    )
    assert resp.status_code == 404


def test_delete_existing_then_list_empty(client):
    appended = client.post(
        f"/api/notes/sections/{SECTION}/append",
        json={"body": "will be deleted"},
    ).json()["note"]

    delete_resp = client.delete(
        f"/api/notes/sections/{SECTION}/notes/{appended['note_id']}"
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"deleted": True}

    list_resp = client.get(f"/api/notes/sections/{SECTION}")
    assert list_resp.status_code == 200
    assert list_resp.json()["notes"] == []


def test_delete_unknown_note_returns_404(client):
    resp = client.delete(f"/api/notes/sections/{SECTION}/notes/nt_missing")
    assert resp.status_code == 404


def test_registry_parity_with_frontend():
    """Python ``ALLOWED_HOME_SECTIONS`` must match the JS registry exactly."""
    frontend_path = (
        BACKEND_ROOT.parent
        / "frontend"
        / "assets"
        / "js"
        / "config"
        / "home_note_sections.js"
    )
    assert frontend_path.exists(), f"missing JS registry at {frontend_path}"
    text = frontend_path.read_text(encoding="utf-8")

    # Grab keys from the object literal. Keys are bare identifiers followed
    # by a colon and a quoted string value.
    js_keys = set(re.findall(r'^\s*([a-z_][a-z0-9_]*)\s*:\s*["\']', text, re.MULTILINE))
    # Filter to plausible section IDs (exclude anything that happens to
    # look like a key elsewhere in comments or headers).
    js_keys = {k for k in js_keys if k not in {"freeze", "Object"}}

    py_keys = set(ALLOWED_HOME_SECTIONS)

    missing_in_py = js_keys - py_keys
    missing_in_js = py_keys - js_keys
    assert not missing_in_py, f"JS keys not in Python set: {missing_in_py}"
    assert not missing_in_js, f"Python keys not in JS registry: {missing_in_js}"
