"""Tests for Step 17 — UI execution mode control.

Covers:
    A — Execution mode state service (get/set/persist/reset)
    B — Execution mode display metadata
    C — Routing system summary includes execution mode
    D — API GET /routing/execution-mode
    E — API POST /routing/execution-mode (valid + invalid)
    F — POST cooldown enforcement
    G — RoutingSystemSummary to_dict has new fields
    H — Frontend rendering of execution mode selector
    I — Frontend rendering of execution mode in routing system status
    J — CSS has execution mode styles
    K — Direct vs distributed mode labels
    L — build_execution_mode_options ordering
"""

from __future__ import annotations

import json
import pathlib
import threading
import asyncio
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
_FRONTEND_ROOT = _BACKEND_ROOT.parent / "frontend"
_DATA_HEALTH_JS = _FRONTEND_ROOT / "assets" / "js" / "pages" / "data_health.js"
_DATA_HEALTH_HTML = _FRONTEND_ROOT / "dashboards" / "data_health.html"
_MODULE_CSS = _FRONTEND_ROOT / "assets" / "css" / "module-dashboard.css"


# ═══════════════════════════════════════════════════════════════════════════
# A — Execution mode state service
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionModeState:

    def test_get_default_mode(self, tmp_path):
        """Default mode should be 'local_distributed' when no config exists."""
        config_file = tmp_path / "runtime_config.json"
        with patch(
            "app.services.execution_mode_state._RUNTIME_CONFIG_PATH",
            config_file,
        ):
            from app.services.execution_mode_state import (
                get_execution_mode,
                reset_execution_mode,
            )
            reset_execution_mode()
            assert get_execution_mode() == "local_distributed"

    def test_set_valid_mode(self, tmp_path):
        config_file = tmp_path / "runtime_config.json"
        config_file.write_text("{}", encoding="utf-8")
        with patch(
            "app.services.execution_mode_state._RUNTIME_CONFIG_PATH",
            config_file,
        ):
            from app.services.execution_mode_state import (
                set_execution_mode,
                get_execution_mode,
                reset_execution_mode,
            )
            reset_execution_mode()
            result = set_execution_mode("online_distributed")
            assert result == "online_distributed"
            assert get_execution_mode() == "online_distributed"

    def test_set_invalid_mode_raises(self):
        from app.services.execution_mode_state import set_execution_mode
        with pytest.raises(ValueError, match="Unknown execution mode"):
            set_execution_mode("nonexistent_mode")

    def test_persists_to_disk(self, tmp_path):
        config_file = tmp_path / "runtime_config.json"
        config_file.write_text("{}", encoding="utf-8")
        with patch(
            "app.services.execution_mode_state._RUNTIME_CONFIG_PATH",
            config_file,
        ):
            from app.services.execution_mode_state import (
                set_execution_mode,
                reset_execution_mode,
            )
            reset_execution_mode()
            set_execution_mode("model_machine")
            data = json.loads(config_file.read_text(encoding="utf-8"))
            assert data["execution_mode"] == "model_machine"
            assert "execution_mode_updated_at" in data

    def test_loads_from_disk(self, tmp_path):
        config_file = tmp_path / "runtime_config.json"
        config_file.write_text(
            json.dumps({"execution_mode": "premium_online"}),
            encoding="utf-8",
        )
        with patch(
            "app.services.execution_mode_state._RUNTIME_CONFIG_PATH",
            config_file,
        ):
            from app.services.execution_mode_state import (
                get_execution_mode,
                reset_execution_mode,
            )
            reset_execution_mode()
            assert get_execution_mode() == "premium_online"

    def test_invalid_disk_value_falls_back(self, tmp_path):
        config_file = tmp_path / "runtime_config.json"
        config_file.write_text(
            json.dumps({"execution_mode": "bogus_mode"}),
            encoding="utf-8",
        )
        with patch(
            "app.services.execution_mode_state._RUNTIME_CONFIG_PATH",
            config_file,
        ):
            from app.services.execution_mode_state import (
                get_execution_mode,
                reset_execution_mode,
            )
            reset_execution_mode()
            assert get_execution_mode() == "local_distributed"

    def test_all_valid_modes_accepted(self):
        from app.services.execution_mode_state import set_execution_mode, reset_execution_mode
        from app.services.model_routing_contract import VALID_MODES
        reset_execution_mode()
        for mode in VALID_MODES:
            with patch("app.services.execution_mode_state._persist"):
                result = set_execution_mode(mode)
                assert result == mode

    def test_thread_safety(self, tmp_path):
        """Concurrent sets should not corrupt state."""
        config_file = tmp_path / "runtime_config.json"
        config_file.write_text("{}", encoding="utf-8")
        with patch(
            "app.services.execution_mode_state._RUNTIME_CONFIG_PATH",
            config_file,
        ):
            from app.services.execution_mode_state import (
                set_execution_mode,
                get_execution_mode,
                reset_execution_mode,
            )
            from app.services.model_routing_contract import VALID_MODES
            reset_execution_mode()
            modes = list(VALID_MODES)
            errors = []

            def worker(m):
                try:
                    set_execution_mode(m)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker, args=(m,)) for m in modes]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors
            assert get_execution_mode() in VALID_MODES


# ═══════════════════════════════════════════════════════════════════════════
# B — Execution mode display metadata
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionModeDisplay:

    def test_all_modes_have_display_entries(self):
        from app.services.model_routing_contract import VALID_MODES
        from app.services.routing_dashboard_contract import EXECUTION_MODE_DISPLAY
        for mode in VALID_MODES:
            assert mode in EXECUTION_MODE_DISPLAY, f"{mode} missing from EXECUTION_MODE_DISPLAY"

    def test_display_label_function(self):
        from app.services.routing_dashboard_contract import execution_mode_display_label
        assert execution_mode_display_label("local_distributed") == "Local Distributed"
        assert execution_mode_display_label("online_distributed") == "Online Distributed"
        assert execution_mode_display_label("local") == "Local"
        assert execution_mode_display_label("model_machine") == "Model Machine"
        assert execution_mode_display_label("premium_online") == "Premium Online"

    def test_description_function(self):
        from app.services.routing_dashboard_contract import execution_mode_description
        assert "then model machine" in execution_mode_description("local_distributed")
        assert "Bedrock" in execution_mode_description("online_distributed")
        assert "only" in execution_mode_description("local")
        assert "only" in execution_mode_description("model_machine")
        assert "only" in execution_mode_description("premium_online")

    def test_unknown_mode_fallback(self):
        from app.services.routing_dashboard_contract import (
            execution_mode_display_label,
            execution_mode_description,
        )
        assert execution_mode_display_label("unknown_xyz") == "unknown_xyz"
        assert execution_mode_description("unknown_xyz") == ""

    def test_group_assignments(self):
        from app.services.routing_dashboard_contract import EXECUTION_MODE_DISPLAY
        assert EXECUTION_MODE_DISPLAY["local_distributed"]["group"] == "primary"
        assert EXECUTION_MODE_DISPLAY["online_distributed"]["group"] == "primary"
        assert EXECUTION_MODE_DISPLAY["local"]["group"] == "direct"
        assert EXECUTION_MODE_DISPLAY["model_machine"]["group"] == "direct"
        assert EXECUTION_MODE_DISPLAY["premium_online"]["group"] == "direct"


# ═══════════════════════════════════════════════════════════════════════════
# C — Routing system summary includes execution mode
# ═══════════════════════════════════════════════════════════════════════════


class TestRoutingSystemSummaryExecutionMode:

    def test_summary_has_execution_mode_fields(self):
        from app.services.routing_dashboard_contract import RoutingSystemSummary
        summary = RoutingSystemSummary(
            routing_enabled=True,
            bedrock_enabled=False,
            default_max_concurrency=1,
            selected_execution_mode="local_distributed",
            execution_mode_label="Local Distributed",
        )
        d = summary.to_dict()
        assert d["selected_execution_mode"] == "local_distributed"
        assert d["execution_mode_label"] == "Local Distributed"

    def test_to_dict_keys_include_new_fields(self):
        from app.services.routing_dashboard_contract import RoutingSystemSummary
        summary = RoutingSystemSummary(
            routing_enabled=True,
            bedrock_enabled=True,
            default_max_concurrency=1,
        )
        d = summary.to_dict()
        assert "selected_execution_mode" in d
        assert "execution_mode_label" in d


# ═══════════════════════════════════════════════════════════════════════════
# D — API GET /routing/execution-mode
# ═══════════════════════════════════════════════════════════════════════════


class TestGetExecutionModeAPI:

    def _make_app(self):
        from fastapi import FastAPI
        from app.api.routes_routing import router
        app = FastAPI()
        app.include_router(router, prefix="/api/admin")
        return app

    def test_get_returns_selected_mode(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        client = TestClient(app)

        with patch("app.services.execution_mode_state.get_execution_mode", return_value="local_distributed"), \
             patch("app.services.model_routing_config.get_routing_config") as mock_cfg, \
             patch("app.services.routing_dashboard_contract.build_execution_mode_options", return_value=[]), \
             patch("app.services.routing_dashboard_contract.execution_mode_description", return_value="Main machine, then model machine"), \
             patch("app.services.routing_dashboard_contract.execution_mode_display_label", return_value="Local Distributed"):
            mock_cfg.return_value = MagicMock(routing_enabled=True)
            resp = client.get("/api/admin/routing/execution-mode")

        assert resp.status_code == 200
        data = resp.json()
        assert data["selected_mode"] == "local_distributed"
        assert data["display_label"] == "Local Distributed"
        assert data["routing_enabled"] is True
        assert "options" in data

    def test_get_includes_options_list(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        client = TestClient(app)

        sample_options = [
            {"mode": "local_distributed", "label": "Local Distributed",
             "description": "Main machine, then model machine", "group": "primary"},
        ]
        with patch("app.services.execution_mode_state.get_execution_mode", return_value="local"), \
             patch("app.services.model_routing_config.get_routing_config") as mock_cfg, \
             patch("app.services.routing_dashboard_contract.build_execution_mode_options", return_value=sample_options), \
             patch("app.services.routing_dashboard_contract.execution_mode_description", return_value=""), \
             patch("app.services.routing_dashboard_contract.execution_mode_display_label", return_value="Local"):
            mock_cfg.return_value = MagicMock(routing_enabled=True)
            resp = client.get("/api/admin/routing/execution-mode")

        assert resp.status_code == 200
        assert len(resp.json()["options"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# E — API POST /routing/execution-mode
# ═══════════════════════════════════════════════════════════════════════════


class TestSetExecutionModeAPI:

    def _make_app(self):
        from fastapi import FastAPI
        from app.api.routes_routing import router, _last_call
        _last_call.clear()
        app = FastAPI()
        app.include_router(router, prefix="/api/admin")
        return app

    def test_post_valid_mode(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        client = TestClient(app)

        with patch("app.services.execution_mode_state.set_execution_mode", return_value="online_distributed"), \
             patch("app.services.model_routing_config.get_routing_config") as mock_cfg, \
             patch("app.services.routing_dashboard_contract.execution_mode_display_label", return_value="Online Distributed"), \
             patch("app.services.routing_dashboard_contract.execution_mode_description", return_value="Main machine, then model machine, then Bedrock"):
            mock_cfg.return_value = MagicMock(routing_enabled=True)
            resp = client.post(
                "/api/admin/routing/execution-mode",
                json={"mode": "online_distributed"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["selected_mode"] == "online_distributed"
        assert data["display_label"] == "Online Distributed"

    def test_post_invalid_mode(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        client = TestClient(app)

        with patch(
            "app.services.execution_mode_state.set_execution_mode",
            side_effect=ValueError("Unknown execution mode 'garbage'"),
        ):
            resp = client.post(
                "/api/admin/routing/execution-mode",
                json={"mode": "garbage"},
            )

        assert resp.status_code == 400
        assert "INVALID_MODE" in resp.json()["error"]

    def test_post_missing_mode_field(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/admin/routing/execution-mode",
            json={"wrong_field": "local"},
        )
        assert resp.status_code == 400
        assert "MISSING_FIELD" in resp.json()["error"]

    def test_post_invalid_json(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/admin/routing/execution-mode",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# F — POST cooldown enforcement
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionModeCooldown:

    def test_post_cooldown_enforced(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes_routing import router, _last_call
        import time

        _last_call.clear()
        app = FastAPI()
        app.include_router(router, prefix="/api/admin")
        client = TestClient(app)

        with patch("app.services.execution_mode_state.set_execution_mode", return_value="local"), \
             patch("app.services.model_routing_config.get_routing_config") as mock_cfg, \
             patch("app.services.routing_dashboard_contract.execution_mode_display_label", return_value="Local"), \
             patch("app.services.routing_dashboard_contract.execution_mode_description", return_value="Main machine only"):
            mock_cfg.return_value = MagicMock(routing_enabled=True)

            resp1 = client.post(
                "/api/admin/routing/execution-mode",
                json={"mode": "local"},
            )
            assert resp1.status_code == 200

            # Second call should be rate limited
            resp2 = client.post(
                "/api/admin/routing/execution-mode",
                json={"mode": "model_machine"},
            )
            assert resp2.status_code == 429
            assert "cooldown" in resp2.json().get("error", "")


# ═══════════════════════════════════════════════════════════════════════════
# G — RoutingSystemSummary to_dict has all expected fields
# ═══════════════════════════════════════════════════════════════════════════


class TestRoutingSystemSummaryShape:

    def test_to_dict_has_expected_keys(self):
        from app.services.routing_dashboard_contract import RoutingSystemSummary
        summary = RoutingSystemSummary(
            routing_enabled=True,
            bedrock_enabled=True,
            default_max_concurrency=1,
        )
        d = summary.to_dict()
        expected = {
            "routing_enabled", "bedrock_enabled", "default_max_concurrency",
            "provider_concurrency", "probe_timeout_seconds",
            "probe_degraded_threshold_ms", "config_source",
            "provider_count", "config_loaded_at",
            # Step 17 additions
            "selected_execution_mode", "execution_mode_label",
        }
        assert set(d.keys()) == expected


# ═══════════════════════════════════════════════════════════════════════════
# H — Frontend rendering of execution mode selector
# ═══════════════════════════════════════════════════════════════════════════


class TestFrontendExecutionModeSelector:

    def test_html_has_execution_mode_card(self):
        """Step 18: Execution mode now lives in unified Model & Routing Mode card."""
        html = _DATA_HEALTH_HTML.read_text(encoding="utf-8")
        assert 'id="dhModelSourceCard"' in html
        assert 'id="dhModelSourceToggle"' in html
        assert 'id="dhModelSourceMeta"' in html
        assert 'id="dhModelSourceFeedback"' in html

    def test_html_has_execution_mode_title(self):
        """Step 18: Card title is now 'Model & Routing Mode'."""
        html = _DATA_HEALTH_HTML.read_text(encoding="utf-8")
        assert "Model &amp; Routing Mode" in html

    def test_js_has_execution_mode_url(self):
        js = _DATA_HEALTH_JS.read_text(encoding="utf-8")
        assert "/api/admin/routing/execution-mode" in js

    def test_js_renders_mode_buttons(self):
        js = _DATA_HEALTH_JS.read_text(encoding="utf-8")
        assert "dh-exec-mode-btn" in js
        assert "dh-exec-mode-btn--active" in js

    def test_js_renders_group_labels(self):
        js = _DATA_HEALTH_JS.read_text(encoding="utf-8")
        assert "dh-exec-mode-group-label" in js
        assert "'Distributed'" in js
        assert "'Direct'" in js

    def test_js_fetch_execution_mode(self):
        """Step 18: Fetch is now via fetchModelSourceState (unified)."""
        js = _DATA_HEALTH_JS.read_text(encoding="utf-8")
        assert "fetchModelSourceState" in js

    def test_js_set_execution_mode(self):
        """Step 18: Set is now via setModelSource (unified)."""
        js = _DATA_HEALTH_JS.read_text(encoding="utf-8")
        assert "setModelSource" in js

    def test_js_disables_buttons_during_update(self):
        js = _DATA_HEALTH_JS.read_text(encoding="utf-8")
        assert "_execModeInFlight" in js
        assert "btn.disabled = true" in js


# ═══════════════════════════════════════════════════════════════════════════
# I — Frontend rendering of execution mode in routing system status
# ═══════════════════════════════════════════════════════════════════════════


class TestFrontendRoutingStatusMode:

    def test_js_renders_execution_mode_in_system_status(self):
        js = _DATA_HEALTH_JS.read_text(encoding="utf-8")
        assert "execution_mode_label" in js
        assert "selected_execution_mode" in js
        assert "Execution Mode" in js


# ═══════════════════════════════════════════════════════════════════════════
# J — CSS has execution mode styles
# ═══════════════════════════════════════════════════════════════════════════


class TestCSSExecutionMode:

    def test_css_has_mode_toggle(self):
        css = _MODULE_CSS.read_text(encoding="utf-8")
        assert ".dh-exec-mode-toggle" in css

    def test_css_has_mode_button(self):
        css = _MODULE_CSS.read_text(encoding="utf-8")
        assert ".dh-exec-mode-btn" in css
        assert ".dh-exec-mode-btn--active" in css

    def test_css_has_group_label(self):
        css = _MODULE_CSS.read_text(encoding="utf-8")
        assert ".dh-exec-mode-group-label" in css


# ═══════════════════════════════════════════════════════════════════════════
# K — Direct vs distributed mode labels
# ═══════════════════════════════════════════════════════════════════════════


class TestDirectVsDistributedLabels:

    def test_distributed_modes_mention_multiple_machines(self):
        from app.services.routing_dashboard_contract import EXECUTION_MODE_DISPLAY
        ld = EXECUTION_MODE_DISPLAY["local_distributed"]
        assert "then" in ld["description"]
        od = EXECUTION_MODE_DISPLAY["online_distributed"]
        assert "then" in od["description"]

    def test_direct_modes_say_only(self):
        from app.services.routing_dashboard_contract import EXECUTION_MODE_DISPLAY
        assert "only" in EXECUTION_MODE_DISPLAY["local"]["description"]
        assert "only" in EXECUTION_MODE_DISPLAY["model_machine"]["description"]
        assert "only" in EXECUTION_MODE_DISPLAY["premium_online"]["description"]


# ═══════════════════════════════════════════════════════════════════════════
# L — build_execution_mode_options ordering and completeness
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildExecutionModeOptions:

    def test_returns_all_five_modes(self):
        from app.services.routing_dashboard_contract import build_execution_mode_options
        options = build_execution_mode_options()
        modes = {o["mode"] for o in options}
        assert modes == {"local", "model_machine", "premium_online",
                         "local_distributed", "online_distributed"}

    def test_each_option_has_required_fields(self):
        from app.services.routing_dashboard_contract import build_execution_mode_options
        options = build_execution_mode_options()
        for opt in options:
            assert "mode" in opt
            assert "label" in opt
            assert "description" in opt
            assert "group" in opt

    def test_primary_options_come_first(self):
        from app.services.routing_dashboard_contract import build_execution_mode_options
        options = build_execution_mode_options()
        primary_indices = [i for i, o in enumerate(options) if o["group"] == "primary"]
        direct_indices = [i for i, o in enumerate(options) if o["group"] == "direct"]
        if primary_indices and direct_indices:
            assert max(primary_indices) < min(direct_indices)
