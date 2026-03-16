"""Tests for the workflow pivot cleanup boundary (Prompt 0).

Verifies:
1. Pipeline runtime modules are quarantined and not importable from production paths
2. Preserved reusable domain modules still import cleanly
3. FastAPI app creates without pipeline routes
4. Quarantine __init__.py deprecation notice exists
"""

import importlib
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# 1. Pipeline modules are NOT importable from their old production paths
# ---------------------------------------------------------------------------

_QUARANTINED_MODULES = [
    "app.services.pipeline_orchestrator",
    "app.services.pipeline_run_contract",
    "app.services.pipeline_artifact_store",
    "app.services.pipeline_run_store",
    "app.services.pipeline_market_stage",
    "app.services.pipeline_market_model_stage",
    "app.services.pipeline_stock_scanners_stage",
    "app.services.pipeline_options_scanners_stage",
    "app.services.pipeline_scanner_stage",
    "app.services.pipeline_candidate_selection_stage",
    "app.services.pipeline_context_assembly_stage",
    "app.services.pipeline_candidate_enrichment_stage",
    "app.services.pipeline_event_context_stage",
    "app.services.pipeline_portfolio_policy_stage",
    "app.services.pipeline_trade_decision_packet_stage",
    "app.services.pipeline_decision_prompt_payload_stage",
    "app.services.pipeline_final_recommendation_stage",
    "app.services.pipeline_final_response_stage",
    "app.services.context_assembler",
    "app.services.trade_decision_orchestrator",
    "app.api.routes_pipeline_monitor",
]


@pytest.mark.parametrize("module_path", _QUARANTINED_MODULES)
def test_quarantined_module_not_importable(module_path: str) -> None:
    """Old pipeline modules must NOT be importable from their original paths."""
    # Clear cached modules so we get a fresh import attempt
    if module_path in sys.modules:
        del sys.modules[module_path]
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_path)


# ---------------------------------------------------------------------------
# 2. Preserved reusable domain modules still import cleanly
# ---------------------------------------------------------------------------

_PRESERVED_DOMAIN_MODULES = [
    # Market engines
    "app.services.breadth_engine",
    "app.services.breadth_service",
    "app.services.volatility_options_engine",
    "app.services.volatility_options_service",
    "app.services.cross_asset_macro_engine",
    "app.services.cross_asset_macro_service",
    "app.services.flows_positioning_engine",
    "app.services.flows_positioning_service",
    "app.services.liquidity_conditions_engine",
    "app.services.liquidity_conditions_service",
    "app.services.news_sentiment_engine",
    "app.services.news_sentiment_service",
    # Contracts
    "app.services.engine_output_contract",
    "app.services.scanner_candidate_contract",
    "app.services.model_analysis_contract",
    "app.services.decision_response_contract",
    "app.services.decision_policy",
    "app.services.decision_prompt_payload",
    "app.services.dashboard_metadata_contract",
    # Scanner V2
    "app.services.scanner_v2.base_scanner",
    "app.services.scanner_v2.contracts",
    "app.services.scanner_v2.registry",
    "app.services.scanner_v2.phases",
    # Evaluation
    "app.services.evaluation.scoring",
    "app.services.evaluation.gates",
    "app.services.evaluation.ranking",
    # Domain services
    "app.services.confidence_framework",
    "app.services.conflict_detector",
    "app.services.market_composite",
    "app.services.market_context_service",
    "app.services.portfolio_risk_engine",
    "app.services.risk_policy_service",
    "app.services.signal_service",
    "app.services.regime_service",
    "app.services.decision_service",
    "app.services.trade_lifecycle_service",
    "app.services.active_trade_monitor_service",
    "app.services.active_trade_pipeline",
    # Utils
    "app.utils.normalize",
    "app.utils.computed_metrics",
    "app.utils.validation",
    "app.utils.strategy_id_resolver",
    "app.utils.expected_fill",
    "app.utils.tone_classification",
    "app.utils.time_horizon",
    # Common
    "common.trade_analysis_engine",
    "common.json_repair",
    "common.quant_analysis",
    "common.model_sanitize",
    # Models
    "app.models.trade_contract",
]


@pytest.mark.parametrize("module_path", _PRESERVED_DOMAIN_MODULES)
def test_preserved_domain_module_imports(module_path: str) -> None:
    """Reusable domain modules must still import cleanly after cleanup."""
    mod = importlib.import_module(module_path)
    assert isinstance(mod, types.ModuleType)


# ---------------------------------------------------------------------------
# 3. Quarantine package has deprecation notice
# ---------------------------------------------------------------------------


def test_quarantine_package_has_deprecation_notice() -> None:
    """The _deprecated_pipeline package must exist and contain a deprecation notice."""
    mod = importlib.import_module("app.services._deprecated_pipeline")
    assert mod.__doc__ is not None
    assert "DEPRECATED" in mod.__doc__
    assert "workflow pivot" in mod.__doc__.lower()


# ---------------------------------------------------------------------------
# 4. FastAPI app starts without pipeline routes
# ---------------------------------------------------------------------------


def test_app_creates_without_pipeline_routes() -> None:
    """The FastAPI app must create successfully with no pipeline monitor routes."""
    from app.main import create_app

    app = create_app()

    route_paths = [r.path for r in app.routes if hasattr(r, "path")]
    pipeline_routes = [p for p in route_paths if "/pipeline" in p.lower()]

    # pipeline_monitor routes should be gone
    assert not any("/pipeline/runs" in p for p in pipeline_routes), (
        f"Pipeline monitor routes still registered: {pipeline_routes}"
    )
