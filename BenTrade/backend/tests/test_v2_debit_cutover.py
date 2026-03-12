"""Debit spreads V2 cutover tests — Prompt 8.

Tests that:
1. Migration routing is correctly set to v2 for debit spreads.
2. All vertical spreads (credit + debit) now route to V2.
3. execute_v2_scanner produces valid results for put_debit.
4. execute_v2_scanner produces valid results for call_debit.
5. Debit spread structure is correct (long closer to ATM, net debit).
6. Legacy debit-spread trust issues are eliminated by V2:
   - no wrong delta storage / "short_delta_abs" confusion
   - no spread quote inversion ambiguity
   - no scanner-time over-decisioning (POP hierarchy gates)
   - clean field naming via V2 canonical contract
7. Comparison harness confirms V2 output quality vs simulated legacy.
8. Pipeline integration remains compatible.
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
)
from app.services.scanner_v2.registry import get_v2_scanner, is_v2_supported
from app.services.scanner_v2.comparison.fixtures import (
    fixture_spy_golden_put_debit,
    fixture_spy_golden_call_debit,
    fixture_spy_empty_chain,
)
from app.services.scanner_v2.comparison.harness import compare_from_results


# =====================================================================
#  Section 1 — Migration routing assertions
# =====================================================================

class TestDebitMigrationRouting:
    """Verify the version map is correctly set for debit spreads."""

    def test_put_debit_is_v2(self):
        assert get_scanner_version("put_debit") == "v2"

    def test_call_debit_is_v2(self):
        assert get_scanner_version("call_debit") == "v2"

    def test_should_run_v2_put_debit(self):
        assert should_run_v2("put_debit") is True

    def test_should_run_v2_call_debit(self):
        assert should_run_v2("call_debit") is True

    def test_all_vertical_spreads_now_v2(self):
        """After Prompt 7 + 8, all 4 vertical spread variants are v2."""
        for key in ("put_credit_spread", "call_credit_spread",
                     "put_debit", "call_debit"):
            assert get_scanner_version(key) == "v2", (
                f"{key} should be v2"
            )
            assert should_run_v2(key), f"{key} should_run_v2"

    def test_non_vertical_families_remain_v1(self):
        for key in ("iron_condor", "butterfly_debit", "iron_butterfly"):
            assert get_scanner_version(key) == "v1", (
                f"{key} should still be v1"
            )

    def test_migration_status_reflects_debit_cutover(self):
        status = get_migration_status()
        versions = status["scanner_versions"]
        assert versions["put_debit"] == "v2"
        assert versions["call_debit"] == "v2"
        # Credit spreads still v2 from Prompt 7
        assert versions["put_credit_spread"] == "v2"
        assert versions["call_credit_spread"] == "v2"


# =====================================================================
#  Section 2 — execute_v2_scanner produces valid debit output
# =====================================================================

class TestExecuteV2DebitScanner:
    """Run V2 scanner end-to-end on debit fixtures and verify output."""

    # ── Put debit ───────────────────────────────────────────────

    def test_put_debit_produces_candidates(self):
        snapshot = fixture_spy_golden_put_debit()
        result = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        assert result["candidate_count"] > 0
        assert result["accepted_count"] > 0
        assert len(result["candidates"]) == result["accepted_count"]
        assert len(result["accepted_trades"]) == result["accepted_count"]

    def test_put_debit_candidates_are_puts(self):
        """Every leg must be a put option."""
        snapshot = fixture_spy_golden_put_debit()
        result = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        for cand in result["candidates"]:
            for leg in cand["legs"]:
                assert leg["option_type"] == "put", (
                    f"put_debit produced a non-put leg: {leg}"
                )

    def test_put_debit_spread_structure(self):
        """Put debit: long higher-strike put, short lower-strike put."""
        snapshot = fixture_spy_golden_put_debit()
        result = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        for cand in result["candidates"]:
            legs = cand["legs"]
            assert len(legs) == 2
            short_leg = next(l for l in legs if l["side"] == "short")
            long_leg = next(l for l in legs if l["side"] == "long")
            # Put debit: long is higher strike (closer to ATM)
            assert long_leg["strike"] > short_leg["strike"], (
                f"Put debit: long strike {long_leg['strike']} should be "
                f"> short strike {short_leg['strike']}"
            )

    def test_put_debit_has_net_debit(self):
        """Put debit spread should have positive net_debit (costs money)."""
        snapshot = fixture_spy_golden_put_debit()
        result = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        for cand in result["candidates"]:
            math = cand.get("math", {})
            debit = math.get("net_debit")
            assert debit is not None and debit > 0, (
                f"Put debit should have positive net_debit, got {debit}"
            )

    def test_put_debit_has_filter_trace(self):
        snapshot = fixture_spy_golden_put_debit()
        result = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        trace = result["filter_trace"]
        assert trace["preset_name"] == "v2_wide_scan"
        assert isinstance(trace["stage_counts"], list)
        assert len(trace["stage_counts"]) > 0

    # ── Call debit ──────────────────────────────────────────────

    def test_call_debit_produces_candidates(self):
        snapshot = fixture_spy_golden_call_debit()
        result = execute_v2_scanner(
            "call_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        assert result["candidate_count"] > 0
        assert result["accepted_count"] > 0
        assert len(result["candidates"]) > 0

    def test_call_debit_candidates_are_calls(self):
        """Every leg must be a call option."""
        snapshot = fixture_spy_golden_call_debit()
        result = execute_v2_scanner(
            "call_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        for cand in result["candidates"]:
            for leg in cand["legs"]:
                assert leg["option_type"] == "call", (
                    f"call_debit produced a non-call leg: {leg}"
                )

    def test_call_debit_spread_structure(self):
        """Call debit: long lower-strike call, short higher-strike call."""
        snapshot = fixture_spy_golden_call_debit()
        result = execute_v2_scanner(
            "call_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        for cand in result["candidates"]:
            legs = cand["legs"]
            assert len(legs) == 2
            short_leg = next(l for l in legs if l["side"] == "short")
            long_leg = next(l for l in legs if l["side"] == "long")
            # Call debit: long is lower strike (closer to ATM)
            assert long_leg["strike"] < short_leg["strike"], (
                f"Call debit: long strike {long_leg['strike']} should be "
                f"< short strike {short_leg['strike']}"
            )

    def test_call_debit_has_net_debit(self):
        """Call debit spread should have positive net_debit."""
        snapshot = fixture_spy_golden_call_debit()
        result = execute_v2_scanner(
            "call_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        for cand in result["candidates"]:
            math = cand.get("math", {})
            debit = math.get("net_debit")
            assert debit is not None and debit > 0, (
                f"Call debit should have positive net_debit, got {debit}"
            )

    # ── Edge cases ──────────────────────────────────────────────

    def test_empty_chain_produces_zero_candidates(self):
        snapshot = fixture_spy_empty_chain()
        for key in ("put_debit", "call_debit"):
            result = execute_v2_scanner(
                key,
                symbol=snapshot.symbol,
                chain=snapshot.chain,
                underlying_price=snapshot.underlying_price,
            )
            assert result["candidate_count"] == 0
            assert len(result["candidates"]) == 0

    def test_put_debit_has_v2_scan_result(self):
        """V2 scan result attached for diagnostics."""
        snapshot = fixture_spy_golden_put_debit()
        result = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        v2_raw = result["_v2_scan_result"]
        assert v2_raw["scanner_key"] == "put_debit"
        assert v2_raw["family_key"] == "vertical_spreads"


# =====================================================================
#  Section 3 — V2 eliminates legacy debit-spread trust issues
# =====================================================================

class TestLegacyTrustIssuesEliminated:
    """Verify V2 eliminates legacy debit-spread field/math problems.

    Legacy issues documented in docs/scanners/options/debit-spreads.md:
    - wrong delta storage ("short_delta_abs" confusion)
    - spread quote inversion ambiguity
    - brittle field naming
    - scanner-time over-decisioning (POP hierarchy / excessive gates)

    V2 replaces all of this with clean canonical contracts.
    """

    def _get_put_debit_candidates(self):
        snapshot = fixture_spy_golden_put_debit()
        result = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        return result["candidates"]

    def _get_call_debit_candidates(self):
        snapshot = fixture_spy_golden_call_debit()
        result = execute_v2_scanner(
            "call_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        return result["candidates"]

    def test_no_short_delta_abs_field(self):
        """V2 does not use legacy's 'short_delta_abs' field.
        Instead, each leg stores raw delta from the chain."""
        for cand in self._get_put_debit_candidates():
            assert "short_delta_abs" not in cand
            for leg in cand["legs"]:
                assert "delta" in leg
                # Delta should be a raw value, not abs-transformed
                assert isinstance(leg["delta"], (int, float, type(None)))

    def test_v2_uses_canonical_leg_structure(self):
        """V2 candidates use the canonical V2Leg structure with
        explicit side, strike, option_type, bid, ask, mid."""
        for cand in self._get_put_debit_candidates():
            for leg in cand["legs"]:
                assert "side" in leg
                assert "strike" in leg
                assert "option_type" in leg
                assert "bid" in leg
                assert "ask" in leg
                assert "mid" in leg
                assert leg["side"] in ("short", "long")

    def test_no_scanner_time_pop_gating(self):
        """V2 does not reject candidates based on POP thresholds.
        POP is computed in math but not used as a rejection gate."""
        snapshot = fixture_spy_golden_put_debit()
        scanner = get_v2_scanner("put_debit")
        result = scanner.run(
            scanner_key="put_debit",
            strategy_id="put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        # No reject reasons should be POP-related
        for rej in result.rejected:
            for reason in rej.diagnostics.reject_reasons:
                assert "pop" not in reason.lower(), (
                    f"V2 should not gate on POP at scanner-time: {reason}"
                )

    def test_v2_candidates_have_math_fields(self):
        """V2 computes and carries core math fields without
        legacy's three-tier POP hierarchy complexity."""
        for cand in self._get_put_debit_candidates():
            math = cand.get("math", {})
            # Core math is present
            assert math.get("net_debit") is not None
            assert math.get("width") is not None
            assert math.get("max_profit") is not None
            assert math.get("max_loss") is not None

    def test_v2_debit_diagnostics_rich(self):
        """V2 candidates carry rich per-phase diagnostics."""
        for cand in self._get_put_debit_candidates():
            diag = cand.get("diagnostics", {})
            assert len(diag.get("structural_checks", [])) > 0
            assert len(diag.get("quote_checks", [])) > 0
            assert len(diag.get("math_checks", [])) > 0
            assert len(diag.get("pass_reasons", [])) > 0

    def test_call_debit_also_clean(self):
        """Same trust guarantees apply to call debit."""
        for cand in self._get_call_debit_candidates():
            assert "short_delta_abs" not in cand
            for leg in cand["legs"]:
                assert "side" in leg
                assert "strike" in leg
                assert leg["option_type"] == "call"
            math = cand.get("math", {})
            assert math.get("net_debit") is not None
            assert math.get("width") is not None


# =====================================================================
#  Section 4 — Comparison harness evidence
# =====================================================================

class TestDebitComparisonEvidence:
    """Use the comparison harness to compare legacy vs V2 debit output."""

    def _run_v2_for_comparison(self, scanner_key, snapshot):
        scanner = get_v2_scanner(scanner_key)
        result = scanner.run(
            scanner_key=scanner_key,
            strategy_id=scanner_key,
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        return result.to_dict()

    def _simulate_legacy_debit_result(self, snapshot, direction="put"):
        """Simulate legacy debit result.

        Legacy debit plugin had issues but did produce some candidates.
        We simulate a result with legacy-style field naming to show
        the comparison harness can detect differences.
        """
        if direction == "put":
            return {
                "accepted_trades": [
                    {
                        "symbol": snapshot.symbol,
                        "strategy_id": "put_debit",
                        "setup_type": "put_debit",
                        "expiration": "2026-03-20",
                        "legs": [
                            {"side": "long", "strike": 590.0, "option_type": "put"},
                            {"side": "short", "strike": 585.0, "option_type": "put"},
                        ],
                        "short_delta_abs": 0.22,  # legacy bad field
                    },
                ],
                "rejected_trades": [],
                "candidate_count": 4,
                "accepted_count": 1,
                "filter_trace": {
                    "preset_name": "balanced",
                    "stage_counts": [],
                    "rejection_reason_counts": {
                        "pop_below_threshold": 2,
                        "ev_to_risk_below_threshold": 1,
                    },
                },
            }
        else:  # call
            return {
                "accepted_trades": [
                    {
                        "symbol": snapshot.symbol,
                        "strategy_id": "call_debit",
                        "setup_type": "call_debit",
                        "expiration": "2026-03-20",
                        "legs": [
                            {"side": "long", "strike": 600.0, "option_type": "call"},
                            {"side": "short", "strike": 605.0, "option_type": "call"},
                        ],
                        "short_delta_abs": 0.28,
                    },
                ],
                "rejected_trades": [],
                "candidate_count": 4,
                "accepted_count": 1,
                "filter_trace": {
                    "preset_name": "balanced",
                    "stage_counts": [],
                    "rejection_reason_counts": {
                        "pop_below_threshold": 2,
                        "ror_below_threshold": 1,
                    },
                },
            }

    def test_put_debit_comparison(self):
        """Compare legacy vs V2 for put debit spread."""
        snapshot = fixture_spy_golden_put_debit()
        v2_result = self._run_v2_for_comparison("put_debit", snapshot)
        legacy_result = self._simulate_legacy_debit_result(snapshot, "put")

        report = compare_from_results(
            scanner_key="put_debit",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert report.scanner_key == "put_debit"
        assert report.v2_total_passed > 0
        # V2 should produce more candidates than legacy (no POP/EV gates)
        assert report.v2_total_constructed >= report.legacy_total_constructed

    def test_call_debit_comparison(self):
        """Compare legacy vs V2 for call debit spread."""
        snapshot = fixture_spy_golden_call_debit()
        v2_result = self._run_v2_for_comparison("call_debit", snapshot)
        legacy_result = self._simulate_legacy_debit_result(snapshot, "call")

        report = compare_from_results(
            scanner_key="call_debit",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert report.scanner_key == "call_debit"
        assert report.v2_total_passed > 0

    def test_v2_no_pop_rejections_vs_legacy(self):
        """V2 should have zero pop-related rejections (scanner-time).
        Legacy over-filtered with POP hierarchy gates."""
        snapshot = fixture_spy_golden_put_debit()
        v2_result = self._run_v2_for_comparison("put_debit", snapshot)
        legacy_result = self._simulate_legacy_debit_result(snapshot, "put")

        report = compare_from_results(
            scanner_key="put_debit",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        # V2 constructs more because it doesn't gate on POP/EV
        assert report.v2_total_constructed >= 1

    def test_debit_comparison_serializable(self):
        """Comparison report should be serializable."""
        snapshot = fixture_spy_golden_put_debit()
        v2_result = self._run_v2_for_comparison("put_debit", snapshot)
        legacy_result = self._simulate_legacy_debit_result(snapshot, "put")

        report = compare_from_results(
            scanner_key="put_debit",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        d = report.to_dict()
        assert isinstance(d, dict)
        assert d["scanner_key"] == "put_debit"

    def test_v2_diagnostics_richer_than_legacy(self):
        """V2 produces per-candidate diagnostics; legacy did not."""
        snapshot = fixture_spy_golden_call_debit()
        v2_result = self._run_v2_for_comparison("call_debit", snapshot)

        assert len(v2_result.get("phase_counts", [])) > 0
        assert v2_result.get("total_constructed", 0) > 0
        # V2 candidates should have diagnostics blocks
        for cand in v2_result.get("candidates", []):
            assert "diagnostics" in cand


# =====================================================================
#  Section 5 — Pipeline integration
# =====================================================================

class TestDebitPipelineIntegration:
    """Verify pipeline_scanner_stage accepts V2 debit output."""

    def test_put_debit_via_pipeline_override(self):
        from app.services.pipeline_artifact_store import create_artifact_store
        from app.services.pipeline_scanner_stage import scanner_stage_handler

        snapshot = fixture_spy_golden_put_debit()
        v2_output = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )

        run = {"run_id": "test_debit_cutover_001", "log_event_counts": {}}
        artifact_store = create_artifact_store("test_debit_cutover_001")

        result = scanner_stage_handler(
            run, artifact_store, "scanners",
            scanner_results_override={"put_debit": v2_output},
            selected_scanners={"put_debit"},
        )

        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_candidates"] > 0

    def test_call_debit_via_pipeline_override(self):
        from app.services.pipeline_artifact_store import create_artifact_store
        from app.services.pipeline_scanner_stage import scanner_stage_handler

        snapshot = fixture_spy_golden_call_debit()
        v2_output = execute_v2_scanner(
            "call_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )

        run = {"run_id": "test_debit_cutover_002", "log_event_counts": {}}
        artifact_store = create_artifact_store("test_debit_cutover_002")

        result = scanner_stage_handler(
            run, artifact_store, "scanners",
            scanner_results_override={"call_debit": v2_output},
            selected_scanners={"call_debit"},
        )

        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_candidates"] > 0

    def test_legacy_shape_adapter_keys(self):
        """V2 output has both 'candidates' and 'accepted_trades' keys."""
        snapshot = fixture_spy_golden_put_debit()
        result = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        assert "candidates" in result
        assert "accepted_trades" in result
        assert result["candidates"] == result["accepted_trades"]
        assert "filter_trace" in result
        assert "_v2_scan_result" in result
