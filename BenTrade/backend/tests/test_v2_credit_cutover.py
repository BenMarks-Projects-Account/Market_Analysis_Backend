"""Credit spreads V2 cutover tests — Prompt 7.

Tests that:
1. Migration routing is correctly set to v2 for credit spreads.
2. Debit spreads remain at v1.
3. execute_v2_scanner produces valid results for put_credit_spread.
4. execute_v2_scanner produces valid results for call_credit_spread
   (proving call credit is truly alive — legacy had a dead path).
5. Comparison harness confirms V2 output quality vs simulated legacy.
6. Pipeline integration glue routes V2 scanners correctly.
"""

from __future__ import annotations

import sys
sys.path.insert(0, ".")

import pytest

from app.services.scanner_v2.migration import (
    execute_v2_scanner,
    get_migration_status,
    get_scanner_version,
    should_run_v2,
    _v2_result_to_legacy_shape,
)
from app.services.scanner_v2.registry import get_v2_scanner, is_v2_supported
from app.services.scanner_v2.comparison.fixtures import (
    fixture_spy_golden_call_credit,
    fixture_spy_golden_put_spread,
    fixture_spy_empty_chain,
)
from app.services.scanner_v2.comparison.harness import compare_from_results


# =====================================================================
#  Section 1 — Migration routing assertions
# =====================================================================

class TestMigrationRouting:
    """Verify the version map is correctly set for credit spreads."""

    def test_put_credit_spread_is_v2(self):
        assert get_scanner_version("put_credit_spread") == "v2"

    def test_call_credit_spread_is_v2(self):
        assert get_scanner_version("call_credit_spread") == "v2"

    def test_should_run_v2_put_credit(self):
        assert should_run_v2("put_credit_spread") is True

    def test_should_run_v2_call_credit(self):
        assert should_run_v2("call_credit_spread") is True

    def test_put_debit_is_v2(self):
        # Updated in Prompt 8: debit spreads now cut over to V2
        assert get_scanner_version("put_debit") == "v2"
        assert should_run_v2("put_debit") is True

    def test_call_debit_is_v2(self):
        # Updated in Prompt 8: debit spreads now cut over to V2
        assert get_scanner_version("call_debit") == "v2"
        assert should_run_v2("call_debit") is True

    def test_iron_condor_remains_v1(self):
        assert get_scanner_version("iron_condor") == "v1"

    def test_migration_status_reflects_cutover(self):
        status = get_migration_status()
        versions = status["scanner_versions"]
        assert versions["put_credit_spread"] == "v2"
        assert versions["call_credit_spread"] == "v2"
        assert versions["put_debit"] == "v2"
        assert versions["call_debit"] == "v2"

    def test_v2_families_implemented(self):
        status = get_migration_status()
        assert "vertical_spreads" in status["v2_families_implemented"]


# =====================================================================
#  Section 2 — execute_v2_scanner produces valid output
# =====================================================================

class TestExecuteV2Scanner:
    """Run V2 scanner end-to-end on fixture data and verify output."""

    def test_put_credit_produces_candidates(self):
        snapshot = fixture_spy_golden_put_spread()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        # Should produce candidates (golden fixture has valid data)
        assert result["candidate_count"] > 0
        assert result["accepted_count"] > 0
        assert len(result["candidates"]) == result["accepted_count"]
        # Must also have accepted_trades alias for legacy compat
        assert len(result["accepted_trades"]) == result["accepted_count"]

    def test_put_credit_has_filter_trace(self):
        snapshot = fixture_spy_golden_put_spread()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        trace = result["filter_trace"]
        assert trace["preset_name"] == "v2_wide_scan"
        assert isinstance(trace["stage_counts"], list)
        assert len(trace["stage_counts"]) > 0
        assert isinstance(trace["rejection_reason_counts"], dict)
        assert isinstance(trace["data_quality_counts"], dict)

    def test_put_credit_has_v2_scan_result(self):
        """V2 scan result is attached for diagnostics."""
        snapshot = fixture_spy_golden_put_spread()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        v2_raw = result["_v2_scan_result"]
        assert v2_raw["scanner_key"] == "put_credit_spread"
        assert v2_raw["family_key"] == "vertical_spreads"
        assert v2_raw["symbol"] == "SPY"

    def test_call_credit_produces_candidates(self):
        """Call credit spread MUST produce candidates (legacy never did)."""
        snapshot = fixture_spy_golden_call_credit()
        result = execute_v2_scanner(
            "call_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        assert result["candidate_count"] > 0, (
            "call_credit_spread must produce candidates — "
            "V2 should fix the legacy dead path"
        )
        assert result["accepted_count"] > 0
        assert len(result["candidates"]) > 0

    def test_call_credit_candidates_are_calls(self):
        """Every leg must be a call option."""
        snapshot = fixture_spy_golden_call_credit()
        result = execute_v2_scanner(
            "call_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        for cand in result["candidates"]:
            for leg in cand["legs"]:
                assert leg["option_type"] == "call", (
                    f"call_credit_spread produced a non-call leg: {leg}"
                )

    def test_call_credit_spread_structure(self):
        """Call credit: short lower strike call, long higher strike call."""
        snapshot = fixture_spy_golden_call_credit()
        result = execute_v2_scanner(
            "call_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        for cand in result["candidates"]:
            legs = cand["legs"]
            assert len(legs) == 2
            short_leg = next(l for l in legs if l["side"] == "short")
            long_leg = next(l for l in legs if l["side"] == "long")
            # For call credit: short is lower strike, long is higher
            assert short_leg["strike"] < long_leg["strike"], (
                f"Call credit: short strike {short_leg['strike']} should be "
                f"< long strike {long_leg['strike']}"
            )

    def test_call_credit_has_positive_credit(self):
        """Call credit spread must produce positive net credit."""
        snapshot = fixture_spy_golden_call_credit()
        result = execute_v2_scanner(
            "call_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        for cand in result["candidates"]:
            math = cand.get("math", {})
            credit = math.get("net_credit")
            assert credit is not None and credit > 0, (
                f"Call credit should have positive net_credit, got {credit}"
            )

    def test_empty_chain_produces_zero_candidates(self):
        snapshot = fixture_spy_empty_chain()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        assert result["candidate_count"] == 0
        assert result["accepted_count"] == 0
        assert len(result["candidates"]) == 0


# =====================================================================
#  Section 3 — Comparison harness evidence
# =====================================================================

class TestComparisonEvidence:
    """Use the comparison harness to justify the cutover."""

    def _run_v2_for_comparison(
        self, scanner_key: str, snapshot,
    ) -> dict:
        """Run V2 scanner and return the V2ScanResult.to_dict() shape
        needed by compare_from_results."""
        scanner = get_v2_scanner(scanner_key)
        result = scanner.run(
            scanner_key=scanner_key,
            strategy_id=scanner_key,
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        return result.to_dict()

    def _simulate_legacy_put_credit(self, snapshot) -> dict:
        """Simulate legacy put credit result on golden fixture.

        Legacy would produce candidates for the golden put spread fixture.
        We create a simplified legacy result for comparison.
        """
        # Legacy returns accepted_trades in its own format
        return {
            "accepted_trades": [
                {
                    "symbol": snapshot.symbol,
                    "strategy_id": "put_credit_spread",
                    "expiration": "2026-03-20",
                    "legs": [
                        {"side": "short", "strike": 590.0, "option_type": "put"},
                        {"side": "long", "strike": 585.0, "option_type": "put"},
                    ],
                },
            ],
            "rejected_trades": [],
            "candidate_count": 6,
            "accepted_count": 1,
            "filter_trace": {
                "preset_name": "balanced",
                "stage_counts": [],
                "rejection_reason_counts": {},
            },
        }

    def _simulate_legacy_call_credit_dead(self) -> dict:
        """Simulate legacy call credit: always returns zero (dead path)."""
        return {
            "accepted_trades": [],
            "rejected_trades": [],
            "candidate_count": 0,
            "accepted_count": 0,
            "filter_trace": {
                "preset_name": "balanced",
                "stage_counts": [],
                "rejection_reason_counts": {},
            },
        }

    def test_put_credit_comparison(self):
        """Compare legacy vs V2 for put credit spread."""
        snapshot = fixture_spy_golden_put_spread()
        v2_result = self._run_v2_for_comparison("put_credit_spread", snapshot)
        legacy_result = self._simulate_legacy_put_credit(snapshot)

        report = compare_from_results(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert report.scanner_key == "put_credit_spread"
        assert report.v2_total_passed > 0, "V2 should pass candidates"
        # V2 should produce at least as many candidates as legacy
        assert report.v2_total_constructed >= report.legacy_total_constructed

    def test_call_credit_v2_alive_vs_legacy_dead(self):
        """V2 produces real call credit candidates; legacy produced zero.

        This is the key evidence: call credit was broken in legacy.
        """
        snapshot = fixture_spy_golden_call_credit()
        v2_result = self._run_v2_for_comparison("call_credit_spread", snapshot)
        legacy_result = self._simulate_legacy_call_credit_dead()

        report = compare_from_results(
            scanner_key="call_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert report.legacy_total_passed == 0, "Legacy should have zero (dead path)"
        assert report.v2_total_passed > 0, "V2 must produce real call credit candidates"
        assert report.v2_only_count > 0, (
            "All V2 candidates should be v2-only (legacy had none)"
        )

    def test_call_credit_v2_diagnostics_richness(self):
        """V2 produces richer diagnostics than legacy for call credit."""
        snapshot = fixture_spy_golden_call_credit()
        v2_result = self._run_v2_for_comparison("call_credit_spread", snapshot)

        # V2 result should have phase counts, reject reasons, etc.
        assert len(v2_result.get("phase_counts", [])) > 0
        assert v2_result.get("total_constructed", 0) > 0

    def test_comparison_report_serializable(self):
        """Comparison report should be serializable for artifact storage."""
        snapshot = fixture_spy_golden_put_spread()
        v2_result = self._run_v2_for_comparison("put_credit_spread", snapshot)
        legacy_result = self._simulate_legacy_put_credit(snapshot)

        report = compare_from_results(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        d = report.to_dict()
        assert isinstance(d, dict)
        assert d["scanner_key"] == "put_credit_spread"


# =====================================================================
#  Section 4 — Pipeline integration glue
# =====================================================================

class TestPipelineIntegrationGlue:
    """Verify pipeline_scanner_stage routes to V2 for credit spreads."""

    def test_v2_dispatch_exists_in_executor(self):
        """The _default_scanner_executor should import V2 migration."""
        import inspect
        from app.services.pipeline_scanner_stage import _default_scanner_executor
        source = inspect.getsource(_default_scanner_executor)
        assert "should_run_v2" in source
        assert "execute_v2_scanner" in source

    def test_scanner_results_override_works_with_v2_output(self):
        """Pipeline stage handler accepts V2-shaped scanner output
        via scanner_results_override (test/replay mode)."""
        from app.services.pipeline_artifact_store import create_artifact_store
        from app.services.pipeline_scanner_stage import scanner_stage_handler

        snapshot = fixture_spy_golden_put_spread()
        v2_output = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )

        run = {
            "run_id": "test_v2_cutover_001",
            "log_event_counts": {},
        }
        artifact_store = create_artifact_store("test_v2_cutover_001")

        result = scanner_stage_handler(
            run,
            artifact_store,
            "scanners",
            scanner_results_override={"put_credit_spread": v2_output},
            selected_scanners={"put_credit_spread"},
        )

        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_candidates"] > 0

    def test_scanner_results_override_call_credit(self):
        """Pipeline stage handler accepts V2 call credit output."""
        from app.services.pipeline_artifact_store import create_artifact_store
        from app.services.pipeline_scanner_stage import scanner_stage_handler

        snapshot = fixture_spy_golden_call_credit()
        v2_output = execute_v2_scanner(
            "call_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )

        run = {
            "run_id": "test_v2_cutover_002",
            "log_event_counts": {},
        }
        artifact_store = create_artifact_store("test_v2_cutover_002")

        result = scanner_stage_handler(
            run,
            artifact_store,
            "scanners",
            scanner_results_override={"call_credit_spread": v2_output},
            selected_scanners={"call_credit_spread"},
        )

        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_candidates"] > 0


# =====================================================================
#  Section 5 — v2_result_to_legacy_shape contract
# =====================================================================

class TestLegacyShapeAdapter:
    """Verify _v2_result_to_legacy_shape produces pipeline-compatible output."""

    def test_has_candidates_key(self):
        """Pipeline reads 'candidates', not just 'accepted_trades'."""
        snapshot = fixture_spy_golden_put_spread()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        assert "candidates" in result
        assert "accepted_trades" in result
        assert result["candidates"] == result["accepted_trades"]

    def test_has_filter_trace(self):
        snapshot = fixture_spy_golden_put_spread()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        trace = result["filter_trace"]
        required_keys = {
            "preset_name", "resolved_thresholds", "stage_counts",
            "rejection_reason_counts", "data_quality_counts",
        }
        assert required_keys.issubset(trace.keys())

    def test_has_v2_scan_result(self):
        snapshot = fixture_spy_golden_put_spread()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        assert "_v2_scan_result" in result
        assert result["_v2_scan_result"]["scanner_key"] == "put_credit_spread"
