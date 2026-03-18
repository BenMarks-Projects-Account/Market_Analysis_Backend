"""Tests for Step 18 — Wire execution mode into live routing dispatch + unified UI.

Covers:
    A — resolve_effective_execution_mode precedence rules
    B — execute_routed_model execution_mode parameter wiring
    C — TMC wrapper passes explicit online_distributed
    D — _model_transport inherits UI-selected mode via execute_routed_model
    E — Frontend: unified Model & Routing Mode card (HTML)
    F — Frontend: unified JS uses execution-mode endpoint
    G — Frontend: Execution Mode card removed
    H — Backward compatibility: resolve_routing_mode still works
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
_FRONTEND_ROOT = _BACKEND_ROOT.parent / "frontend"
_DATA_HEALTH_JS = _FRONTEND_ROOT / "assets" / "js" / "pages" / "data_health.js"
_DATA_HEALTH_HTML = _FRONTEND_ROOT / "dashboards" / "data_health.html"


# ═══════════════════════════════════════════════════════════════════════════
# A — resolve_effective_execution_mode precedence rules
# ═══════════════════════════════════════════════════════════════════════════


class TestResolveEffectiveExecutionMode:
    """Precedence: premium > caller_mode > UI-selected > fallback."""

    def test_returns_tuple(self):
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local_distributed",
        ):
            result = resolve_effective_execution_mode("generic_task")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_fallback_uses_ui_selected_mode(self):
        """When no overrides, use UI-selected mode."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="online_distributed",
        ):
            mode, premium = resolve_effective_execution_mode("generic_task")
        assert mode == "online_distributed"
        assert premium is False

    def test_caller_mode_overrides_ui_selected(self):
        """Explicit caller_mode takes precedence over UI-selected mode."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local_distributed",
        ):
            mode, premium = resolve_effective_execution_mode(
                "generic_task", caller_mode="model_machine",
            )
        assert mode == "model_machine"
        assert premium is False

    def test_premium_overrides_caller_mode(self):
        """Premium for eligible tasks takes highest precedence."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local_distributed",
        ):
            mode, premium = resolve_effective_execution_mode(
                "tmc_final_decision",
                premium=True,
                caller_mode="model_machine",
            )
        assert mode == "premium_online"
        assert premium is True

    def test_premium_ignored_for_non_eligible_task(self):
        """Premium flag ignored for tasks not in _PREMIUM_ELIGIBLE_TASKS."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local_distributed",
        ):
            mode, premium = resolve_effective_execution_mode(
                "market_picture_interpretation", premium=True,
            )
        assert mode == "local_distributed"
        assert premium is False

    def test_invalid_caller_mode_falls_through_to_ui(self):
        """Invalid caller_mode is ignored, UI-selected mode used."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="online_distributed",
        ):
            mode, _ = resolve_effective_execution_mode(
                "generic_task", caller_mode="invalid_mode_xyz",
            )
        assert mode == "online_distributed"

    def test_ui_mode_local(self):
        """UI-selected 'local' mode is respected."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local",
        ):
            mode, premium = resolve_effective_execution_mode("regime_analysis")
        assert mode == "local"
        assert premium is False

    def test_ui_mode_model_machine(self):
        """UI-selected 'model_machine' mode is respected."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="model_machine",
        ):
            mode, _ = resolve_effective_execution_mode("stock_idea_analysis")
        assert mode == "model_machine"

    def test_ui_mode_premium_online(self):
        """UI-selected 'premium_online' mode is respected for non-premium calls."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="premium_online",
        ):
            mode, premium = resolve_effective_execution_mode("generic_task")
        assert mode == "premium_online"
        assert premium is False

    def test_fallback_default_when_ui_returns_empty(self):
        """When UI returns empty string, fallback to DEFAULT_ROUTED_MODE."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
            DEFAULT_ROUTED_MODE,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="",
        ):
            mode, _ = resolve_effective_execution_mode("generic_task")
        assert mode == DEFAULT_ROUTED_MODE

    def test_caller_mode_none_uses_ui(self):
        """caller_mode=None explicitly falls through to UI mode."""
        from app.services.model_routing_integration import (
            resolve_effective_execution_mode,
        )
        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="online_distributed",
        ):
            mode, _ = resolve_effective_execution_mode(
                "generic_task", caller_mode=None,
            )
        assert mode == "online_distributed"


# ═══════════════════════════════════════════════════════════════════════════
# B — execute_routed_model execution_mode parameter wiring
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteRoutedModelModeWiring:
    """Verify that execute_routed_model passes execution_mode through."""

    def _make_mock_result(self):
        from app.services.model_provider_base import ProviderResult
        return ProviderResult(
            success=True,
            content="test response",
            provider="test_provider",
        )

    def _make_mock_trace(self, mode="local_distributed"):
        from app.services.model_routing_contract import ExecutionTrace
        return ExecutionTrace(
            request_id="test-001",
            requested_mode=mode,
            resolved_mode=mode,
        )

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    @patch("app.services.model_router.route_and_execute")
    def test_execution_mode_passed_to_resolver(self, mock_route, mock_enabled):
        """execution_mode param should propagate to ExecutionRequest."""
        from app.services.model_routing_integration import execute_routed_model

        mock_route.return_value = (self._make_mock_result(), self._make_mock_trace("model_machine"))

        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local_distributed",
        ):
            result, trace = execute_routed_model(
                task_type="test_task",
                messages=[{"role": "user", "content": "hello"}],
                execution_mode="model_machine",
            )

        # Verify the request was built with model_machine mode
        call_args = mock_route.call_args
        request = call_args[0][0]
        assert request.mode == "model_machine"

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    @patch("app.services.model_router.route_and_execute")
    def test_no_execution_mode_uses_ui_selected(self, mock_route, mock_enabled):
        """Without execution_mode, UI-selected mode is used."""
        from app.services.model_routing_integration import execute_routed_model

        mock_route.return_value = (self._make_mock_result(), self._make_mock_trace("online_distributed"))

        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="online_distributed",
        ):
            result, trace = execute_routed_model(
                task_type="test_task",
                messages=[{"role": "user", "content": "hello"}],
            )

        call_args = mock_route.call_args
        request = call_args[0][0]
        assert request.mode == "online_distributed"

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    @patch("app.services.model_router.route_and_execute")
    def test_premium_overrides_execution_mode(self, mock_route, mock_enabled):
        """premium=True for eligible task overrides explicit execution_mode."""
        from app.services.model_routing_integration import execute_routed_model

        mock_route.return_value = (self._make_mock_result(), self._make_mock_trace("premium_online"))

        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local_distributed",
        ):
            result, trace = execute_routed_model(
                task_type="tmc_final_decision",
                messages=[{"role": "user", "content": "decide"}],
                premium=True,
                execution_mode="model_machine",
            )

        call_args = mock_route.call_args
        request = call_args[0][0]
        assert request.mode == "premium_online"
        assert request.premium_override is True

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    @patch("app.services.model_router.route_and_execute")
    def test_execution_mode_none_same_as_omitted(self, mock_route, mock_enabled):
        """execution_mode=None should behave as if not specified."""
        from app.services.model_routing_integration import execute_routed_model

        mock_route.return_value = (self._make_mock_result(), self._make_mock_trace())

        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="local_distributed",
        ):
            result, trace = execute_routed_model(
                task_type="analysis",
                messages=[{"role": "user", "content": "analyze"}],
                execution_mode=None,
            )

        call_args = mock_route.call_args
        request = call_args[0][0]
        assert request.mode == "local_distributed"


# ═══════════════════════════════════════════════════════════════════════════
# C — TMC wrapper passes explicit online_distributed
# ═══════════════════════════════════════════════════════════════════════════


class TestTMCWrapperExplicitMode:
    """routed_tmc_final_decision should force online_distributed."""

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    @patch("app.services.model_routing_integration.execute_routed_model")
    def test_tmc_passes_online_distributed(self, mock_execute, mock_enabled):
        """TMC wrapper should pass execution_mode='online_distributed'."""
        from app.services.model_routing_integration import routed_tmc_final_decision

        # Mock prompt module
        mock_sys_prompt = "You are a TMC."
        mock_user_prompt = "Evaluate this trade."

        with patch.dict("sys.modules", {
            "common.tmc_final_decision_prompts": MagicMock(
                TMC_FINAL_DECISION_SYSTEM_PROMPT=mock_sys_prompt,
                build_tmc_final_decision_prompt=MagicMock(return_value=mock_user_prompt),
            ),
        }):
            # Make execute_routed_model return a valid result
            from app.services.model_routing_contract import ExecutionTrace
            trace = ExecutionTrace(
                request_id="tmc-001",
                requested_mode="online_distributed",
                resolved_mode="online_distributed",
            )
            mock_execute.return_value = (
                {
                    "status": "success",
                    "content": '{"decision":"ACCEPT","conviction":"high","reasoning":"test"}',
                },
                trace,
            )

            with patch(
                "app.services.execution_mode_state.get_execution_mode",
                return_value="local_distributed",
            ):
                routed_tmc_final_decision(
                    candidate={"symbol": "SPY"},
                    market_picture_context=None,
                )

        # Verify execute_routed_model was called with execution_mode
        call_kwargs = mock_execute.call_args[1]
        assert call_kwargs["execution_mode"] == "online_distributed"
        assert call_kwargs["task_type"] == "tmc_final_decision"


# ═══════════════════════════════════════════════════════════════════════════
# D — _model_transport inherits UI-selected mode
# ═══════════════════════════════════════════════════════════════════════════


class TestModelTransportInheritsUIMode:
    """_model_transport doesn't pass execution_mode, so UI-selected is used."""

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    @patch("app.services.model_router.route_and_execute")
    def test_transport_uses_ui_mode(self, mock_route, mock_enabled):
        """Calls through _model_transport should use UI-selected mode."""
        from app.services.model_provider_base import ProviderResult
        from app.services.model_routing_contract import ExecutionTrace

        mock_result = ProviderResult(
            success=True,
            content="test response content",
            provider="localhost_llm",
        )
        mock_trace = ExecutionTrace(
            request_id="transport-001",
            requested_mode="model_machine",
            resolved_mode="model_machine",
            selected_provider="network_model_machine",
        )
        mock_route.return_value = (mock_result, mock_trace)

        with patch(
            "app.services.execution_mode_state.get_execution_mode",
            return_value="model_machine",
        ):
            from common.model_analysis import _model_transport
            result = _model_transport(
                task_type="regime_analysis",
                payload={
                    "messages": [
                        {"role": "system", "content": "Analyze the following."},
                        {"role": "user", "content": "What is the market regime?"},
                    ],
                },
                log_prefix="TEST",
            )

        assert result.transport_path == "routed"
        # Check that route_and_execute received the UI-selected mode
        call_args = mock_route.call_args
        request = call_args[0][0]
        assert request.mode == "model_machine"


# ═══════════════════════════════════════════════════════════════════════════
# E — Frontend: unified Model & Routing Mode card (HTML)
# ═══════════════════════════════════════════════════════════════════════════


class TestFrontendUnifiedCard:
    """data_health.html has unified Model & Routing Mode card."""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = _DATA_HEALTH_HTML.read_text(encoding="utf-8")

    def test_unified_card_exists(self):
        assert 'id="dhModelSourceCard"' in self.html

    def test_unified_card_title(self):
        assert "Model &amp; Routing Mode" in self.html

    def test_unified_toggle_container(self):
        assert 'id="dhModelSourceToggle"' in self.html

    def test_unified_meta_element(self):
        assert 'id="dhModelSourceMeta"' in self.html

    def test_unified_feedback_element(self):
        assert 'id="dhModelSourceFeedback"' in self.html

    def test_toggle_uses_exec_mode_class(self):
        """Toggle container uses dh-exec-mode-toggle class for consistent styling."""
        assert 'class="dh-exec-mode-toggle"' in self.html

    def test_no_separate_execution_mode_card(self):
        """The separate Execution Mode card should be removed."""
        assert 'id="dhExecutionModeCard"' not in self.html

    def test_no_separate_execution_mode_toggle(self):
        assert 'id="dhExecutionModeToggle"' not in self.html

    def test_no_separate_execution_mode_meta(self):
        assert 'id="dhExecutionModeMeta"' not in self.html

    def test_no_separate_execution_mode_feedback(self):
        assert 'id="dhExecutionModeFeedback"' not in self.html


# ═══════════════════════════════════════════════════════════════════════════
# F — Frontend: unified JS uses execution-mode endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestFrontendUnifiedJS:
    """data_health.js uses the execution-mode API endpoint for the unified card."""

    @pytest.fixture(autouse=True)
    def _load_js(self):
        self.js = _DATA_HEALTH_JS.read_text(encoding="utf-8")

    def test_execution_mode_url_defined(self):
        assert "EXECUTION_MODE_URL" in self.js
        assert "/api/admin/routing/execution-mode" in self.js

    def test_no_model_source_url(self):
        """Old MODEL_SOURCE_URL constant should be removed."""
        assert "MODEL_SOURCE_URL" not in self.js

    def test_render_model_source_state_exists(self):
        """renderModelSourceState function should exist (now renders execution modes)."""
        assert "function renderModelSourceState" in self.js

    def test_fetch_model_source_uses_execution_mode(self):
        """fetchModelSourceState should fetch from execution-mode endpoint."""
        assert "fetch(EXECUTION_MODE_URL" in self.js

    def test_set_model_source_posts_mode(self):
        """setModelSource should POST mode to execution-mode endpoint."""
        # Verify it sends { mode: ... } not { source: ... }
        assert "JSON.stringify({ mode: mode })" in self.js

    def test_creates_exec_mode_buttons(self):
        """Buttons should use dh-exec-mode-btn class."""
        assert "dh-exec-mode-btn" in self.js

    def test_group_labels_rendered(self):
        """Should render Distributed and Direct group labels."""
        assert "'Distributed'" in self.js
        assert "'Direct'" in self.js

    def test_no_old_execution_mode_references(self):
        """Old executionModeToggleEl and renderExecutionModeState references removed."""
        assert "executionModeToggleEl" not in self.js
        assert "renderExecutionModeState" not in self.js
        assert "fetchExecutionModeState" not in self.js
        assert "setExecutionMode" not in self.js

    def test_feedback_element_reference(self):
        """Feedback shows on modelSourceFeedbackEl."""
        assert "modelSourceFeedbackEl" in self.js

    def test_model_source_toggle_click_handler(self):
        """Click handler on modelSourceToggleEl dispatches mode change."""
        assert "modelSourceToggleEl.addEventListener" in self.js

    def test_load_data_health_fetches_unified(self):
        """loadDataHealth should call fetchModelSourceState (not separate fetch)."""
        assert "fetchModelSourceState" in self.js
        # Should NOT have separate fetchExecutionModeState
        assert "fetchExecutionModeState" not in self.js


# ═══════════════════════════════════════════════════════════════════════════
# G — Frontend: Execution Mode card removed
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionModeCardRemoved:
    """The separate Execution Mode card must not exist in HTML."""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = _DATA_HEALTH_HTML.read_text(encoding="utf-8")

    def test_no_execution_mode_card_element(self):
        assert 'id="dhExecutionModeCard"' not in self.html

    def test_no_execution_mode_panel_element(self):
        assert 'id="dhExecutionModePanel"' not in self.html


# ═══════════════════════════════════════════════════════════════════════════
# H — Backward compatibility: resolve_routing_mode still works
# ═══════════════════════════════════════════════════════════════════════════


class TestResolveRoutingModeBackcompat:
    """Old resolve_routing_mode still exists and works for backward compat."""

    def test_resolve_routing_mode_still_importable(self):
        from app.services.model_routing_integration import resolve_routing_mode
        assert callable(resolve_routing_mode)

    def test_resolve_routing_mode_tmc_returns_online(self):
        from app.services.model_routing_integration import resolve_routing_mode
        mode, _ = resolve_routing_mode("tmc_final_decision")
        assert mode == "online_distributed"

    def test_resolve_routing_mode_generic_returns_local_distributed(self):
        from app.services.model_routing_integration import resolve_routing_mode
        mode, _ = resolve_routing_mode("generic_task")
        assert mode == "local_distributed"

    def test_resolve_routing_mode_premium(self):
        from app.services.model_routing_integration import resolve_routing_mode
        mode, premium = resolve_routing_mode("tmc_final_decision", premium=True)
        assert mode == "online_distributed"
        assert premium is True

    def test_both_resolvers_coexist(self):
        from app.services.model_routing_integration import (
            resolve_routing_mode,
            resolve_effective_execution_mode,
        )
        assert resolve_routing_mode is not resolve_effective_execution_mode
