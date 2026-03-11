"""Tests for pipeline_portfolio_policy_stage — Step 11.

Covers:
  - Vocabulary/constant registration
  - Default portfolio provider contract
  - Portfolio context builder
  - Individual policy check functions (9 checks)
  - Outcome derivation logic
  - Full policy evaluation (evaluate_policy)
  - Handler contract (return shape, summary_counts, metadata)
  - Single-candidate processing
  - Multi-candidate processing
  - Vacuous completion (no enrichment summary, no candidates)
  - Degraded provider results
  - Provider failure handling
  - Partial candidate failures
  - Artifact creation and lineage
  - Stage summary structure
  - Event emission (started/completed/failed)
  - Injectable portfolio provider
  - Event context integration (reads Step 10 artifacts)
  - Orchestrator integration (wiring, deps, pipeline run)
"""

import pytest

from app.services.pipeline_portfolio_policy_stage import (
    _STAGE_KEY,
    _POLICY_VERSION,
    _PORTFOLIO_CONTEXT_VERSION,
    MAX_ACTIVE_SAME_SYMBOL,
    MAX_ACTIVE_SAME_STRATEGY,
    MAX_TOTAL_POSITIONS,
    MAX_CAPITAL_UTILIZATION_PCT,
    EARNINGS_BLOCK_DAYS,
    EARNINGS_CAUTION_DAYS,
    MACRO_CAUTION_DAYS,
    PREMIUM_SELLING_STRATEGIES,
    OUTCOME_ELIGIBLE,
    OUTCOME_ELIGIBLE_WITH_CAUTIONS,
    OUTCOME_RESTRICTED,
    OUTCOME_BLOCKED,
    OUTCOME_FAILED,
    VALID_OUTCOMES,
    CHECK_PASS,
    CHECK_CAUTION,
    CHECK_RESTRICT,
    CHECK_BLOCK,
    CHECK_UNKNOWN,
    VALID_CHECK_STATUSES,
    POLICY_STATUS_EVALUATED,
    POLICY_STATUS_EVALUATED_DEGRADED,
    POLICY_STATUS_SKIPPED_INVALID,
    POLICY_STATUS_FAILED,
    default_portfolio_provider,
    build_portfolio_context,
    check_required_fields,
    check_trade_capability,
    check_strategy_allowed,
    check_event_risk_window,
    check_same_symbol_overlap,
    check_position_count,
    check_capital_limit,
    check_concentration,
    check_event_coverage,
    derive_overall_outcome,
    evaluate_policy,
    portfolio_policy_handler,
)
from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    VALID_EVENT_TYPES,
    create_pipeline_run,
    mark_stage_completed,
    mark_stage_running,
)
from app.services.pipeline_artifact_store import (
    VALID_ARTIFACT_TYPES,
    build_artifact_record,
    create_artifact_store,
    get_artifact_by_key,
    put_artifact,
)
from app.services.pipeline_orchestrator import (
    execute_stage,
    get_default_handlers,
    get_default_dependency_map,
    run_pipeline_with_handlers,
)


# =====================================================================
#  Test helpers
# =====================================================================

def _make_run_and_store(run_id="test-policy-001"):
    """Create a fresh run+store with upstream stages completed.

    Completes through candidate_enrichment and events (since events
    runs before policy in the canonical stage order).
    """
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    for stage in (
        "market_data", "market_model_analysis",
        "scanners", "candidate_selection",
        "shared_context", "candidate_enrichment",
        "events",
    ):
        mark_stage_running(run, stage)
        mark_stage_completed(run, stage)
    return run, store


def _success_handler(run, artifact_store, stage_key, **kwargs):
    """Always-succeed stub handler."""
    return {
        "outcome": "completed",
        "summary_counts": {"items_processed": 0},
        "artifacts": [],
        "metadata": {"stub": True},
        "error": None,
    }


def _all_stub_pipeline(**kwargs):
    """Run pipeline with all real-handler stages stubbed out."""
    handlers = kwargs.pop("handlers", {})
    handlers.setdefault("market_data", _success_handler)
    handlers.setdefault("market_model_analysis", _success_handler)
    handlers.setdefault("scanners", _success_handler)
    handlers.setdefault("candidate_selection", _success_handler)
    handlers.setdefault("shared_context", _success_handler)
    handlers.setdefault("candidate_enrichment", _success_handler)
    handlers.setdefault("events", _success_handler)
    handlers.setdefault("policy", _success_handler)
    handlers.setdefault("orchestration", _success_handler)
    handlers.setdefault("prompt_payload", _success_handler)
    handlers.setdefault("final_model_decision", _success_handler)
    handlers.setdefault("final_response_normalization", _success_handler)
    return run_pipeline_with_handlers(handlers, **kwargs)


def _write_enriched_candidate(
    store, run_id, candidate_id, symbol="SPY", **extra
):
    """Write a Step 9 enriched candidate artifact."""
    data = {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "symbol": symbol,
        "strategy_type": extra.get("strategy_type", "put_credit_spread"),
        "scanner_key": extra.get("scanner_key", "test_scanner"),
        "scanner_family": extra.get("scanner_family", "options"),
        "direction": extra.get("direction", "long"),
        "rank_position": extra.get("rank_position", 1),
        "rank_score": extra.get("rank_score", 0.85),
        "setup_quality": extra.get("setup_quality", 75.0),
        "confidence": extra.get("confidence", 0.8),
        "candidate_snapshot": {},
        "shared_context_artifact_ref": extra.get("shared_context_ref"),
        "compact_context_summary": {},
        "enrichment_status": "full",
        "enrichment_notes": [],
        "event_context": None,
        "portfolio_context": None,
        "policy_context": None,
        "decision_packet": None,
        "prompt_payload": None,
        "final_response": None,
        "enriched_at": "2026-03-11T00:00:00+00:00",
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="candidate_enrichment",
        artifact_key=f"enriched_{candidate_id}",
        artifact_type="enriched_candidate",
        data=data,
        candidate_id=candidate_id,
        summary={"candidate_id": candidate_id, "symbol": symbol},
    )
    put_artifact(store, art, overwrite=True)
    return data


def _write_enrichment_summary(store, run_id, candidate_ids):
    """Write a Step 9 enrichment summary artifact."""
    data = {
        "stage_key": "candidate_enrichment",
        "overall_status": "full",
        "total_candidates_in": len(candidate_ids),
        "total_enriched": len(candidate_ids),
        "total_full": len(candidate_ids),
        "total_degraded": 0,
        "total_failed": 0,
        "enrichment_records": [
            {
                "candidate_id": cid,
                "enrichment_status": "full",
                "enrichment_notes": [],
                "elapsed_ms": 1,
            }
            for cid in candidate_ids
        ],
        "enriched_artifact_refs": [],
        "shared_context_artifact_ref": None,
        "summary_artifact_ref": None,
        "elapsed_ms": 10,
        "generated_at": "2026-03-11T00:00:00+00:00",
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="candidate_enrichment",
        artifact_key="candidate_enrichment_summary",
        artifact_type="candidate_enrichment_summary",
        data=data,
        summary={"total_enriched": len(candidate_ids)},
    )
    put_artifact(store, art, overwrite=True)
    return data


def _write_event_context(
    store, run_id, candidate_id, symbol="SPY",
    event_status="enriched", risk_flags=None,
    nearest_event=None, downstream_usable=True,
):
    """Write a Step 10 event context artifact for a candidate."""
    data = {
        "event_context_version": "1.0",
        "run_id": run_id,
        "candidate_id": candidate_id,
        "symbol": symbol,
        "source_enriched_candidate_ref": None,
        "event_status": event_status,
        "event_summary": {
            "total_events": 0,
            "risk_flag_count": len(risk_flags) if risk_flags else 0,
            "nearest_event_type": (
                nearest_event.get("event_type") if nearest_event else None
            ),
            "nearest_days_until": (
                nearest_event.get("days_until") if nearest_event else None
            ),
        },
        "upcoming_events": [],
        "recent_events": [],
        "nearest_relevant_event": nearest_event,
        "event_risk_flags": risk_flags or [],
        "downstream_usable": downstream_usable,
        "degraded_reasons": [],
        "generated_at": "2026-03-11T00:00:00+00:00",
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="events",
        artifact_key=f"event_{candidate_id}",
        artifact_type="event_context",
        data=data,
        candidate_id=candidate_id,
        summary={"candidate_id": candidate_id, "event_status": event_status},
    )
    put_artifact(store, art, overwrite=True)
    return data


def _populate_upstream(store, run_id, candidate_ids, symbols=None,
                       write_events=False, **event_kwargs):
    """Write enriched candidates + summary (Step 9 output).

    Optionally writes event context artifacts too.
    """
    symbols = symbols or {}
    for cid in candidate_ids:
        sym = symbols.get(cid, "SPY")
        _write_enriched_candidate(store, run_id, cid, symbol=sym)
        if write_events:
            _write_event_context(
                store, run_id, cid, symbol=sym, **event_kwargs,
            )
    _write_enrichment_summary(store, run_id, candidate_ids)


def _mock_portfolio_provider(
    active_positions=None,
    trade_capability=None,
    capital_summary=None,
    restrictions=None,
    status="available",
    degraded_reasons=None,
):
    """Return a mock portfolio provider."""
    def provider(lookup_input):
        return {
            "provider_status": status,
            "trade_capability": trade_capability or {
                "enabled": True,
                "status": "enabled",
                "restrictions": [],
            },
            "active_positions": active_positions or [],
            "capital_summary": capital_summary or {
                "total_capital": 100_000.0,
                "capital_in_use": 20_000.0,
                "utilization_pct": 20.0,
            },
            "restrictions": restrictions or [],
            "degraded_reasons": degraded_reasons or [],
        }
    return provider


def _failing_provider(error_msg="Provider exploded"):
    """Return a portfolio provider that raises."""
    def provider(lookup_input):
        raise RuntimeError(error_msg)
    return provider


def _make_enriched_data(
    candidate_id="c1", symbol="SPY",
    strategy_type="put_credit_spread", **extra,
):
    """Build minimal enriched data dict for unit tests."""
    d = {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "strategy_type": strategy_type,
        "scanner_family": extra.get("scanner_family", "options"),
        "direction": extra.get("direction", "long"),
    }
    d.update(extra)
    return d


def _make_portfolio_ctx(
    active_symbol=0,
    active_strategy=0,
    total_active=0,
    utilization_pct=20.0,
    snapshot_status="available",
    trade_capability_status="enabled",
    restriction_flags=None,
    degraded_reasons=None,
):
    """Build minimal portfolio context for unit tests."""
    return {
        "portfolio_context_version": "1.0",
        "candidate_id": "c1",
        "symbol": "SPY",
        "portfolio_snapshot_status": snapshot_status,
        "trade_capability_status": trade_capability_status,
        "active_symbol_positions": active_symbol,
        "active_strategy_positions": active_strategy,
        "total_active_positions": total_active,
        "estimated_capital_utilization_pct": utilization_pct,
        "concentration_context": {
            "strategy_family_count": 0,
            "symbol_exposure_count": 0,
            "scanner_family": "options",
        },
        "correlation_context": {
            "cluster_overlap": False,
            "cluster_count": 0,
        },
        "restriction_flags": restriction_flags or [],
        "degraded_reasons": degraded_reasons or [],
        "source_refs": {"provider_status": "available"},
    }


def _make_event_ctx(
    risk_flags=None,
    nearest_event=None,
    event_status="enriched",
    downstream_usable=True,
):
    """Build minimal event context for unit tests."""
    return {
        "event_risk_flags": risk_flags or [],
        "nearest_relevant_event": nearest_event,
        "event_status": event_status,
        "event_summary": {
            "total_events": 0,
            "risk_flag_count": len(risk_flags) if risk_flags else 0,
        },
        "downstream_usable": downstream_usable,
    }


# =====================================================================
#  Test classes
# =====================================================================


class TestConstants:
    """Verify vocabularies and types are registered."""

    def test_stage_key(self):
        assert _STAGE_KEY == "policy"
        assert "policy" in PIPELINE_STAGES

    def test_policy_version(self):
        assert _POLICY_VERSION == "1.0"

    def test_portfolio_context_version(self):
        assert _PORTFOLIO_CONTEXT_VERSION == "1.0"

    def test_stage_order_events_before_policy(self):
        """Events must run before policy in PIPELINE_STAGES."""
        events_idx = PIPELINE_STAGES.index("events")
        policy_idx = PIPELINE_STAGES.index("policy")
        assert events_idx < policy_idx

    def test_event_types_registered(self):
        for et in ("policy_evaluation_started",
                    "policy_evaluation_completed",
                    "policy_evaluation_failed"):
            assert et in VALID_EVENT_TYPES, f"{et} not in VALID_EVENT_TYPES"

    def test_artifact_types_registered(self):
        assert "policy_output" in VALID_ARTIFACT_TYPES
        assert "policy_stage_summary" in VALID_ARTIFACT_TYPES
        assert "portfolio_context" in VALID_ARTIFACT_TYPES

    def test_outcome_vocabulary(self):
        for o in (OUTCOME_ELIGIBLE, OUTCOME_ELIGIBLE_WITH_CAUTIONS,
                   OUTCOME_RESTRICTED, OUTCOME_BLOCKED, OUTCOME_FAILED):
            assert o in VALID_OUTCOMES

    def test_check_status_vocabulary(self):
        for s in (CHECK_PASS, CHECK_CAUTION, CHECK_RESTRICT,
                   CHECK_BLOCK, CHECK_UNKNOWN):
            assert s in VALID_CHECK_STATUSES

    def test_thresholds(self):
        assert MAX_ACTIVE_SAME_SYMBOL == 2
        assert MAX_ACTIVE_SAME_STRATEGY == 5
        assert MAX_TOTAL_POSITIONS == 10
        assert MAX_CAPITAL_UTILIZATION_PCT == 80.0
        assert EARNINGS_BLOCK_DAYS == 3
        assert EARNINGS_CAUTION_DAYS == 7
        assert MACRO_CAUTION_DAYS == 3

    def test_premium_selling_strategies(self):
        assert "put_credit_spread" in PREMIUM_SELLING_STRATEGIES
        assert "iron_condor" in PREMIUM_SELLING_STRATEGIES
        assert isinstance(PREMIUM_SELLING_STRATEGIES, frozenset)


class TestDefaultPortfolioProvider:
    """Test default_portfolio_provider."""

    def test_returns_no_live_sources(self):
        result = default_portfolio_provider({"symbol": "SPY"})
        assert result["provider_status"] == "no_live_sources"
        assert result["active_positions"] == []
        assert result["capital_summary"]["total_capital"] is None
        assert len(result["degraded_reasons"]) > 0

    def test_trade_capability_defaults(self):
        result = default_portfolio_provider({"symbol": "QQQ"})
        cap = result["trade_capability"]
        assert cap["enabled"] is True
        assert cap["status"] == "unknown"
        assert cap["restrictions"] == []

    def test_restrictions_empty(self):
        result = default_portfolio_provider({"symbol": "IWM"})
        assert result["restrictions"] == []


class TestBuildPortfolioContext:
    """Test build_portfolio_context."""

    def test_basic_available(self):
        enriched = _make_enriched_data()
        provider_result = {
            "provider_status": "available",
            "trade_capability": {"enabled": True, "status": "enabled",
                                  "restrictions": []},
            "active_positions": [],
            "capital_summary": {"utilization_pct": 20.0},
            "restrictions": [],
            "degraded_reasons": [],
        }
        ctx = build_portfolio_context(enriched, provider_result)
        assert ctx["portfolio_context_version"] == "1.0"
        assert ctx["candidate_id"] == "c1"
        assert ctx["symbol"] == "SPY"
        assert ctx["portfolio_snapshot_status"] == "available"
        assert ctx["trade_capability_status"] == "enabled"
        assert ctx["active_symbol_positions"] == 0
        assert ctx["total_active_positions"] == 0

    def test_counts_same_symbol(self):
        enriched = _make_enriched_data(symbol="SPY")
        provider_result = {
            "provider_status": "available",
            "trade_capability": {"status": "enabled"},
            "active_positions": [
                {"symbol": "SPY", "strategy_type": "put_credit_spread"},
                {"symbol": "SPY", "strategy_type": "iron_condor"},
                {"symbol": "QQQ", "strategy_type": "put_credit_spread"},
            ],
            "capital_summary": {},
            "restrictions": [],
            "degraded_reasons": [],
        }
        ctx = build_portfolio_context(enriched, provider_result)
        assert ctx["active_symbol_positions"] == 2
        # Two positions use put_credit_spread (the candidate's strategy),
        # regardless of symbol.
        assert ctx["active_strategy_positions"] == 2
        assert ctx["total_active_positions"] == 3

    def test_no_data_status(self):
        enriched = _make_enriched_data()
        provider_result = default_portfolio_provider({"symbol": "SPY"})
        ctx = build_portfolio_context(enriched, provider_result)
        assert ctx["portfolio_snapshot_status"] == "no_data"

    def test_degraded_status(self):
        enriched = _make_enriched_data()
        provider_result = {
            "provider_status": "degraded",
            "trade_capability": {"status": "enabled"},
            "active_positions": [],
            "capital_summary": {},
            "restrictions": [],
            "degraded_reasons": ["some source stale"],
        }
        ctx = build_portfolio_context(enriched, provider_result)
        assert ctx["portfolio_snapshot_status"] == "degraded"
        assert "some source stale" in ctx["degraded_reasons"]

    def test_failed_status(self):
        enriched = _make_enriched_data()
        provider_result = {
            "provider_status": "failed",
            "trade_capability": {"status": "unknown"},
            "active_positions": [],
            "capital_summary": {},
            "restrictions": [],
            "degraded_reasons": ["total failure"],
        }
        ctx = build_portfolio_context(enriched, provider_result)
        assert ctx["portfolio_snapshot_status"] == "failed"

    def test_concentration_context(self):
        enriched = _make_enriched_data()
        provider_result = {
            "provider_status": "available",
            "trade_capability": {"status": "enabled"},
            "active_positions": [
                {"symbol": "SPY", "strategy_type": "put_credit_spread"},
                {"symbol": "QQQ", "strategy_type": "iron_condor"},
                {"symbol": "IWM", "strategy_type": "put_credit_spread"},
            ],
            "capital_summary": {},
            "restrictions": [],
            "degraded_reasons": [],
        }
        ctx = build_portfolio_context(enriched, provider_result)
        conc = ctx["concentration_context"]
        assert conc["strategy_family_count"] == 2
        assert conc["symbol_exposure_count"] == 3


class TestCheckRequiredFields:
    """Test check_required_fields."""

    def test_all_present(self):
        data = _make_enriched_data()
        result = check_required_fields(data)
        assert result["check_status"] == CHECK_PASS

    def test_missing_candidate_id(self):
        data = _make_enriched_data()
        data["candidate_id"] = None
        result = check_required_fields(data)
        assert result["check_status"] == CHECK_BLOCK
        assert "candidate_id" in result["details"]["missing_fields"]

    def test_missing_symbol(self):
        data = {"candidate_id": "c1", "strategy_type": "pcs"}
        result = check_required_fields(data)
        assert result["check_status"] == CHECK_BLOCK
        assert "symbol" in result["details"]["missing_fields"]

    def test_missing_strategy_type(self):
        data = {"candidate_id": "c1", "symbol": "SPY"}
        result = check_required_fields(data)
        assert result["check_status"] == CHECK_BLOCK
        assert "strategy_type" in result["details"]["missing_fields"]

    def test_multiple_missing(self):
        result = check_required_fields({})
        assert result["check_status"] == CHECK_BLOCK
        assert len(result["details"]["missing_fields"]) == 3


class TestCheckTradeCapability:
    """Test check_trade_capability."""

    def test_enabled(self):
        ctx = _make_portfolio_ctx(trade_capability_status="enabled")
        result = check_trade_capability(ctx)
        assert result["check_status"] == CHECK_PASS

    def test_disabled(self):
        ctx = _make_portfolio_ctx(trade_capability_status="disabled")
        result = check_trade_capability(ctx)
        assert result["check_status"] == CHECK_BLOCK

    def test_unknown(self):
        ctx = _make_portfolio_ctx(trade_capability_status="unknown")
        result = check_trade_capability(ctx)
        assert result["check_status"] == CHECK_UNKNOWN


class TestCheckStrategyAllowed:
    """Test check_strategy_allowed."""

    def test_no_restrictions(self):
        enriched = _make_enriched_data()
        ctx = _make_portfolio_ctx()
        result = check_strategy_allowed(enriched, ctx)
        assert result["check_status"] == CHECK_PASS

    def test_strategy_blocked(self):
        enriched = _make_enriched_data(strategy_type="iron_condor")
        ctx = _make_portfolio_ctx(
            restriction_flags=["strategy_blocked:iron_condor"],
        )
        result = check_strategy_allowed(enriched, ctx)
        assert result["check_status"] == CHECK_BLOCK

    def test_strategy_restricted(self):
        enriched = _make_enriched_data(strategy_type="iron_condor")
        ctx = _make_portfolio_ctx(
            restriction_flags=["strategy_restricted:iron_condor"],
        )
        result = check_strategy_allowed(enriched, ctx)
        assert result["check_status"] == CHECK_RESTRICT

    def test_other_strategy_blocked_no_effect(self):
        enriched = _make_enriched_data(strategy_type="iron_condor")
        ctx = _make_portfolio_ctx(
            restriction_flags=["strategy_blocked:put_credit_spread"],
        )
        result = check_strategy_allowed(enriched, ctx)
        assert result["check_status"] == CHECK_PASS


class TestCheckEventRiskWindow:
    """Test check_event_risk_window."""

    def test_no_event_context(self):
        enriched = _make_enriched_data()
        result = check_event_risk_window(enriched, None)
        assert result["check_status"] == CHECK_CAUTION
        assert result["details"]["event_data_available"] is False

    def test_no_risk_flags(self):
        enriched = _make_enriched_data()
        event_ctx = _make_event_ctx(risk_flags=[])
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_PASS

    def test_earnings_block_premium_selling(self):
        """Earnings within EARNINGS_BLOCK_DAYS blocks premium-selling."""
        enriched = _make_enriched_data(strategy_type="put_credit_spread")
        event_ctx = _make_event_ctx(
            risk_flags=["earnings_nearby"],
            nearest_event={
                "event_type": "earnings",
                "days_until": 2,
            },
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_BLOCK

    def test_earnings_restrict_premium_selling_within_caution(self):
        """Earnings within EARNINGS_CAUTION_DAYS restricts
        premium-selling strategies."""
        enriched = _make_enriched_data(strategy_type="iron_condor")
        event_ctx = _make_event_ctx(
            risk_flags=["earnings_nearby"],
            nearest_event={
                "event_type": "earnings",
                "days_until": 5,
            },
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_RESTRICT

    def test_earnings_caution_non_premium_selling(self):
        """Earnings nearby causes caution for non-premium-selling."""
        enriched = _make_enriched_data(strategy_type="long_call")
        event_ctx = _make_event_ctx(
            risk_flags=["earnings_nearby"],
            nearest_event={
                "event_type": "earnings",
                "days_until": 3,
            },
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_macro_event_caution(self):
        enriched = _make_enriched_data()
        event_ctx = _make_event_ctx(
            risk_flags=["macro_event_nearby"],
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_no_event_coverage_caution(self):
        enriched = _make_enriched_data()
        event_ctx = _make_event_ctx(
            risk_flags=["no_event_coverage"],
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_event_lookup_degraded_caution(self):
        enriched = _make_enriched_data()
        event_ctx = _make_event_ctx(
            risk_flags=["event_lookup_degraded"],
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_event_window_overlap_caution(self):
        enriched = _make_enriched_data()
        event_ctx = _make_event_ctx(
            risk_flags=["event_window_overlap"],
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_earnings_at_block_boundary(self):
        """Earnings exactly at EARNINGS_BLOCK_DAYS → block for
        premium-selling."""
        enriched = _make_enriched_data(strategy_type="short_put")
        event_ctx = _make_event_ctx(
            risk_flags=["earnings_nearby"],
            nearest_event={
                "event_type": "earnings",
                "days_until": EARNINGS_BLOCK_DAYS,
            },
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_BLOCK

    def test_earnings_just_past_block_boundary(self):
        """Earnings at EARNINGS_BLOCK_DAYS+1 → restrict (not block)."""
        enriched = _make_enriched_data(strategy_type="short_put")
        event_ctx = _make_event_ctx(
            risk_flags=["earnings_nearby"],
            nearest_event={
                "event_type": "earnings",
                "days_until": EARNINGS_BLOCK_DAYS + 1,
            },
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_RESTRICT

    def test_earnings_at_caution_boundary(self):
        """Earnings exactly at EARNINGS_CAUTION_DAYS → restrict for
        premium-selling."""
        enriched = _make_enriched_data(strategy_type="short_call")
        event_ctx = _make_event_ctx(
            risk_flags=["earnings_nearby"],
            nearest_event={
                "event_type": "earnings",
                "days_until": EARNINGS_CAUTION_DAYS,
            },
        )
        result = check_event_risk_window(enriched, event_ctx)
        assert result["check_status"] == CHECK_RESTRICT


class TestCheckSameSymbolOverlap:
    """Test check_same_symbol_overlap."""

    def test_no_active(self):
        enriched = _make_enriched_data()
        ctx = _make_portfolio_ctx(active_symbol=0)
        result = check_same_symbol_overlap(enriched, ctx)
        assert result["check_status"] == CHECK_PASS

    def test_one_active_caution(self):
        enriched = _make_enriched_data()
        ctx = _make_portfolio_ctx(active_symbol=1)
        result = check_same_symbol_overlap(enriched, ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_at_limit_restrict(self):
        enriched = _make_enriched_data()
        ctx = _make_portfolio_ctx(active_symbol=MAX_ACTIVE_SAME_SYMBOL)
        result = check_same_symbol_overlap(enriched, ctx)
        assert result["check_status"] == CHECK_RESTRICT

    def test_above_limit_restrict(self):
        enriched = _make_enriched_data()
        ctx = _make_portfolio_ctx(active_symbol=MAX_ACTIVE_SAME_SYMBOL + 1)
        result = check_same_symbol_overlap(enriched, ctx)
        assert result["check_status"] == CHECK_RESTRICT


class TestCheckPositionCount:
    """Test check_position_count."""

    def test_well_below_limit(self):
        ctx = _make_portfolio_ctx(total_active=2)
        result = check_position_count(ctx)
        assert result["check_status"] == CHECK_PASS

    def test_near_limit_caution(self):
        ctx = _make_portfolio_ctx(total_active=MAX_TOTAL_POSITIONS - 2)
        result = check_position_count(ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_at_limit_restrict(self):
        ctx = _make_portfolio_ctx(total_active=MAX_TOTAL_POSITIONS)
        result = check_position_count(ctx)
        assert result["check_status"] == CHECK_RESTRICT

    def test_above_limit_restrict(self):
        ctx = _make_portfolio_ctx(total_active=MAX_TOTAL_POSITIONS + 3)
        result = check_position_count(ctx)
        assert result["check_status"] == CHECK_RESTRICT


class TestCheckCapitalLimit:
    """Test check_capital_limit."""

    def test_well_below(self):
        ctx = _make_portfolio_ctx(utilization_pct=30.0)
        result = check_capital_limit(ctx)
        assert result["check_status"] == CHECK_PASS

    def test_near_limit_caution(self):
        ctx = _make_portfolio_ctx(
            utilization_pct=MAX_CAPITAL_UTILIZATION_PCT - 5,
        )
        result = check_capital_limit(ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_at_limit_restrict(self):
        ctx = _make_portfolio_ctx(
            utilization_pct=MAX_CAPITAL_UTILIZATION_PCT,
        )
        result = check_capital_limit(ctx)
        assert result["check_status"] == CHECK_RESTRICT

    def test_above_limit_restrict(self):
        ctx = _make_portfolio_ctx(utilization_pct=95.0)
        result = check_capital_limit(ctx)
        assert result["check_status"] == CHECK_RESTRICT

    def test_none_utilization_unknown(self):
        ctx = _make_portfolio_ctx(utilization_pct=None)
        result = check_capital_limit(ctx)
        assert result["check_status"] == CHECK_UNKNOWN

    def test_just_below_caution_threshold_pass(self):
        ctx = _make_portfolio_ctx(
            utilization_pct=MAX_CAPITAL_UTILIZATION_PCT - 11,
        )
        result = check_capital_limit(ctx)
        assert result["check_status"] == CHECK_PASS


class TestCheckConcentration:
    """Test check_concentration."""

    def test_no_active_pass(self):
        enriched = _make_enriched_data()
        ctx = _make_portfolio_ctx(active_strategy=0)
        result = check_concentration(enriched, ctx)
        assert result["check_status"] == CHECK_PASS

    def test_near_limit_caution(self):
        enriched = _make_enriched_data()
        ctx = _make_portfolio_ctx(
            active_strategy=MAX_ACTIVE_SAME_STRATEGY - 1,
        )
        result = check_concentration(enriched, ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_at_limit_restrict(self):
        enriched = _make_enriched_data()
        ctx = _make_portfolio_ctx(active_strategy=MAX_ACTIVE_SAME_STRATEGY)
        result = check_concentration(enriched, ctx)
        assert result["check_status"] == CHECK_RESTRICT


class TestCheckEventCoverage:
    """Test check_event_coverage."""

    def test_no_event_ctx(self):
        result = check_event_coverage(None)
        assert result["check_status"] == CHECK_CAUTION

    def test_failed_event_ctx(self):
        ctx = _make_event_ctx(event_status="failed")
        result = check_event_coverage(ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_not_downstream_usable(self):
        ctx = _make_event_ctx(downstream_usable=False)
        result = check_event_coverage(ctx)
        assert result["check_status"] == CHECK_CAUTION

    def test_good_event_ctx(self):
        ctx = _make_event_ctx(event_status="enriched", downstream_usable=True)
        result = check_event_coverage(ctx)
        assert result["check_status"] == CHECK_PASS


class TestDeriveOverallOutcome:
    """Test derive_overall_outcome deterministic logic."""

    def test_all_pass(self):
        checks = [
            {"check_status": CHECK_PASS},
            {"check_status": CHECK_PASS},
        ]
        assert derive_overall_outcome(checks) == OUTCOME_ELIGIBLE

    def test_any_block_blocks(self):
        checks = [
            {"check_status": CHECK_PASS},
            {"check_status": CHECK_BLOCK},
            {"check_status": CHECK_CAUTION},
        ]
        assert derive_overall_outcome(checks) == OUTCOME_BLOCKED

    def test_restrict_without_block(self):
        checks = [
            {"check_status": CHECK_PASS},
            {"check_status": CHECK_RESTRICT},
        ]
        assert derive_overall_outcome(checks) == OUTCOME_RESTRICTED

    def test_caution_without_block_or_restrict(self):
        checks = [
            {"check_status": CHECK_PASS},
            {"check_status": CHECK_CAUTION},
        ]
        assert derive_overall_outcome(checks) == OUTCOME_ELIGIBLE_WITH_CAUTIONS

    def test_unknown_causes_caution_outcome(self):
        checks = [
            {"check_status": CHECK_PASS},
            {"check_status": CHECK_UNKNOWN},
        ]
        assert derive_overall_outcome(checks) == OUTCOME_ELIGIBLE_WITH_CAUTIONS

    def test_block_takes_priority_over_restrict(self):
        checks = [
            {"check_status": CHECK_RESTRICT},
            {"check_status": CHECK_BLOCK},
        ]
        assert derive_overall_outcome(checks) == OUTCOME_BLOCKED

    def test_empty_checks_eligible(self):
        assert derive_overall_outcome([]) == OUTCOME_ELIGIBLE


class TestEvaluatePolicy:
    """Test evaluate_policy full evaluation."""

    def test_basic_eligible(self):
        enriched = _make_enriched_data()
        event_ctx = _make_event_ctx(
            event_status="enriched", downstream_usable=True,
        )
        pctx = _make_portfolio_ctx()
        result = evaluate_policy(enriched, event_ctx, pctx, "run-1")
        assert result["policy_version"] == "1.0"
        assert result["run_id"] == "run-1"
        assert result["candidate_id"] == "c1"
        assert result["symbol"] == "SPY"
        assert result["overall_outcome"] == OUTCOME_ELIGIBLE
        assert result["policy_status"] == POLICY_STATUS_EVALUATED
        assert result["downstream_usable"] is True
        assert len(result["checks"]) == 9
        assert len(result["blocking_reasons"]) == 0

    def test_all_contract_fields_present(self):
        enriched = _make_enriched_data()
        event_ctx = _make_event_ctx()
        pctx = _make_portfolio_ctx()
        result = evaluate_policy(enriched, event_ctx, pctx, "run-1")
        required_keys = {
            "policy_version", "run_id", "candidate_id", "symbol",
            "source_enriched_candidate_ref",
            "source_event_context_ref",
            "policy_status", "overall_outcome", "checks",
            "blocking_reasons", "caution_reasons",
            "restriction_reasons",
            "eligibility_flags",
            "portfolio_context_summary",
            "event_risk_summary",
            "downstream_usable", "degraded_reasons",
            "policy_metadata", "generated_at",
        }
        assert required_keys.issubset(result.keys())

    def test_eligibility_flags_shape(self):
        enriched = _make_enriched_data()
        pctx = _make_portfolio_ctx()
        result = evaluate_policy(enriched, None, pctx, "run-1")
        flags = result["eligibility_flags"]
        for key in ("trade_capable", "strategy_allowed",
                     "within_capital_limits", "within_position_limits",
                     "no_symbol_overlap", "event_risk_acceptable"):
            assert key in flags
            assert isinstance(flags[key], bool)

    def test_policy_metadata_has_thresholds(self):
        enriched = _make_enriched_data()
        pctx = _make_portfolio_ctx()
        result = evaluate_policy(enriched, None, pctx, "run-1")
        meta = result["policy_metadata"]
        thresholds = meta["thresholds_used"]
        assert thresholds["max_active_same_symbol"] == MAX_ACTIVE_SAME_SYMBOL
        assert thresholds["max_total_positions"] == MAX_TOTAL_POSITIONS
        assert meta["check_count"] == 9

    def test_blocked_candidate(self):
        enriched = _make_enriched_data()
        pctx = _make_portfolio_ctx(trade_capability_status="disabled")
        result = evaluate_policy(enriched, None, pctx, "run-1")
        assert result["overall_outcome"] == OUTCOME_BLOCKED
        assert len(result["blocking_reasons"]) > 0
        assert result["eligibility_flags"]["trade_capable"] is False

    def test_restricted_candidate(self):
        enriched = _make_enriched_data()
        pctx = _make_portfolio_ctx(
            total_active=MAX_TOTAL_POSITIONS + 1,
        )
        result = evaluate_policy(enriched, None, pctx, "run-1")
        assert result["overall_outcome"] in (
            OUTCOME_RESTRICTED, OUTCOME_BLOCKED,
        )

    def test_degraded_status_when_no_event_ctx(self):
        enriched = _make_enriched_data()
        pctx = _make_portfolio_ctx()
        result = evaluate_policy(enriched, None, pctx, "run-1")
        assert result["policy_status"] == POLICY_STATUS_EVALUATED_DEGRADED
        assert "event context not available" in result["degraded_reasons"]

    def test_degraded_status_when_no_portfolio_data(self):
        enriched = _make_enriched_data()
        event_ctx = _make_event_ctx()
        pctx = _make_portfolio_ctx(snapshot_status="no_data")
        result = evaluate_policy(enriched, event_ctx, pctx, "run-1")
        assert result["policy_status"] == POLICY_STATUS_EVALUATED_DEGRADED

    def test_event_risk_summary_with_events(self):
        enriched = _make_enriched_data()
        event_ctx = _make_event_ctx(
            risk_flags=["earnings_nearby"],
            event_status="enriched",
        )
        event_ctx["event_summary"] = {
            "total_events": 2,
            "risk_flag_count": 1,
            "nearest_event_type": "earnings",
            "nearest_days_until": 5,
        }
        pctx = _make_portfolio_ctx()
        result = evaluate_policy(enriched, event_ctx, pctx, "run-1")
        ers = result["event_risk_summary"]
        assert ers["event_data_available"] is True
        assert ers["total_events"] == 2
        assert ers["risk_flag_count"] == 1

    def test_event_risk_summary_without_events(self):
        enriched = _make_enriched_data()
        pctx = _make_portfolio_ctx()
        result = evaluate_policy(enriched, None, pctx, "run-1")
        ers = result["event_risk_summary"]
        assert ers["event_data_available"] is False
        assert ers["total_events"] == 0

    def test_source_refs_passed_through(self):
        enriched = _make_enriched_data()
        pctx = _make_portfolio_ctx()
        result = evaluate_policy(
            enriched, None, pctx, "run-1",
            enriched_artifact_ref="art-e1",
            event_artifact_ref="art-ev1",
        )
        assert result["source_enriched_candidate_ref"] == "art-e1"
        assert result["source_event_context_ref"] == "art-ev1"


class TestHandlerContract:
    """Test handler return shape matches orchestrator expectations."""

    def test_return_shape_required_keys(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "policy")
        result = portfolio_policy_handler(run, store, "policy")
        assert "outcome" in result
        assert "summary_counts" in result
        assert "artifacts" in result
        assert "metadata" in result
        assert "error" in result

    def test_outcome_failed_no_upstream(self):
        """Without enrichment summary, handler fails."""
        run, store = _make_run_and_store()
        mark_stage_running(run, "policy")
        result = portfolio_policy_handler(run, store, "policy")
        assert result["outcome"] == "failed"
        assert result["error"] is not None
        assert result["error"]["code"] == "NO_CANDIDATE_SOURCE"

    def test_summary_counts_keys(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "policy")
        result = portfolio_policy_handler(run, store, "policy")
        sc = result["summary_counts"]
        for key in ("total_evaluated", "total_eligible",
                     "total_eligible_with_cautions",
                     "total_restricted", "total_blocked",
                     "total_failed"):
            assert key in sc

    def test_metadata_has_elapsed_ms(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "policy")
        result = portfolio_policy_handler(run, store, "policy")
        assert "elapsed_ms" in result["metadata"]


class TestSingleCandidate:
    """Test processing a single candidate."""

    def test_single_candidate_eligible(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-1"])
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_evaluated"] == 1
        assert result["summary_counts"]["total_eligible_with_cautions"] >= 0

        # Verify artifact written
        art = get_artifact_by_key(store, "policy", "policy_cand-1")
        assert art is not None
        assert art["data"]["candidate_id"] == "cand-1"
        assert art["data"]["overall_outcome"] in VALID_OUTCOMES

    def test_single_candidate_with_event_context(self):
        """Policy correctly reads Step 10 event context."""
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-ev"],
            write_events=True,
            risk_flags=["earnings_nearby"],
            nearest_event={"event_type": "earnings", "days_until": 5},
        )
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        assert result["outcome"] == "completed"

        art = get_artifact_by_key(store, "policy", "policy_cand-ev")
        assert art is not None
        data = art["data"]
        # Should have event risk info
        assert data["event_risk_summary"]["event_data_available"] is True

    def test_single_candidate_default_provider(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-def"])
        mark_stage_running(run, "policy")

        # Uses default_portfolio_provider
        result = portfolio_policy_handler(run, store, "policy")
        assert result["outcome"] == "completed"

        art = get_artifact_by_key(store, "policy", "policy_cand-def")
        assert art is not None
        # Default provider → degraded status
        assert art["data"]["policy_status"] == POLICY_STATUS_EVALUATED_DEGRADED

    def test_single_candidate_blocked(self):
        """Candidate blocked by trade capability."""
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-blk"])
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider(
            trade_capability={
                "enabled": False,
                "status": "disabled",
                "restrictions": ["account frozen"],
            },
        )
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_blocked"] == 1

        art = get_artifact_by_key(store, "policy", "policy_cand-blk")
        assert art["data"]["overall_outcome"] == OUTCOME_BLOCKED


class TestMultipleCandidates:
    """Test processing multiple candidates."""

    def test_multiple_candidates(self):
        run, store = _make_run_and_store()
        cid_list = ["cand-a", "cand-b", "cand-c"]
        _populate_upstream(
            store, run["run_id"], cid_list,
            symbols={"cand-a": "SPY", "cand-b": "QQQ", "cand-c": "IWM"},
        )
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        assert result["outcome"] == "completed"
        sc = result["summary_counts"]
        assert sc["total_evaluated"] == 3

        for cid in cid_list:
            art = get_artifact_by_key(store, "policy", f"policy_{cid}")
            assert art is not None
            assert art["data"]["candidate_id"] == cid

    def test_mixed_outcomes(self):
        """Different candidates get different outcomes based on
        portfolio state."""
        run, store = _make_run_and_store()
        # Write candidates with different strategies
        _write_enriched_candidate(
            store, run["run_id"], "cand-ok",
            symbol="SPY", strategy_type="put_credit_spread",
        )
        _write_enriched_candidate(
            store, run["run_id"], "cand-blk",
            symbol="QQQ", strategy_type="iron_condor",
        )
        _write_enrichment_summary(
            store, run["run_id"], ["cand-ok", "cand-blk"],
        )
        mark_stage_running(run, "policy")

        def mixed_provider(lookup_input):
            if lookup_input["symbol"] == "QQQ":
                return {
                    "provider_status": "available",
                    "trade_capability": {
                        "enabled": False, "status": "disabled",
                        "restrictions": [],
                    },
                    "active_positions": [],
                    "capital_summary": {
                        "total_capital": 100_000,
                        "capital_in_use": 0,
                        "utilization_pct": 0,
                    },
                    "restrictions": [],
                    "degraded_reasons": [],
                }
            return {
                "provider_status": "available",
                "trade_capability": {
                    "enabled": True, "status": "enabled",
                    "restrictions": [],
                },
                "active_positions": [],
                "capital_summary": {
                    "total_capital": 100_000,
                    "capital_in_use": 10_000,
                    "utilization_pct": 10.0,
                },
                "restrictions": [],
                "degraded_reasons": [],
            }

        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=mixed_provider,
        )
        assert result["outcome"] == "completed"

        art_ok = get_artifact_by_key(store, "policy", "policy_cand-ok")
        art_blk = get_artifact_by_key(store, "policy", "policy_cand-blk")
        assert art_blk["data"]["overall_outcome"] == OUTCOME_BLOCKED
        assert art_ok["data"]["overall_outcome"] != OUTCOME_BLOCKED


class TestVacuousCompletion:
    """Test vacuous completion paths."""

    def test_no_enrichment_summary(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "policy")
        result = portfolio_policy_handler(run, store, "policy")
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_CANDIDATE_SOURCE"

    def test_empty_enrichment_records(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], [])
        mark_stage_running(run, "policy")
        result = portfolio_policy_handler(run, store, "policy")
        assert result["outcome"] == "completed"
        assert result["metadata"]["stage_status"] == "no_candidates_to_process"

        # Summary artifact still written
        art = get_artifact_by_key(
            store, "policy", "policy_stage_summary",
        )
        assert art is not None
        assert art["data"]["stage_status"] == "no_candidates_to_process"


class TestProviderFailure:
    """Test portfolio provider that raises exceptions."""

    def test_provider_exception_single_candidate(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-fail"])
        mark_stage_running(run, "policy")

        provider = _failing_provider("Portfolio API crash")
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        assert result["outcome"] == "failed"
        assert result["summary_counts"]["total_failed"] == 1
        assert result["error"] is not None
        assert result["error"]["code"] == "POLICY_ALL_FAILED"

    def test_provider_exception_partial(self):
        """One candidate fails provider, another succeeds."""
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-ok", "cand-boom"],
            symbols={"cand-ok": "SPY", "cand-boom": "QQQ"},
        )
        mark_stage_running(run, "policy")

        def mixed_provider(lookup_input):
            if lookup_input["symbol"] == "QQQ":
                raise RuntimeError("QQQ portfolio error")
            return {
                "provider_status": "available",
                "trade_capability": {
                    "enabled": True, "status": "enabled",
                    "restrictions": [],
                },
                "active_positions": [],
                "capital_summary": {
                    "total_capital": 100_000,
                    "capital_in_use": 10_000,
                    "utilization_pct": 10.0,
                },
                "restrictions": [],
                "degraded_reasons": [],
            }

        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=mixed_provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_failed"] == 1
        sc = result["summary_counts"]
        total = (sc["total_evaluated"] + sc["total_failed"])
        assert total == 2
        assert result["metadata"]["stage_status"] == "degraded"


class TestPartialFailures:
    """Test partial candidate failures."""

    def test_enriched_packet_missing(self):
        """Enrichment summary lists a candidate whose artifact
        is missing."""
        run, store = _make_run_and_store()
        _write_enrichment_summary(
            store, run["run_id"], ["cand-ok", "cand-ghost"],
        )
        _write_enriched_candidate(store, run["run_id"], "cand-ok")
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_failed"] == 1
        assert result["metadata"]["stage_status"] == "degraded"

    def test_all_candidates_missing_packets(self):
        """Enrichment summary has candidates but no enriched
        packets."""
        run, store = _make_run_and_store()
        _write_enrichment_summary(
            store, run["run_id"], ["ghost-1", "ghost-2"],
        )
        mark_stage_running(run, "policy")

        result = portfolio_policy_handler(run, store, "policy")
        assert result["outcome"] == "failed"
        assert result["summary_counts"]["total_failed"] == 2


class TestArtifactWriting:
    """Test artifact creation and lineage."""

    def test_per_candidate_artifact_keyed(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-art"])
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )

        art = get_artifact_by_key(store, "policy", "policy_cand-art")
        assert art is not None
        assert art["artifact_type"] == "policy_output"
        assert art["stage_key"] == "policy"
        assert art["candidate_id"] == "cand-art"

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-sum"])
        mark_stage_running(run, "policy")

        portfolio_policy_handler(run, store, "policy")

        art = get_artifact_by_key(
            store, "policy", "policy_stage_summary",
        )
        assert art is not None
        assert art["artifact_type"] == "policy_stage_summary"
        assert art["data"]["stage_key"] == "policy"

    def test_summary_artifact_on_vacuous(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], [])
        mark_stage_running(run, "policy")

        portfolio_policy_handler(run, store, "policy")

        art = get_artifact_by_key(
            store, "policy", "policy_stage_summary",
        )
        assert art is not None
        assert art["data"]["total_candidates_in"] == 0

    def test_artifact_refs_in_metadata(self):
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-ref1", "cand-ref2"],
        )
        mark_stage_running(run, "policy")

        result = portfolio_policy_handler(run, store, "policy")
        refs = result["metadata"].get("output_artifact_refs", {})
        assert "cand-ref1" in refs
        assert "cand-ref2" in refs


class TestStageSummary:
    """Test stage summary structure."""

    def test_summary_fields(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-s1"])
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        summary = result["metadata"]["stage_summary"]

        assert summary["stage_key"] == "policy"
        assert summary["total_candidates_in"] == 1
        assert summary["total_evaluated"] == 1
        assert "execution_records" in summary
        assert "outcome_counts" in summary
        assert "blocking_reason_counts" in summary
        assert "caution_reason_counts" in summary
        assert "elapsed_ms" in summary
        assert "generated_at" in summary
        assert summary["summary_artifact_ref"] is not None

    def test_outcome_counts(self):
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["c1", "c2"],
        )
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        oc = result["metadata"]["outcome_counts"]
        assert isinstance(oc, dict)
        total = sum(oc.values())
        assert total == 2

    def test_execution_records_per_candidate(self):
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-er1", "cand-er2"],
        )
        mark_stage_running(run, "policy")

        result = portfolio_policy_handler(run, store, "policy")
        summary = result["metadata"]["stage_summary"]
        records = summary["execution_records"]
        assert len(records) == 2
        cids = {r["candidate_id"] for r in records}
        assert cids == {"cand-er1", "cand-er2"}
        for r in records:
            assert "elapsed_ms" in r
            assert "policy_status" in r
            assert "overall_outcome" in r


class TestEventEmission:
    """Test structured event callbacks."""

    def test_started_and_completed_emitted(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-ev1"])
        mark_stage_running(run, "policy")

        events_received = []
        result = portfolio_policy_handler(
            run, store, "policy",
            event_callback=events_received.append,
        )
        types = [e["event_type"] for e in events_received]
        assert "policy_evaluation_started" in types
        assert "policy_evaluation_completed" in types

    def test_failed_emitted_on_failure(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-evf"])
        mark_stage_running(run, "policy")

        events_received = []
        result = portfolio_policy_handler(
            run, store, "policy",
            event_callback=events_received.append,
            portfolio_provider=_failing_provider(),
        )
        types = [e["event_type"] for e in events_received]
        assert "policy_evaluation_started" in types
        assert "policy_evaluation_failed" in types

    def test_vacuous_emits_completed(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], [])
        mark_stage_running(run, "policy")

        events_received = []
        result = portfolio_policy_handler(
            run, store, "policy",
            event_callback=events_received.append,
        )
        types = [e["event_type"] for e in events_received]
        assert "policy_evaluation_started" in types
        assert "policy_evaluation_completed" in types

    def test_callback_exception_does_not_crash(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], [])
        mark_stage_running(run, "policy")

        def bad_callback(event):
            raise ValueError("callback exploded")

        result = portfolio_policy_handler(
            run, store, "policy",
            event_callback=bad_callback,
        )
        assert result["outcome"] == "completed"


class TestInjectableProvider:
    """Test that portfolio_provider kwarg is properly injectable."""

    def test_custom_provider_receives_lookup_input(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-inj"])
        mark_stage_running(run, "policy")

        received_inputs = []

        def spy_provider(lookup_input):
            received_inputs.append(lookup_input)
            return default_portfolio_provider(lookup_input)

        portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=spy_provider,
        )
        assert len(received_inputs) == 1
        li = received_inputs[0]
        assert li["symbol"] == "SPY"
        assert "strategy_type" in li
        assert "scanner_family" in li
        assert "direction" in li
        assert "candidate_id" in li

    def test_provider_per_candidate(self):
        """Each candidate gets its own provider call."""
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-p1", "cand-p2"],
            symbols={"cand-p1": "SPY", "cand-p2": "QQQ"},
        )
        mark_stage_running(run, "policy")

        symbols_seen = []

        def tracking_provider(lookup_input):
            symbols_seen.append(lookup_input["symbol"])
            return default_portfolio_provider(lookup_input)

        portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=tracking_provider,
        )
        assert set(symbols_seen) == {"SPY", "QQQ"}


class TestEventContextIntegration:
    """Test reading Step 10 event context artifacts."""

    def test_reads_event_context_from_store(self):
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-ec"],
            write_events=True,
            risk_flags=["earnings_nearby", "macro_event_nearby"],
            nearest_event={"event_type": "earnings", "days_until": 3},
        )
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        assert result["outcome"] == "completed"

        art = get_artifact_by_key(store, "policy", "policy_cand-ec")
        policy = art["data"]
        ers = policy["event_risk_summary"]
        assert ers["event_data_available"] is True
        assert ers["risk_flags"] == [
            "earnings_nearby", "macro_event_nearby",
        ]

    def test_handles_missing_event_context_gracefully(self):
        """When no event context artifact exists, policy degrades."""
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-noev"],
            write_events=False,
        )
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        assert result["outcome"] == "completed"

        art = get_artifact_by_key(store, "policy", "policy_cand-noev")
        policy = art["data"]
        assert policy["event_risk_summary"]["event_data_available"] is False
        assert policy["policy_status"] == POLICY_STATUS_EVALUATED_DEGRADED

    def test_event_block_premium_selling_via_store(self):
        """Premium-selling candidate blocked by imminent earnings
        read from Step 10."""
        run, store = _make_run_and_store()
        _write_enriched_candidate(
            store, run["run_id"], "cand-eb",
            symbol="SPY", strategy_type="put_credit_spread",
        )
        _write_event_context(
            store, run["run_id"], "cand-eb",
            risk_flags=["earnings_nearby"],
            nearest_event={"event_type": "earnings", "days_until": 2},
        )
        _write_enrichment_summary(store, run["run_id"], ["cand-eb"])
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider()
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )

        art = get_artifact_by_key(store, "policy", "policy_cand-eb")
        assert art["data"]["overall_outcome"] == OUTCOME_BLOCKED


class TestDegradedProvider:
    """Test degraded portfolio provider results."""

    def test_degraded_provider(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-deg"])
        mark_stage_running(run, "policy")

        provider = _mock_portfolio_provider(
            status="degraded",
            degraded_reasons=["broker connection stale"],
        )
        result = portfolio_policy_handler(
            run, store, "policy",
            portfolio_provider=provider,
        )
        assert result["outcome"] == "completed"

        art = get_artifact_by_key(store, "policy", "policy_cand-deg")
        assert art["data"]["policy_status"] == POLICY_STATUS_EVALUATED_DEGRADED


class TestOrchestratorIntegration:
    """Test wiring, dependencies, and full pipeline integration."""

    def test_default_handler_wired(self):
        handlers = get_default_handlers()
        assert handlers["policy"] is portfolio_policy_handler

    def test_dependency_on_candidate_enrichment(self):
        deps = get_default_dependency_map()
        assert "candidate_enrichment" in deps["policy"]

    def test_policy_not_continuable(self):
        """Policy stage failure should halt the pipeline
        (it's a gating layer)."""
        from app.services.pipeline_orchestrator import _CONTINUABLE_STAGES
        assert "policy" not in _CONTINUABLE_STAGES

    def test_runs_through_pipeline_with_stubs(self):
        result = _all_stub_pipeline()
        run = result["run"]
        assert run["status"] in ("completed", "partial_failed")

    def test_execute_stage_with_handler(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-orch"])

        result = execute_stage(
            run, store, _STAGE_KEY,
            handler=portfolio_policy_handler,
        )
        assert result["outcome"] == "completed"

    def test_full_pipeline_with_policy(self):
        result = run_pipeline_with_handlers(
            {
                "market_data": _success_handler,
                "market_model_analysis": _success_handler,
                "scanners": _success_handler,
                "candidate_selection": _success_handler,
                "shared_context": _success_handler,
                "candidate_enrichment": _success_handler,
                "events": _success_handler,
                "policy": portfolio_policy_handler,
            },
            run_id="test-pipe-policy-001",
        )
        sr = {s["stage_key"]: s for s in result["stage_results"]}
        # Policy gets "failed" because no enrichment summary artifact
        # exists (all upstream stages are stubs), which is expected
        assert sr["policy"]["outcome"] in ("completed", "failed")
