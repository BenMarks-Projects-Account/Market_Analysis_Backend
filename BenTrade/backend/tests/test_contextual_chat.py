"""Targeted tests for contextual chat service and route.

Coverage:
    - validate_context: valid and invalid contracts
    - build_model_messages: message assembly with history
    - build_market_regime_context: server-side context builder
    - Route endpoint: success + validation error paths
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.contextual_chat_service import (
    VALID_CONTEXT_TYPES,
    build_market_regime_context,
    build_model_messages,
    validate_context,
)


# ═══════════════════════════════════════════════════════════════════════════
# A. validate_context
# ═══════════════════════════════════════════════════════════════════════════


def _make_context(**overrides) -> dict[str, Any]:
    """Return a minimal valid context contract, with optional overrides."""
    base = {
        "context_type": "market_regime",
        "context_title": "Market Regime",
        "context_summary": "Regime: Bullish (score 72, confidence 81%)",
        "context_payload": {"regime_label": "Bullish", "regime_score": 72},
        "source_panel": "home.regime",
        "generated_at": "2025-06-14T12:00:00Z",
    }
    base.update(overrides)
    return base


class TestValidateContext:
    def test_valid_contract(self):
        errors = validate_context(_make_context())
        assert errors == []

    def test_invalid_context_type(self):
        errors = validate_context(_make_context(context_type="nonexistent"))
        assert len(errors) == 1
        assert "context_type" in errors[0]

    def test_missing_context_type(self):
        ctx = _make_context()
        del ctx["context_type"]
        errors = validate_context(ctx)
        assert any("context_type" in e for e in errors)

    def test_missing_context_title(self):
        errors = validate_context(_make_context(context_title=""))
        assert any("context_title" in e for e in errors)

    def test_missing_context_payload(self):
        errors = validate_context(_make_context(context_payload="not_a_dict"))
        assert any("context_payload" in e for e in errors)

    def test_empty_context_payload(self):
        errors = validate_context(_make_context(context_payload={}))
        assert any("empty" in e for e in errors)

    def test_not_a_dict(self):
        errors = validate_context("not a dict")
        assert errors == ["context must be an object"]

    def test_multiple_errors(self):
        errors = validate_context({"context_type": "bad", "context_title": ""})
        assert len(errors) >= 2


# ═══════════════════════════════════════════════════════════════════════════
# C. build_model_messages
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildModelMessages:
    def test_basic_structure(self):
        ctx = _make_context()
        msgs = build_model_messages(
            context=ctx,
            user_message="What does this regime mean?",
            chat_history=[],
        )
        assert len(msgs) == 2  # system + user
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "What does this regime mean?"

    def test_system_contains_context_payload(self):
        ctx = _make_context()
        msgs = build_model_messages(
            context=ctx,
            user_message="test",
            chat_history=[],
        )
        system = msgs[0]["content"]
        assert "BEGIN CONTEXT PAYLOAD" in system
        assert "Bullish" in system

    def test_context_type_wrapper_included(self):
        ctx = _make_context()
        msgs = build_model_messages(
            context=ctx,
            user_message="test",
            chat_history=[],
        )
        system = msgs[0]["content"]
        assert "Market Regime" in system
        assert "structural" in system.lower()

    def test_history_replayed(self):
        ctx = _make_context()
        history = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ]
        msgs = build_model_messages(
            context=ctx,
            user_message="follow-up",
            chat_history=history,
        )
        # system + 2 history + current user = 4
        assert len(msgs) == 4
        assert msgs[1]["content"] == "first question"
        assert msgs[2]["content"] == "first answer"
        assert msgs[3]["content"] == "follow-up"

    def test_empty_history_entries_skipped(self):
        ctx = _make_context()
        history = [
            {"role": "user", "content": "valid"},
            {"role": "user", "content": ""},
            {"role": "other", "content": "invalid role"},
        ]
        msgs = build_model_messages(
            context=ctx,
            user_message="now",
            chat_history=history,
        )
        # system + 1 valid history + current user = 3
        assert len(msgs) == 3

    def test_large_payload_truncated(self):
        big_payload = {"data": "x" * 7000}
        ctx = _make_context(context_payload=big_payload)
        msgs = build_model_messages(
            context=ctx,
            user_message="test",
            chat_history=[],
        )
        system = msgs[0]["content"]
        assert "truncated" in system


# ═══════════════════════════════════════════════════════════════════════════
# B. build_market_regime_context
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildMarketRegimeContext:
    def test_minimal_data(self):
        result = build_market_regime_context({})
        assert result["context_type"] == "market_regime"
        assert result["context_title"] == "Market Regime"
        assert "context_payload" in result
        assert "generated_at" in result

    def test_full_data(self):
        regime = {
            "regime_label": "Risk-Off Bearish",
            "regime_score": 25,
            "confidence": 0.88,
            "interpretation": "Strongly bearish environment",
            "blocks": {
                "structural": {"label": "Bearish", "summary": "Below key MAs"},
                "tape": {"label": "Weak", "summary": "Breadth declining"},
                "tactical": {"label": "Oversold", "summary": "RSI low"},
            },
            "key_drivers": ["VIX elevated", "Breadth weak"],
            "suggested_playbook": {
                "primary": ["Put spreads", "Hedges"],
                "avoid": ["Naked calls"],
            },
            "change_triggers": ["VIX reversion", "Breadth flip"],
            "as_of": "2025-06-14T10:00:00Z",
        }

        result = build_market_regime_context(regime)
        payload = result["context_payload"]
        assert payload["regime_label"] == "Risk-Off Bearish"
        assert payload["regime_score"] == 25
        assert payload["confidence"] == 0.88
        assert payload["structural_block"]["label"] == "Bearish"
        assert payload["tape_block"]["summary"] == "Breadth declining"
        assert payload["key_drivers"] == ["VIX elevated", "Breadth weak"]
        assert payload["what_works"] == ["Put spreads", "Hedges"]
        assert payload["what_to_avoid"] == ["Naked calls"]
        assert "Risk-Off Bearish" in result["context_summary"]


# ═══════════════════════════════════════════════════════════════════════════
# D. Route endpoint (FastAPI TestClient)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class FakeTransportResult:
    content: str
    transport_path: str = "mock"
    finish_reason: str = "stop"


class TestContextualChatRoute:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_valid_request(self, client):
        with patch(
            "common.model_analysis._model_transport",
            return_value=FakeTransportResult(content="Test response from model."),
        ):
            resp = client.post("/api/chat/contextual", json={
                "context": _make_context(),
                "message": "What does this regime mean?",
                "history": [],
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["assistant_message"] == "Test response from model."
        assert body["context_type"] == "market_regime"
        assert "duration_ms" in body

    def test_invalid_context_returns_422(self, client):
        resp = client.post("/api/chat/contextual", json={
            "context": {"context_type": "invalid_type", "context_title": "", "context_payload": {}},
            "message": "hello",
            "history": [],
        })
        assert resp.status_code == 422

    def test_empty_message_rejected(self, client):
        resp = client.post("/api/chat/contextual", json={
            "context": _make_context(),
            "message": "",
            "history": [],
        })
        assert resp.status_code == 422

    def test_history_capped_at_20(self, client):
        long_history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(30)
        ]
        with patch(
            "common.model_analysis._model_transport",
            return_value=FakeTransportResult(content="OK"),
        ) as mock_transport:
            resp = client.post("/api/chat/contextual", json={
                "context": _make_context(),
                "message": "latest",
                "history": long_history,
            })
        assert resp.status_code == 200
        # The route caps at 20 — verify execute_chat received ≤20 history items
        # We check the transport call's messages: system + ≤20 history + 1 current user
        call_payload = mock_transport.call_args[1]["payload"]
        messages = call_payload["messages"]
        # system(1) + history(≤20) + current user(1) = ≤22
        assert len(messages) <= 22
