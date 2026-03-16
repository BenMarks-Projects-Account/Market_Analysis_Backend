"""V2-forward scanner-stage integration tests — Prompt 13.

Tests the V2-forward routing model, legacy isolation, execution path
markers, pipeline registry completeness, override mechanism, manual
verification hooks, and routing summary.
"""

from __future__ import annotations

import sys
sys.path.insert(0, ".")

import pytest

from app.services.scanner_v2.migration import (
    clear_scanner_version_override,
    execute_v2_scanner,
    get_migration_status,
    get_routing_report,
    get_scanner_version,
    set_scanner_version,
    should_run_v2,
)
from app.services.scanner_v2.registry import (
    is_v2_supported,
    list_v2_families,
)
from app.services.pipeline_scanner_stage import (
    build_scanner_execution_record,
    build_scanner_stage_summary,
    get_default_scanner_registry,
)
from app.services.scanner_v2.verify import (
    get_family_verification_summary,
    get_v2_routing_report,
    verify_v2_family,
)


# =====================================================================
#  Section 1 — V2-forward routing
# =====================================================================

class TestV2ForwardRouting:
    """All implemented V2 families route V2 by default."""

    ALL_V2_STRATEGY_IDS = [
        "put_credit_spread",
        "call_credit_spread",
        "put_debit",
        "call_debit",
        "iron_condor",
        "butterfly_debit",
        "iron_butterfly",
        "calendar_call_spread",
        "calendar_put_spread",
        "diagonal_call_spread",
        "diagonal_put_spread",
    ]

    @pytest.mark.parametrize("scanner_key", ALL_V2_STRATEGY_IDS)
    def test_all_implemented_route_v2(self, scanner_key: str):
        assert get_scanner_version(scanner_key) == "v2"

    @pytest.mark.parametrize("scanner_key", ALL_V2_STRATEGY_IDS)
    def test_should_run_v2_true(self, scanner_key: str):
        assert should_run_v2(scanner_key) is True

    @pytest.mark.parametrize("scanner_key", ALL_V2_STRATEGY_IDS)
    def test_is_v2_supported(self, scanner_key: str):
        assert is_v2_supported(scanner_key) is True

    def test_unknown_key_routes_v1(self):
        assert get_scanner_version("totally_fake_scanner") == "v1"
        assert should_run_v2("totally_fake_scanner") is False

    def test_four_families_implemented(self):
        families = list_v2_families()
        implemented = [f for f in families if f["implemented"]]
        assert len(implemented) == 4
        family_keys = {f["family_key"] for f in implemented}
        assert family_keys == {
            "vertical_spreads", "iron_condors", "butterflies", "calendars",
        }


# =====================================================================
#  Section 2 — Override mechanism (emergency rollback)
# =====================================================================

class TestOverrideMechanism:
    """Override mechanism allows emergency rollback to v1."""

    def teardown_method(self):
        """Clean up overrides after each test."""
        clear_scanner_version_override("iron_condor")
        clear_scanner_version_override("put_credit_spread")

    def test_set_override_to_v1(self):
        set_scanner_version("iron_condor", "v1")
        assert get_scanner_version("iron_condor") == "v1"
        assert should_run_v2("iron_condor") is False

    def test_clear_override_restores_v2(self):
        set_scanner_version("iron_condor", "v1")
        assert get_scanner_version("iron_condor") == "v1"
        clear_scanner_version_override("iron_condor")
        assert get_scanner_version("iron_condor") == "v2"

    def test_override_to_v2_explicit(self):
        set_scanner_version("put_credit_spread", "v2")
        assert get_scanner_version("put_credit_spread") == "v2"

    def test_override_v2_for_unimplemented_falls_back(self):
        """If override says v2 but no V2 implementation, fall back to v1."""
        set_scanner_version("totally_fake_scanner", "v2")
        assert get_scanner_version("totally_fake_scanner") == "v1"
        clear_scanner_version_override("totally_fake_scanner")


# =====================================================================
#  Section 3 — Pipeline registry completeness
# =====================================================================

class TestPipelineRegistryCompleteness:
    """Pipeline registry has all V2 strategy IDs."""

    def test_options_scanner_count(self):
        reg = get_default_scanner_registry()
        options = [k for k, v in reg.items() if v["scanner_family"] == "options"]
        assert len(options) == 11

    def test_stock_scanner_count(self):
        reg = get_default_scanner_registry()
        stock = [k for k, v in reg.items() if v["scanner_family"] == "stock"]
        assert len(stock) == 4

    def test_total_scanners(self):
        reg = get_default_scanner_registry()
        assert len(reg) == 15

    def test_all_v2_strategy_ids_in_registry(self):
        """Every V2 strategy_id must have a pipeline registry entry."""
        reg = get_default_scanner_registry()
        families = list_v2_families()
        for fm in families:
            for sid in fm["strategy_ids"]:
                assert sid in reg, (
                    f"V2 strategy_id '{sid}' (family={fm['family_key']}) "
                    f"missing from pipeline registry"
                )

    def test_all_entries_have_required_fields(self):
        reg = get_default_scanner_registry()
        required = {"scanner_key", "display_name", "scanner_family",
                     "strategy_type", "enabled", "required"}
        for key, entry in reg.items():
            assert required.issubset(entry.keys()), (
                f"Entry '{key}' missing: {required - set(entry.keys())}"
            )


# =====================================================================
#  Section 4 — Execution path markers
# =====================================================================

class TestExecutionPathMarkers:
    """Execution path markers in records and summaries."""

    def test_execution_record_supports_execution_path(self):
        """build_scanner_execution_record returns a dict that can hold
        execution_path (set post-creation by _run_single_scanner)."""
        rec = build_scanner_execution_record(
            scanner_key="iron_condor",
            scanner_family="options",
            strategy_type="iron_condor",
            status="completed",
            candidate_count=3,
        )
        rec["execution_path"] = "v2"
        assert rec["execution_path"] == "v2"

    def test_stage_summary_includes_execution_path_per_scanner(self):
        """Scanner summaries in stage summary include execution_path."""
        execution_records = {
            "put_credit_spread": {
                "status": "completed",
                "scanner_family": "options",
                "strategy_type": "put_credit_spread",
                "execution_path": "v2",
                "elapsed_ms": 100,
                "candidate_count": 5,
                "downstream_usable": True,
            },
            "iron_condor": {
                "status": "completed",
                "scanner_family": "options",
                "strategy_type": "iron_condor",
                "execution_path": "v2",
                "elapsed_ms": 200,
                "candidate_count": 3,
                "downstream_usable": True,
            },
        }
        summary = build_scanner_stage_summary(execution_records, {})
        for key in ("put_credit_spread", "iron_condor"):
            assert summary["scanner_summaries"][key]["execution_path"] == "v2"

    def test_stage_summary_routing_summary(self):
        """Stage summary includes routing_summary with V2/legacy counts."""
        execution_records = {
            "put_credit_spread": {
                "status": "completed",
                "scanner_family": "options",
                "execution_path": "v2",
                "elapsed_ms": 100,
                "candidate_count": 5,
                "downstream_usable": True,
            },
        }
        summary = build_scanner_stage_summary(execution_records, {})
        rs = summary["routing_summary"]
        assert "v2_scanners" in rs
        assert "legacy_scanners" in rs
        assert "v2_count" in rs
        assert "legacy_count" in rs
        assert rs["v2_count"] == 1
        assert rs["legacy_count"] == 0
        assert "put_credit_spread" in rs["v2_scanners"]

    def test_routing_summary_with_legacy_scanner(self):
        """Routing summary counts legacy scanners correctly."""
        execution_records = {
            "put_credit_spread": {
                "status": "completed",
                "scanner_family": "options",
                "execution_path": "v2",
                "elapsed_ms": 100,
                "candidate_count": 5,
                "downstream_usable": True,
            },
            "some_legacy_scanner": {
                "status": "completed",
                "scanner_family": "options",
                "execution_path": "legacy",
                "elapsed_ms": 200,
                "candidate_count": 2,
                "downstream_usable": True,
            },
        }
        summary = build_scanner_stage_summary(execution_records, {})
        rs = summary["routing_summary"]
        assert rs["v2_count"] == 1
        assert rs["legacy_count"] == 1
        assert "some_legacy_scanner" in rs["legacy_scanners"]


# =====================================================================
#  Section 5 — Migration status and routing report
# =====================================================================

class TestMigrationStatusAndRouting:
    """Migration status and routing report reflect V2-forward model."""

    def test_migration_status_has_overrides(self):
        status = get_migration_status()
        assert "version_overrides" in status
        assert isinstance(status["version_overrides"], dict)

    def test_migration_status_has_v2_families(self):
        status = get_migration_status()
        assert "v2_families_implemented" in status
        families = status["v2_families_implemented"]
        assert "vertical_spreads" in families
        assert "iron_condors" in families
        assert "butterflies" in families
        assert "calendars" in families

    def test_routing_report_shape(self):
        report = get_routing_report()
        assert report["routing_model"] == "v2_forward"
        assert isinstance(report["v2_families"], list)
        assert isinstance(report["scanner_key_routing"], dict)
        assert isinstance(report["overrides_active"], dict)
        assert isinstance(report["legacy_forced_keys"], list)
        assert isinstance(report["retirement_readiness"], dict)

    def test_routing_report_all_families_ready(self):
        """All 4 families should show retirement_readiness."""
        report = get_routing_report()
        rr = report["retirement_readiness"]
        for fk in ("vertical_spreads", "iron_condors", "butterflies", "calendars"):
            assert fk in rr
            assert rr[fk]["implemented"] is True
            assert rr[fk]["all_routing_v2"] is True

    def test_routing_report_no_legacy_forced_by_default(self):
        report = get_routing_report()
        assert report["legacy_forced_keys"] == []


# =====================================================================
#  Section 6 — Manual verification hooks (verify.py)
# =====================================================================

class TestVerifyModule:
    """verify.py provides practical validation utilities."""

    def test_verify_returns_error_without_chain(self):
        result = verify_v2_family("iron_condor", symbol="SPY")
        assert result["error"] == "chain and underlying_price are required"
        assert result["v2_implemented"] is True
        assert result["routing"] == "v2"

    def test_verify_unimplemented_returns_error(self):
        result = verify_v2_family("totally_fake_scanner")
        assert result["v2_implemented"] is False
        assert "no V2 implementation" in result["error"]

    def test_verify_report_has_required_fields(self):
        result = verify_v2_family("put_credit_spread")
        required = {
            "scanner_key", "symbol", "v2_implemented", "routing",
            "family_key", "strategy_id", "scan_result",
            "phase_counts", "reject_reason_counts",
            "candidate_count", "passed_count",
            "sample_candidates", "diagnostics_summary", "error",
        }
        assert required.issubset(result.keys())

    def test_routing_report_includes_pipeline_registry(self):
        report = get_v2_routing_report()
        assert "pipeline_registry" in report
        pr = report["pipeline_registry"]
        assert "options_scanners" in pr
        assert "stock_scanners" in pr
        assert len(pr["options_scanners"]) == 11
        assert len(pr["stock_scanners"]) == 4

    def test_family_verification_summary(self):
        summary = get_family_verification_summary()
        assert len(summary) == 4
        for fk in ("vertical_spreads", "iron_condors", "butterflies", "calendars"):
            assert fk in summary
            fs = summary[fk]
            assert fs["implemented"] is True
            assert fs["all_keys_routing_v2"] is True
            assert fs["all_keys_in_pipeline"] is True
            assert fs["ready_for_legacy_deletion"] is True

    def test_family_verification_summary_with_override(self):
        """When a key is overridden to v1, ready_for_legacy_deletion = False."""
        set_scanner_version("iron_condor", "v1")
        try:
            summary = get_family_verification_summary()
            assert summary["iron_condors"]["all_keys_routing_v2"] is False
            assert summary["iron_condors"]["ready_for_legacy_deletion"] is False
        finally:
            clear_scanner_version_override("iron_condor")


# =====================================================================
#  Section 7 — Override + routing interaction
# =====================================================================

class TestOverrideRoutingInteraction:
    """End-to-end override ↔ routing ↔ verification interaction."""

    def teardown_method(self):
        clear_scanner_version_override("butterfly_debit")
        clear_scanner_version_override("iron_condor")

    def test_override_reflects_in_routing_report(self):
        set_scanner_version("butterfly_debit", "v1")
        report = get_routing_report()
        assert report["overrides_active"].get("butterfly_debit") == "v1"
        assert "butterfly_debit" in report["legacy_forced_keys"]

    def test_override_reflects_in_verification_summary(self):
        set_scanner_version("iron_condor", "v1")
        summary = get_family_verification_summary()
        assert summary["iron_condors"]["ready_for_legacy_deletion"] is False

    def test_clear_override_restores_readiness(self):
        set_scanner_version("iron_condor", "v1")
        assert get_family_verification_summary()["iron_condors"][
            "ready_for_legacy_deletion"
        ] is False

        clear_scanner_version_override("iron_condor")
        assert get_family_verification_summary()["iron_condors"][
            "ready_for_legacy_deletion"
        ] is True
