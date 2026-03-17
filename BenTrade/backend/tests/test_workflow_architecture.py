"""Tests for app.workflows — definitions and architecture modules.

Focused tests only.  Does not import deprecated pipeline code.
"""

from __future__ import annotations

import importlib
import sys

import pytest

# ── Import targets ────────────────────────────────────────────────────
from app.workflows.definitions import (
    ACTIVE_TRADE_STAGES,
    MARKET_INTELLIGENCE_STAGES,
    OPTIONS_OPPORTUNITY_STAGES,
    OWNERSHIP,
    STOCK_OPPORTUNITY_STAGES,
    WORKFLOW_STAGES,
    WORKFLOW_VERSION,
    StageSpec,
    WorkflowID,
    WorkflowOwnership,
    get_stage_keys,
    get_stage_spec,
)
from app.workflows.architecture import (
    ARTIFACT_SPECS,
    BOUNDARY_RULES,
    DEFAULT_FRESHNESS_POLICY,
    FRESHNESS_DEGRADE_THRESHOLD_SECONDS,
    FRESHNESS_WARN_THRESHOLD_SECONDS,
    ArtifactKind,
    ArtifactSpec,
    BoundaryRule,
    FreshnessPolicy,
    get_artifact_spec,
    get_boundary_rules_for,
)


# ═══════════════════════════════════════════════════════════════════════
# Package-level tests
# ═══════════════════════════════════════════════════════════════════════


class TestPackageIntegrity:
    """Verify the workflows package imports cleanly."""

    def test_package_imports(self):
        mod = importlib.import_module("app.workflows")
        assert mod.__doc__ is not None

    def test_definitions_imports(self):
        mod = importlib.import_module("app.workflows.definitions")
        assert hasattr(mod, "WorkflowID")

    def test_architecture_imports(self):
        mod = importlib.import_module("app.workflows.architecture")
        assert hasattr(mod, "ArtifactKind")

    def test_no_deprecated_pipeline_imported(self):
        """Ensure no deprecated pipeline module leaks into workflows."""
        loaded = [
            k for k in sys.modules
            if "_deprecated_pipeline" in k
        ]
        assert loaded == [], f"Deprecated modules loaded: {loaded}"


# ═══════════════════════════════════════════════════════════════════════
# WorkflowID enum tests
# ═══════════════════════════════════════════════════════════════════════


class TestWorkflowID:
    """Verify enum values and completeness."""

    def test_four_workflows_defined(self):
        assert len(WorkflowID) == 4

    @pytest.mark.parametrize("wid,value", [
        (WorkflowID.MARKET_INTELLIGENCE, "market_intelligence"),
        (WorkflowID.STOCK_OPPORTUNITY, "stock_opportunity"),
        (WorkflowID.OPTIONS_OPPORTUNITY, "options_opportunity"),
        (WorkflowID.ACTIVE_TRADE, "active_trade"),
    ])
    def test_workflow_id_values(self, wid, value):
        assert wid.value == value

    def test_str_enum(self):
        assert isinstance(WorkflowID.MARKET_INTELLIGENCE, str)
        assert WorkflowID.MARKET_INTELLIGENCE == "market_intelligence"


# ═══════════════════════════════════════════════════════════════════════
# StageSpec tests
# ═══════════════════════════════════════════════════════════════════════


class TestStageSpec:
    """Verify StageSpec is frozen and has correct defaults."""

    def test_frozen(self):
        s = StageSpec(key="x", label="X", description="test")
        with pytest.raises(AttributeError):
            s.key = "y"  # type: ignore[misc]

    def test_produces_artifact_default(self):
        s = StageSpec(key="x", label="X", description="test")
        assert s.produces_artifact is True

    def test_produces_artifact_override(self):
        s = StageSpec(key="x", label="X", description="test", produces_artifact=False)
        assert s.produces_artifact is False


# ═══════════════════════════════════════════════════════════════════════
# Stage map tests
# ═══════════════════════════════════════════════════════════════════════


class TestStageMaps:
    """Verify stage maps have correct shape and no duplicates."""

    def test_market_intelligence_has_5_stages(self):
        assert len(MARKET_INTELLIGENCE_STAGES) == 5

    def test_stock_opportunity_has_5_stages(self):
        assert len(STOCK_OPPORTUNITY_STAGES) == 5

    def test_options_opportunity_has_5_stages(self):
        assert len(OPTIONS_OPPORTUNITY_STAGES) == 5

    def test_active_trade_is_empty(self):
        assert len(ACTIVE_TRADE_STAGES) == 0

    def test_all_stages_are_stage_spec(self):
        for wid in WorkflowID:
            for stage in WORKFLOW_STAGES[wid]:
                assert isinstance(stage, StageSpec)

    @pytest.mark.parametrize("wid", list(WorkflowID))
    def test_stage_keys_unique_within_workflow(self, wid):
        keys = get_stage_keys(wid)
        assert len(keys) == len(set(keys)), f"Duplicate stage keys in {wid}"

    def test_market_intelligence_stage_order(self):
        keys = get_stage_keys(WorkflowID.MARKET_INTELLIGENCE)
        assert keys == ("collect", "engine_run", "model_interpret", "composite", "publish")

    def test_stock_opportunity_stage_order(self):
        keys = get_stage_keys(WorkflowID.STOCK_OPPORTUNITY)
        assert keys == ("load_market_state", "scan", "normalize", "enrich_evaluate", "select_package")

    def test_options_opportunity_stage_order(self):
        keys = get_stage_keys(WorkflowID.OPTIONS_OPPORTUNITY)
        assert keys == ("load_market_state", "scan", "validate_math", "enrich_evaluate", "select_package")

    def test_workflow_stages_dict_covers_all_ids(self):
        for wid in WorkflowID:
            assert wid in WORKFLOW_STAGES


# ═══════════════════════════════════════════════════════════════════════
# Helper function tests
# ═══════════════════════════════════════════════════════════════════════


class TestHelpers:
    """Test get_stage_keys and get_stage_spec."""

    def test_get_stage_spec_found(self):
        spec = get_stage_spec(WorkflowID.MARKET_INTELLIGENCE, "collect")
        assert spec is not None
        assert spec.key == "collect"

    def test_get_stage_spec_not_found(self):
        spec = get_stage_spec(WorkflowID.MARKET_INTELLIGENCE, "nonexistent")
        assert spec is None

    def test_get_stage_keys_empty_for_active_trade(self):
        assert get_stage_keys(WorkflowID.ACTIVE_TRADE) == ()


# ═══════════════════════════════════════════════════════════════════════
# Ownership tests
# ═══════════════════════════════════════════════════════════════════════


class TestOwnership:
    """Verify ownership declarations for all workflows."""

    def test_ownership_covers_all_workflows(self):
        for wid in WorkflowID:
            assert wid in OWNERSHIP

    @pytest.mark.parametrize("wid", list(WorkflowID))
    def test_ownership_is_frozen_dataclass(self, wid):
        o = OWNERSHIP[wid]
        assert isinstance(o, WorkflowOwnership)
        with pytest.raises(AttributeError):
            o.owns = ()  # type: ignore[misc]

    def test_market_intelligence_owns_data_collection(self):
        owns = OWNERSHIP[WorkflowID.MARKET_INTELLIGENCE].owns
        assert "market_data_collection" in owns

    def test_market_intelligence_does_not_own_scanners(self):
        not_owns = OWNERSHIP[WorkflowID.MARKET_INTELLIGENCE].does_not_own
        assert "scanner_execution" in not_owns

    def test_stock_workflow_does_not_own_options(self):
        not_owns = OWNERSHIP[WorkflowID.STOCK_OPPORTUNITY].does_not_own
        assert "options_chain_analysis" in not_owns

    def test_options_workflow_owns_trust_hygiene(self):
        owns = OWNERSHIP[WorkflowID.OPTIONS_OPPORTUNITY].owns
        assert "quote_validation_trust_hygiene" in owns

    def test_active_trade_ownership_present(self):
        o = OWNERSHIP[WorkflowID.ACTIVE_TRADE]
        # Active Trade preserves its existing responsibilities
        assert "active_position_monitoring" in o.owns
        assert "scanner_execution" in o.does_not_own


# ═══════════════════════════════════════════════════════════════════════
# Architecture module tests
# ═══════════════════════════════════════════════════════════════════════


class TestArtifactKind:
    """Verify ArtifactKind enum."""

    def test_four_kinds_defined(self):
        assert len(ArtifactKind) == 4

    def test_str_enum(self):
        assert isinstance(ArtifactKind.MARKET_STATE, str)
        assert ArtifactKind.MARKET_STATE == "market_state"


class TestArtifactSpecs:
    """Verify artifact spec catalog."""

    def test_all_kinds_have_specs(self):
        for kind in ArtifactKind:
            assert kind in ARTIFACT_SPECS

    @pytest.mark.parametrize("kind", list(ArtifactKind))
    def test_spec_is_frozen(self, kind):
        spec = ARTIFACT_SPECS[kind]
        assert isinstance(spec, ArtifactSpec)
        with pytest.raises(AttributeError):
            spec.kind = ArtifactKind.MARKET_STATE  # type: ignore[misc]

    def test_market_state_required_keys(self):
        spec = get_artifact_spec(ArtifactKind.MARKET_STATE)
        assert "version" in spec.required_top_level_keys
        assert "engines" in spec.required_top_level_keys
        assert "composite" in spec.required_top_level_keys
        assert "quality" in spec.required_top_level_keys

    def test_options_candidates_includes_filter_trace(self):
        spec = get_artifact_spec(ArtifactKind.OPTIONS_CANDIDATES)
        assert "filter_trace" in spec.required_top_level_keys

    def test_filter_trace_spec_keys(self):
        spec = get_artifact_spec(ArtifactKind.FILTER_TRACE)
        expected = {"preset_name", "resolved_thresholds", "stage_counts",
                     "rejection_reasons", "data_quality_counts"}
        assert set(spec.required_top_level_keys) == expected


class TestFreshnessPolicy:
    """Verify freshness thresholds and policy."""

    def test_default_warn_threshold(self):
        assert FRESHNESS_WARN_THRESHOLD_SECONDS == 600

    def test_default_degrade_threshold(self):
        assert FRESHNESS_DEGRADE_THRESHOLD_SECONDS == 1800

    def test_warn_before_degrade(self):
        assert FRESHNESS_WARN_THRESHOLD_SECONDS < FRESHNESS_DEGRADE_THRESHOLD_SECONDS

    def test_default_policy_allows_stale(self):
        assert DEFAULT_FRESHNESS_POLICY.allow_stale is True

    def test_custom_policy(self):
        p = FreshnessPolicy(warn_after_seconds=120, degrade_after_seconds=300, allow_stale=False)
        assert p.warn_after_seconds == 120
        assert p.allow_stale is False


class TestBoundaryRules:
    """Verify cross-workflow boundary rules."""

    def test_rules_not_empty(self):
        assert len(BOUNDARY_RULES) > 0

    def test_all_rules_are_boundary_rule(self):
        for r in BOUNDARY_RULES:
            assert isinstance(r, BoundaryRule)

    def test_no_cross_scanner_dependency_exists(self):
        rule = next(r for r in BOUNDARY_RULES if r.name == "no_cross_scanner_dependency")
        assert rule.mechanism == "none"

    def test_market_state_consumption_is_artifact_read(self):
        rule = next(r for r in BOUNDARY_RULES if r.name == "market_state_consumption")
        assert rule.mechanism == "artifact_read"
        assert rule.from_workflow == "market_intelligence"
        assert rule.to_workflow == "stock_opportunity"

    def test_tmc_triggers_via_api_call(self):
        tmc_rules = [r for r in BOUNDARY_RULES if r.from_workflow == "tmc"]
        assert len(tmc_rules) >= 2
        assert all(r.mechanism == "api_call" for r in tmc_rules)

    def test_get_boundary_rules_for_returns_matches(self):
        mi_rules = get_boundary_rules_for("market_intelligence")
        assert len(mi_rules) >= 2
        names = [r.name for r in mi_rules]
        assert "market_state_consumption" in names

    def test_get_boundary_rules_for_unknown_returns_empty(self):
        assert get_boundary_rules_for("nonexistent") == ()


# ═══════════════════════════════════════════════════════════════════════
# Version constant test
# ═══════════════════════════════════════════════════════════════════════


class TestVersion:
    def test_workflow_version_is_string(self):
        assert isinstance(WORKFLOW_VERSION, str)
        assert WORKFLOW_VERSION == "1.0"
