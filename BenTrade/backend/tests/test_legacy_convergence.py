"""Tests for Step 19 — Legacy convergence and deprecation verification.

Covers:
    A — Source-of-truth boundaries: execution_mode_state is authoritative
    B — Compatibility isolation: model_state / model_sources still work
    C — Deprecated function verification: resolve_routing_mode no prod callers
    D — Docstring/module-level clarity assertions
    E — Frontend convergence: no stale legacy references
    F — Runtime config coexistence: model_source + execution_mode independent
"""

from __future__ import annotations

import ast
import pathlib
import re
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"
_SERVICES_ROOT = _APP_ROOT / "services"
_FRONTEND_ROOT = _BACKEND_ROOT.parent / "frontend"
_DATA_HEALTH_JS = _FRONTEND_ROOT / "assets" / "js" / "pages" / "data_health.js"
_DATA_HEALTH_HTML = _FRONTEND_ROOT / "dashboards" / "data_health.html"


# ═══════════════════════════════════════════════════════════════════════════
# A — Source-of-truth boundaries
# ═══════════════════════════════════════════════════════════════════════════


class TestAuthorityBoundaries:
    """execution_mode_state is the single authority for routed mode selection."""

    def test_execution_mode_state_is_authoritative(self):
        """execution_mode_state module docstring declares AUTHORITATIVE."""
        src = (_SERVICES_ROOT / "execution_mode_state.py").read_text(encoding="utf-8")
        assert "AUTHORITATIVE" in src

    def test_model_state_is_compatibility(self):
        """model_state module docstring declares compatibility layer."""
        src = (_SERVICES_ROOT / "model_state.py").read_text(encoding="utf-8")
        assert "Compatibility layer" in src or "compatibility" in src.lower()

    def test_model_sources_is_compatibility(self):
        """model_sources module docstring declares compatibility layer."""
        src = (_APP_ROOT / "model_sources.py").read_text(encoding="utf-8")
        assert "Compatibility layer" in src or "compatibility" in src.lower()

    def test_execution_mode_state_independent_of_model_state(self):
        """execution_mode_state does NOT import from model_state."""
        src = (_SERVICES_ROOT / "execution_mode_state.py").read_text(encoding="utf-8")
        assert "from app.services.model_state" not in src
        assert "import model_state" not in src

    def test_model_state_independent_of_execution_mode_state(self):
        """model_state does NOT import from execution_mode_state."""
        src = (_SERVICES_ROOT / "model_state.py").read_text(encoding="utf-8")
        assert "from app.services.execution_mode_state" not in src
        assert "import execution_mode_state" not in src

    def test_resolve_effective_uses_execution_mode_state(self):
        """The authoritative resolver reads from execution_mode_state."""
        src = (_SERVICES_ROOT / "model_routing_integration.py").read_text(encoding="utf-8")
        assert "from app.services.execution_mode_state import get_execution_mode" in src

    def test_resolve_effective_does_not_use_model_state(self):
        """The authoritative resolver never touches model_state."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local_distributed",
        ):
            mode, premium = resolve_effective_execution_mode("generic_task")
        assert mode == "local_distributed"
        assert premium is False

    def test_set_execution_mode_does_not_change_model_source(self):
        """set_execution_mode and set_model_source are independent."""
        from app.services.execution_mode_state import (
            get_execution_mode,
            set_execution_mode,
            reset_execution_mode,
        )
        from app.services.model_state import get_model_source

        original_source = get_model_source()
        try:
            set_execution_mode("online_distributed")
            assert get_execution_mode() == "online_distributed"
            assert get_model_source() == original_source
        finally:
            reset_execution_mode()


# ═══════════════════════════════════════════════════════════════════════════
# B — Compatibility isolation
# ═══════════════════════════════════════════════════════════════════════════


class TestCompatibilityIsolation:
    """model_state / model_sources still work for non-routed callers."""

    def test_model_sources_importable(self):
        from app.model_sources import MODEL_SOURCES, VALID_SOURCE_KEYS
        assert isinstance(MODEL_SOURCES, dict)
        assert len(MODEL_SOURCES) >= 2
        assert isinstance(VALID_SOURCE_KEYS, frozenset)
        assert "local" in VALID_SOURCE_KEYS

    def test_get_model_source_returns_valid_key(self):
        from app.model_sources import VALID_SOURCE_KEYS
        from app.services.model_state import get_model_source
        source = get_model_source()
        assert source in VALID_SOURCE_KEYS

    def test_model_sources_have_required_fields(self):
        """Each source must have name, endpoint, enabled."""
        from app.model_sources import MODEL_SOURCES
        for key, cfg in MODEL_SOURCES.items():
            assert "name" in cfg, f"{key} missing name"
            assert "enabled" in cfg, f"{key} missing enabled"
            assert "endpoint" in cfg, f"{key} missing endpoint"

    def test_model_state_not_affected_by_routing_config(self):
        """model_state does NOT reference routing_enabled or routing_config."""
        src = (_SERVICES_ROOT / "model_state.py").read_text(encoding="utf-8")
        assert "routing_enabled" not in src
        assert "routing_config" not in src

    def test_compat_endpoints_exist_in_routes(self):
        """GET/POST /platform/model-source endpoints still registered."""
        src = (_APP_ROOT / "api" / "routes_admin.py").read_text(encoding="utf-8")
        assert '/platform/model-source' in src
        assert "get_model_source_endpoint" in src
        assert "set_model_source_endpoint" in src

    def test_compat_endpoints_have_compatibility_docstring(self):
        """model-source endpoints note their compatibility role."""
        src = (_APP_ROOT / "api" / "routes_admin.py").read_text(encoding="utf-8")
        # Both GET and POST should mention compatibility
        get_idx = src.index("get_model_source_endpoint")
        post_idx = src.index("set_model_source_endpoint")
        get_block = src[get_idx - 500:get_idx + 200]
        post_block = src[post_idx - 500:post_idx + 200]
        assert "ompatibility" in get_block
        assert "ompatibility" in post_block


# ═══════════════════════════════════════════════════════════════════════════
# C — Deprecated function verification
# ═══════════════════════════════════════════════════════════════════════════


class TestDeprecatedFunctionIsolation:
    """resolve_routing_mode is deprecated with no production callers."""

    def test_resolve_routing_mode_importable(self):
        """Still importable for backward compat."""
        from app.services.model_routing_integration import resolve_routing_mode
        assert callable(resolve_routing_mode)

    def test_resolve_routing_mode_has_deprecation_note(self):
        """Docstring mentions deprecated."""
        from app.services.model_routing_integration import resolve_routing_mode
        assert "deprecated" in (resolve_routing_mode.__doc__ or "").lower()

    def test_resolve_routing_mode_not_called_in_production_code(self):
        """No production .py file (outside tests) calls resolve_routing_mode."""
        prod_files = list(_APP_ROOT.rglob("*.py"))
        for f in prod_files:
            if "_deprecated_pipeline" in str(f):
                continue
            src = f.read_text(encoding="utf-8")
            # Skip the definition itself
            if "def resolve_routing_mode(" in src:
                continue
            assert "resolve_routing_mode(" not in src, (
                f"Production code {f.relative_to(_BACKEND_ROOT)} "
                "calls deprecated resolve_routing_mode()"
            )

    def test_online_distributed_tasks_deprecated_note(self):
        """_ONLINE_DISTRIBUTED_TASKS is marked as used only by deprecated fn."""
        src = (_SERVICES_ROOT / "model_routing_integration.py").read_text(encoding="utf-8")
        idx = src.index("_ONLINE_DISTRIBUTED_TASKS")
        # Check the comment block above the constant
        block = src[max(0, idx - 300):idx + 50]
        assert "deprecated" in block.lower()

    def test_premium_eligible_tasks_used_by_authoritative_resolver(self):
        """_PREMIUM_ELIGIBLE_TASKS is used by the authoritative resolver."""
        src = (_SERVICES_ROOT / "model_routing_integration.py").read_text(encoding="utf-8")
        # Find the resolve_effective_execution_mode function body
        idx = src.index("def resolve_effective_execution_mode")
        fn_block = src[idx:idx + 1200]
        assert "_PREMIUM_ELIGIBLE_TASKS" in fn_block

    def test_both_resolvers_return_same_shape(self):
        """Both resolve functions return (str, bool) tuples."""
        from app.services.model_routing_integration import (
            resolve_routing_mode,
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local_distributed",
        ):
            old = resolve_routing_mode("generic_task")
            new = resolve_effective_execution_mode("generic_task")
        assert isinstance(old, tuple) and len(old) == 2
        assert isinstance(new, tuple) and len(new) == 2
        assert isinstance(old[0], str) and isinstance(old[1], bool)
        assert isinstance(new[0], str) and isinstance(new[1], bool)


# ═══════════════════════════════════════════════════════════════════════════
# D — Contract / docstring clarity
# ═══════════════════════════════════════════════════════════════════════════


class TestDocstringClarity:
    """Modules have clear role declarations."""

    def test_routing_contract_references_execution_mode_state(self):
        """model_routing_contract references execution_mode_state, not model_state."""
        src = (_SERVICES_ROOT / "model_routing_contract.py").read_text(encoding="utf-8")
        assert "execution_mode_state" in src
        assert "model_state," not in src  # was removed in Step 19

    def test_integration_module_docstring_has_policy_table(self):
        """model_routing_integration documents the routing policy table."""
        src = (_SERVICES_ROOT / "model_routing_integration.py").read_text(encoding="utf-8")
        assert "local_distributed" in src[:1500]
        assert "online_distributed" in src[:1500]
        assert "premium_override" in src[:1500]

    def test_execution_mode_state_lists_all_public_functions(self):
        """Module docstring lists get/set/reset."""
        src = (_SERVICES_ROOT / "execution_mode_state.py").read_text(encoding="utf-8")
        docstring = src[:800]
        assert "get_execution_mode" in docstring
        assert "set_execution_mode" in docstring
        assert "reset_execution_mode" in docstring


# ═══════════════════════════════════════════════════════════════════════════
# E — Frontend convergence
# ═══════════════════════════════════════════════════════════════════════════


class TestFrontendConvergence:
    """Frontend has no stale legacy references."""

    @pytest.fixture(autouse=True)
    def _load_assets(self):
        self.html = _DATA_HEALTH_HTML.read_text(encoding="utf-8")
        self.js = _DATA_HEALTH_JS.read_text(encoding="utf-8")

    def test_no_old_model_source_url_constant(self):
        """MODEL_SOURCE_URL constant must not exist in data_health.js."""
        assert "MODEL_SOURCE_URL" not in self.js

    def test_no_active_model_source_variable(self):
        """_activeModelSource variable must not exist."""
        assert "_activeModelSource" not in self.js

    def test_no_dhExecutionModeCard_element(self):
        """Old standalone execution mode card element removed."""
        assert 'id="dhExecutionModeCard"' not in self.html

    def test_no_dhExecutionModeToggle_element(self):
        assert 'id="dhExecutionModeToggle"' not in self.html

    def test_no_dhExecutionModeMeta_element(self):
        assert 'id="dhExecutionModeMeta"' not in self.html

    def test_no_dhExecutionModeFeedback_element(self):
        assert 'id="dhExecutionModeFeedback"' not in self.html

    def test_unified_card_present(self):
        """Unified Model & Routing Mode card exists."""
        assert 'id="dhModelSourceCard"' in self.html
        assert "Model &amp; Routing Mode" in self.html

    def test_js_uses_execution_mode_endpoint(self):
        """JS code talks to the authoritative execution-mode endpoint."""
        assert "/api/admin/routing/execution-mode" in self.js

    def test_unified_js_functions_present(self):
        """renderModelSourceState and fetchModelSourceState exist."""
        assert "renderModelSourceState" in self.js
        assert "fetchModelSourceState" in self.js


# ═══════════════════════════════════════════════════════════════════════════
# F — Runtime config coexistence
# ═══════════════════════════════════════════════════════════════════════════


class TestRuntimeConfigCoexistence:
    """model_source and execution_mode coexist independently in config."""

    def test_different_config_keys(self):
        """model_state and execution_mode_state use different JSON keys."""
        ms_src = (_SERVICES_ROOT / "model_state.py").read_text(encoding="utf-8")
        em_src = (_SERVICES_ROOT / "execution_mode_state.py").read_text(encoding="utf-8")
        assert '"model_source"' in ms_src
        assert '"execution_mode"' in em_src
        # They should NOT cross-reference each other's keys
        assert '"execution_mode"' not in ms_src
        assert '"model_source"' not in em_src

    def test_both_persist_to_same_file_safely(self):
        """Both modules use merge-persist (read-modify-write), not overwrite."""
        ms_src = (_SERVICES_ROOT / "model_state.py").read_text(encoding="utf-8")
        em_src = (_SERVICES_ROOT / "execution_mode_state.py").read_text(encoding="utf-8")
        # Both should read existing data before writing
        assert "json.loads" in ms_src
        assert "json.loads" in em_src
        # Neither should do bare write_text without reading first
        for src, name in [(ms_src, "model_state"), (em_src, "execution_mode_state")]:
            persist_fn = src[src.index("def _persist"):]
            persist_fn = persist_fn[:persist_fn.index("\ndef ") if "\ndef " in persist_fn else len(persist_fn)]
            assert "json.loads" in persist_fn, f"{name}._persist must merge, not overwrite"

    def test_valid_modes_covers_all_execution_modes(self):
        """VALID_MODES includes all ExecutionMode enum values."""
        from app.services.model_routing_contract import VALID_MODES, ExecutionMode
        for em in ExecutionMode:
            assert em.value in VALID_MODES, f"{em.value} missing from VALID_MODES"

    def test_valid_source_keys_covers_all_model_sources(self):
        """VALID_SOURCE_KEYS includes all MODEL_SOURCES keys."""
        from app.model_sources import MODEL_SOURCES, VALID_SOURCE_KEYS
        for key in MODEL_SOURCES:
            assert key in VALID_SOURCE_KEYS
