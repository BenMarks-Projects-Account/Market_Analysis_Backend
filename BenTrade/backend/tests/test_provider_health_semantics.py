"""Tests for Step 16 — provider health semantics + dashboard accuracy.

Sections:
    A — build_status_detail_text produces correct detail for each state
    B — state_display_label mapping
    C — ProviderHealthSummary carries new Step 16 fields
    D — ProviderStatusSnapshot carries probe_type and checked_at
    E — Dashboard service populates new fields
    F — Degraded summary includes latency AND threshold context
    G — Busy/unavailable/failed summaries produce stable reasons
    H — Bedrock config-only readiness clearly represented
    I — Provider card API summary no longer omits latency
    J — No sensitive fields exposed in new summary fields
    K — Frontend renders detail text and state label
    L — STATE_DISPLAY_LABELS coverage
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.services.routing_dashboard_contract import (
    BLOCKED_FIELDS,
    STATE_DISPLAY_LABELS,
    STATE_SEVERITY_MAP,
    ProviderHealthSummary,
    build_status_detail_text,
    state_display_label,
    state_to_severity,
)
from app.services.model_provider_registry import ProviderStatusSnapshot
from app.services.model_routing_config import (
    reset_routing_config,
)
from app.services.model_execution_gate import reset_execution_gate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    reset_routing_config()
    reset_execution_gate()
    yield
    reset_routing_config()
    reset_execution_gate()


# ═══════════════════════════════════════════════════════════════════════════
# A — build_status_detail_text produces correct detail for each state
# ═══════════════════════════════════════════════════════════════════════════


class TestStatusDetailText:
    def test_available_live_with_timing(self):
        text = build_status_detail_text(
            state="available", status_reason="healthy",
            timing_ms=45.0, degraded_threshold_ms=2000.0,
            probe_type="live", configured=True,
        )
        assert "45 ms" in text
        assert "Healthy" in text

    def test_available_config_only(self):
        text = build_status_detail_text(
            state="available", status_reason="configured",
            timing_ms=None, degraded_threshold_ms=2000.0,
            probe_type="config_only", configured=True,
        )
        assert "config-only" in text.lower() or "Config-only" in text
        assert "no live probe" in text.lower()

    def test_degraded_with_latency_and_threshold(self):
        text = build_status_detail_text(
            state="degraded",
            status_reason="slow probe response",
            timing_ms=2054.0,
            degraded_threshold_ms=2000.0,
            probe_type="live",
            configured=True,
        )
        assert "2054" in text
        assert "2000" in text
        assert "threshold" in text.lower()

    def test_degraded_without_threshold_shows_latency(self):
        text = build_status_detail_text(
            state="degraded",
            status_reason="slow",
            timing_ms=2500.0,
            degraded_threshold_ms=None,
            probe_type="live",
            configured=True,
        )
        assert "2500" in text

    def test_busy_default_reason(self):
        text = build_status_detail_text(
            state="busy", status_reason="",
            timing_ms=None, degraded_threshold_ms=2000.0,
            probe_type="live", configured=True,
        )
        assert "capacity" in text.lower() or "slots" in text.lower()

    def test_busy_with_specific_reason(self):
        text = build_status_detail_text(
            state="busy",
            status_reason="HTTP 429 — server busy or overloaded",
            timing_ms=100.0, degraded_threshold_ms=2000.0,
            probe_type="live", configured=True,
        )
        assert "HTTP 429" in text

    def test_unavailable_reason(self):
        text = build_status_detail_text(
            state="unavailable", status_reason="probe timed out after 3.0s",
            timing_ms=3000.0, degraded_threshold_ms=2000.0,
            probe_type="live", configured=True,
        )
        assert "timed out" in text

    def test_failed_reason_truncated(self):
        long_reason = "x" * 300
        text = build_status_detail_text(
            state="failed", status_reason=long_reason,
            timing_ms=None, degraded_threshold_ms=2000.0,
            probe_type="live", configured=True,
        )
        assert len(text) <= 203  # 200 + "..."
        assert text.endswith("...")

    def test_not_configured(self):
        text = build_status_detail_text(
            state="unavailable", status_reason="not configured",
            timing_ms=None, degraded_threshold_ms=2000.0,
            probe_type="live", configured=False,
        )
        assert "not configured" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════
# B — state_display_label mapping
# ═══════════════════════════════════════════════════════════════════════════


class TestStateDisplayLabel:
    def test_all_states_have_labels(self):
        for state in STATE_SEVERITY_MAP:
            label = state_display_label(state)
            assert label
            assert label != state  # Should be different from raw state

    def test_available_is_healthy(self):
        assert state_display_label("available") == "Healthy"

    def test_degraded_is_slow(self):
        assert state_display_label("degraded") == "Slow"

    def test_unavailable_is_offline(self):
        assert state_display_label("unavailable") == "Offline"

    def test_busy_is_busy(self):
        assert state_display_label("busy") == "Busy"

    def test_failed_is_error(self):
        assert state_display_label("failed") == "Error"

    def test_unknown_state_falls_back(self):
        label = state_display_label("some_new_state")
        assert isinstance(label, str)


# ═══════════════════════════════════════════════════════════════════════════
# C — ProviderHealthSummary carries new Step 16 fields
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderHealthSummaryFields:
    def test_new_fields_in_dataclass(self):
        s = ProviderHealthSummary(
            provider="localhost_llm",
            display_label="Localhost LLM",
            configured=True,
            current_state="degraded",
            severity="caution",
            probe_success=True,
            status_reason="slow",
            timing_ms=2054.0,
            max_concurrency=1,
            in_flight_count=0,
            available_capacity=1,
            probe_type="live",
            degraded_threshold_ms=2000.0,
            state_display_label="Slow",
            status_detail_text="Responded in 2054 ms; exceeds degraded threshold of 2000 ms",
            last_checked_at="2026-03-17T10:00:00Z",
        )
        d = s.to_dict()
        assert d["probe_type"] == "live"
        assert d["degraded_threshold_ms"] == 2000.0
        assert d["state_display_label"] == "Slow"
        assert "2054" in d["status_detail_text"]
        assert d["last_checked_at"] == "2026-03-17T10:00:00Z"

    def test_defaults_for_new_fields(self):
        s = ProviderHealthSummary(
            provider="test",
            display_label="Test",
            configured=True,
            current_state="available",
            severity="healthy",
            probe_success=True,
            status_reason="ok",
            timing_ms=None,
            max_concurrency=1,
            in_flight_count=0,
            available_capacity=1,
        )
        d = s.to_dict()
        assert d["probe_type"] == "live"
        assert d["degraded_threshold_ms"] is None
        assert d["state_display_label"] == ""
        assert d["status_detail_text"] == ""
        assert d["last_checked_at"] is None


# ═══════════════════════════════════════════════════════════════════════════
# D — ProviderStatusSnapshot carries probe_type and checked_at
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderStatusSnapshotNewFields:
    def test_probe_type_defaults_to_live(self):
        s = ProviderStatusSnapshot(
            provider_id="test", registered=True, configured=True,
            state="available",
        )
        assert s.probe_type == "live"

    def test_checked_at_defaults_to_none(self):
        s = ProviderStatusSnapshot(
            provider_id="test", registered=True, configured=True,
            state="available",
        )
        assert s.checked_at is None

    def test_probe_type_config_only(self):
        s = ProviderStatusSnapshot(
            provider_id="bedrock_titan_nova_pro", registered=True,
            configured=True, state="available",
            probe_type="config_only",
            checked_at="2026-03-17T10:00:00Z",
        )
        assert s.probe_type == "config_only"
        assert s.checked_at == "2026-03-17T10:00:00Z"

    def test_cached_probe_type(self):
        s = ProviderStatusSnapshot(
            provider_id="localhost_llm", registered=True,
            configured=True, state="available",
            probe_type="cached",
        )
        assert s.probe_type == "cached"


# ═══════════════════════════════════════════════════════════════════════════
# E — Dashboard service populates new fields
# ═══════════════════════════════════════════════════════════════════════════


_P_REG = "app.services.model_provider_registry.get_registry"
_P_GATE = "app.services.model_execution_gate.get_execution_gate"
_P_CFG = "app.services.model_routing_config.get_routing_config"


def _mock_registry_with_statuses(statuses):
    mock_reg = MagicMock()
    mock_reg.all_statuses.return_value = statuses
    mock_reg.list_registered.return_value = [s.provider_id for s in statuses]
    return mock_reg


def _mock_gate(providers=None):
    from app.services.model_execution_gate import GateSnapshot
    mock_gate = MagicMock()
    snapshots = {}
    for pid in (providers or ["localhost_llm"]):
        snapshots[pid] = GateSnapshot(
            provider_id=pid, in_flight=0, max_concurrency=1, has_capacity=True,
        )
    mock_gate.all_snapshots.return_value = snapshots
    return mock_gate


def _mock_config(degraded_threshold_ms=2000.0):
    from app.services.model_routing_config import RoutingConfig
    return RoutingConfig(probe_degraded_threshold_ms=degraded_threshold_ms)


class TestDashboardServiceNewFields:
    def test_degraded_summary_has_detail_text_and_threshold(self):
        from app.services.routing_dashboard_service import build_provider_health_summaries

        statuses = [ProviderStatusSnapshot(
            provider_id="localhost_llm", registered=True, configured=True,
            state="degraded", probe_success=True,
            status_reason="slow probe response (2054ms > 2000ms threshold)",
            timing_ms=2054.0, probe_type="live",
            checked_at="2026-03-17T10:00:00Z",
        )]

        with patch(_P_REG) as mock_r, \
             patch(_P_GATE) as mock_g, \
             patch(_P_CFG) as mock_c:
            mock_r.return_value = _mock_registry_with_statuses(statuses)
            mock_g.return_value = _mock_gate(["localhost_llm"])
            mock_c.return_value = _mock_config(2000.0)
            summaries = build_provider_health_summaries(refresh=False)

        s = summaries[0]
        assert s.probe_type == "live"
        assert s.degraded_threshold_ms == 2000.0
        assert s.state_display_label == "Slow"
        assert "2054" in s.status_detail_text
        assert "2000" in s.status_detail_text
        assert s.last_checked_at == "2026-03-17T10:00:00Z"

    def test_bedrock_config_only_clearly_labeled(self):
        from app.services.routing_dashboard_service import build_provider_health_summaries

        statuses = [ProviderStatusSnapshot(
            provider_id="bedrock_titan_nova_pro", registered=True, configured=True,
            state="available", probe_success=True,
            status_reason="configured (config-level probe only — no live inference check)",
            timing_ms=None, probe_type="config_only",
            checked_at="2026-03-17T10:00:00Z",
        )]

        with patch(_P_REG) as mock_r, \
             patch(_P_GATE) as mock_g, \
             patch(_P_CFG) as mock_c:
            mock_r.return_value = _mock_registry_with_statuses(statuses)
            mock_g.return_value = _mock_gate(["bedrock_titan_nova_pro"])
            mock_c.return_value = _mock_config(2000.0)
            summaries = build_provider_health_summaries(refresh=False)

        s = summaries[0]
        assert s.probe_type == "config_only"
        assert "config-only" in s.status_detail_text.lower() or "Config-only" in s.status_detail_text
        assert s.state_display_label == "Healthy"

    def test_available_live_probe_has_timing(self):
        from app.services.routing_dashboard_service import build_provider_health_summaries

        statuses = [ProviderStatusSnapshot(
            provider_id="localhost_llm", registered=True, configured=True,
            state="available", probe_success=True,
            status_reason="healthy", timing_ms=45.0,
            probe_type="live", checked_at="2026-03-17T10:05:00Z",
        )]

        with patch(_P_REG) as mock_r, \
             patch(_P_GATE) as mock_g, \
             patch(_P_CFG) as mock_c:
            mock_r.return_value = _mock_registry_with_statuses(statuses)
            mock_g.return_value = _mock_gate(["localhost_llm"])
            mock_c.return_value = _mock_config(2000.0)
            summaries = build_provider_health_summaries(refresh=False)

        s = summaries[0]
        assert "45 ms" in s.status_detail_text
        assert s.timing_ms == 45.0


# ═══════════════════════════════════════════════════════════════════════════
# F — Degraded summary includes actual latency AND threshold context
# ═══════════════════════════════════════════════════════════════════════════


class TestDegradedLatencyContext:
    def test_detail_text_has_both_latency_and_threshold(self):
        text = build_status_detail_text(
            state="degraded", status_reason="slow",
            timing_ms=3100.0, degraded_threshold_ms=2000.0,
            probe_type="live", configured=True,
        )
        assert "3100" in text
        assert "2000" in text
        assert "threshold" in text.lower()

    def test_to_dict_includes_timing_for_degraded(self):
        s = ProviderHealthSummary(
            provider="localhost_llm", display_label="Localhost LLM",
            configured=True, current_state="degraded", severity="caution",
            probe_success=True, status_reason="slow", timing_ms=2500.0,
            max_concurrency=1, in_flight_count=0, available_capacity=1,
            degraded_threshold_ms=2000.0,
            status_detail_text="Responded in 2500 ms; exceeds degraded threshold of 2000 ms",
        )
        d = s.to_dict()
        assert d["timing_ms"] == 2500.0
        assert d["degraded_threshold_ms"] == 2000.0
        assert "2500" in d["status_detail_text"]


# ═══════════════════════════════════════════════════════════════════════════
# G — Busy/unavailable/failed summaries produce stable reasons
# ═══════════════════════════════════════════════════════════════════════════


class TestStableReasonText:
    def test_busy_with_http_429(self):
        text = build_status_detail_text(
            state="busy",
            status_reason="HTTP 429 — server busy or overloaded",
            timing_ms=50.0, degraded_threshold_ms=2000.0,
            probe_type="live", configured=True,
        )
        assert "429" in text

    def test_unavailable_connection_error(self):
        text = build_status_detail_text(
            state="unavailable",
            status_reason="connection error: [Errno 111] Connection refused",
            timing_ms=2.0, degraded_threshold_ms=2000.0,
            probe_type="live", configured=True,
        )
        assert "connection error" in text.lower()

    def test_failed_with_http_500(self):
        text = build_status_detail_text(
            state="failed",
            status_reason="HTTP 500",
            timing_ms=100.0, degraded_threshold_ms=2000.0,
            probe_type="live", configured=True,
        )
        assert "HTTP 500" in text


# ═══════════════════════════════════════════════════════════════════════════
# H — Bedrock config-only readiness is clearly represented
# ═══════════════════════════════════════════════════════════════════════════


class TestBedrockConfigOnlyReadiness:
    def test_config_only_detail_text(self):
        text = build_status_detail_text(
            state="available",
            status_reason="configured (config-level probe only — no live inference check)",
            timing_ms=None, degraded_threshold_ms=2000.0,
            probe_type="config_only", configured=True,
        )
        assert "config-only" in text.lower() or "Config-only" in text
        assert "no live probe" in text.lower()

    def test_config_only_in_to_dict(self):
        s = ProviderHealthSummary(
            provider="bedrock_titan_nova_pro",
            display_label="Bedrock Titan Nova Pro",
            configured=True, current_state="available", severity="healthy",
            probe_success=True,
            status_reason="configured",
            timing_ms=None, max_concurrency=1,
            in_flight_count=0, available_capacity=1,
            probe_type="config_only",
        )
        d = s.to_dict()
        assert d["probe_type"] == "config_only"


# ═══════════════════════════════════════════════════════════════════════════
# I — Provider card API summary no longer omits latency
# ═══════════════════════════════════════════════════════════════════════════


class TestLatencyNotOmitted:
    def test_degraded_to_dict_has_timing(self):
        s = ProviderHealthSummary(
            provider="localhost_llm", display_label="Localhost LLM",
            configured=True, current_state="degraded", severity="caution",
            probe_success=True, status_reason="slow",
            timing_ms=2054.0,
            max_concurrency=1, in_flight_count=0, available_capacity=1,
            degraded_threshold_ms=2000.0,
            status_detail_text="Responded in 2054 ms; exceeds degraded threshold of 2000 ms",
        )
        d = s.to_dict()
        assert d["timing_ms"] == 2054.0
        assert d["timing_ms"] is not None

    def test_available_to_dict_has_timing(self):
        s = ProviderHealthSummary(
            provider="localhost_llm", display_label="Localhost LLM",
            configured=True, current_state="available", severity="healthy",
            probe_success=True, status_reason="healthy",
            timing_ms=45.0,
            max_concurrency=1, in_flight_count=0, available_capacity=1,
        )
        d = s.to_dict()
        assert d["timing_ms"] == 45.0


# ═══════════════════════════════════════════════════════════════════════════
# J — No sensitive fields exposed in new summary fields
# ═══════════════════════════════════════════════════════════════════════════


class TestNoSensitiveFieldsInNewFields:
    def test_status_detail_text_has_no_blocked_content(self):
        for state in ["available", "degraded", "busy", "unavailable", "failed"]:
            text = build_status_detail_text(
                state=state, status_reason="some reason",
                timing_ms=100.0, degraded_threshold_ms=2000.0,
                probe_type="live", configured=True,
            )
            for bf in BLOCKED_FIELDS:
                assert bf not in text.lower()

    def test_to_dict_keys_no_blocked_fields(self):
        s = ProviderHealthSummary(
            provider="test", display_label="Test",
            configured=True, current_state="available", severity="healthy",
            probe_success=True, status_reason="ok",
            timing_ms=10.0, max_concurrency=1,
            in_flight_count=0, available_capacity=1,
        )
        d = s.to_dict()
        for bf in BLOCKED_FIELDS:
            assert bf not in d


# ═══════════════════════════════════════════════════════════════════════════
# K — Frontend renders detail text and state label
# ═══════════════════════════════════════════════════════════════════════════


class TestFrontendRendering:
    @pytest.fixture()
    def js_content(self):
        import pathlib
        js_path = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "assets" / "js" / "pages" / "data_health.js"
        return js_path.read_text(encoding="utf-8")

    @pytest.fixture()
    def css_content(self):
        import pathlib
        css_path = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "assets" / "css" / "module-dashboard.css"
        return css_path.read_text(encoding="utf-8")

    def test_js_renders_status_detail_text(self, js_content):
        assert "status_detail_text" in js_content

    def test_js_renders_state_display_label(self, js_content):
        assert "state_display_label" in js_content

    def test_js_renders_probe_type(self, js_content):
        assert "probe_type" in js_content

    def test_js_renders_last_checked_at(self, js_content):
        assert "last_checked_at" in js_content

    def test_js_renders_degraded_threshold(self, js_content):
        assert "probe_degraded_threshold_ms" in js_content

    def test_css_has_detail_text_style(self, css_content):
        assert "dh-routing-detail-text" in css_content

    def test_css_has_threshold_hint_style(self, css_content):
        assert "dh-routing-threshold-hint" in css_content


# ═══════════════════════════════════════════════════════════════════════════
# L — STATE_DISPLAY_LABELS coverage
# ═══════════════════════════════════════════════════════════════════════════


class TestStateDisplayLabelsMap:
    def test_all_severity_states_have_display_labels(self):
        for state in STATE_SEVERITY_MAP:
            assert state in STATE_DISPLAY_LABELS, f"Missing display label for state '{state}'"

    def test_labels_are_non_empty_strings(self):
        for state, label in STATE_DISPLAY_LABELS.items():
            assert isinstance(label, str)
            assert len(label) > 0

    def test_labels_are_title_case(self):
        for label in STATE_DISPLAY_LABELS.values():
            assert label[0].isupper(), f"Label '{label}' should be title-case"
