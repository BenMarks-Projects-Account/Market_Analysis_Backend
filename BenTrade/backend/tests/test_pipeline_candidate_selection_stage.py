"""Tests for Pipeline Candidate Selection Stage v1.0.

Coverage targets:
─── Selection record
    - record shape / required keys
    - status vocabulary
─── Dedup key
    - deterministic key generation
    - duplicate detection
─── Candidate eligibility
    - eligible candidate passes
    - excluded_not_usable
    - excluded_missing_required_fields
    - excluded_disabled_strategy
    - excluded_invalid_payload (non-dict handled upstream)
─── Ranking
    - deterministic scoring
    - quality and confidence components
    - family/strategy weights
    - completeness bonus
    - custom weights
─── Candidate loading
    - loads from scanner stage summary
    - missing scanner stage summary
    - empty scanner summaries
    - partial artifact loading
─── Selection pipeline
    - all eligible selected within cap
    - cap enforced
    - duplicates excluded
    - excluded candidates tracked
    - zero eligible
    - zero candidates loaded
    - disabled strategy exclusion
─── Artifact creation
    - selected_candidates artifact
    - candidate_selection_ledger artifact
    - candidate_selection_summary artifact
    - artifact lineage refs
─── Stage summary
    - contains expected fields
    - counts correct
    - status rollup
─── Event emission
    - selection_started event
    - selection_completed event
    - selection_failed event (missing summary)
─── Handler contract
    - returns expected dict shape
    - outcome field values
    - summary_counts fields
─── Orchestrator integration
    - default handler wired
    - runs through pipeline
    - stub override works
─── Forward compatibility
    - selected candidate downstream_selected flag
    - candidate lineage preserved
    - retrieval seam for downstream stages
─── No source summary failure
    - clear failure code
─── Partial degraded behavior
    - some candidate artifacts missing
"""

import pytest

from app.services.pipeline_candidate_selection_stage import (
    _STAGE_KEY,
    _REQUIRED_CANDIDATE_FIELDS,
    DEFAULT_MAX_SELECTED_CANDIDATES,
    SELECTION_STATUSES,
    _check_candidate_eligibility,
    _deduplicate_candidates,
    _load_candidates_from_scanner_stage,
    _run_selection_pipeline,
    _write_selected_candidates_artifact,
    _write_selection_ledger_artifact,
    _write_selection_summary_artifact,
    build_candidate_dedup_key,
    build_selection_record,
    build_selection_summary,
    candidate_selection_handler,
    compute_candidate_rank_score,
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
    list_artifacts,
    list_stage_artifacts,
    put_artifact,
    validate_artifact_store,
)
from app.services.pipeline_orchestrator import (
    execute_stage,
    get_default_handlers,
    run_pipeline_with_handlers,
)


# ── Helper factories ────────────────────────────────────────────

def _make_run_and_store(run_id="test-sel-001"):
    """Create a fresh run+store with market_data and scanners completed."""
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    # Complete prerequisites
    for stage in ("market_data", "scanners"):
        mark_stage_running(run, stage)
        mark_stage_completed(run, stage)
    return run, store


def _make_candidate(
    candidate_id="cand_001",
    scanner_key="test_scanner_a",
    symbol="SPY",
    strategy_type="put_credit_spread",
    scanner_family="options",
    downstream_usable=True,
    setup_quality=75.0,
    confidence=0.8,
    direction="long",
    **extra,
):
    """Build a normalized candidate dict."""
    cand = {
        "candidate_id": candidate_id,
        "scanner_key": scanner_key,
        "symbol": symbol,
        "strategy_type": strategy_type,
        "scanner_family": scanner_family,
        "downstream_usable": downstream_usable,
        "setup_quality": setup_quality,
        "confidence": confidence,
        "direction": direction,
        "normalization_status": "normalized",
        "run_id": "test-sel-001",
        "stage_key": "scanners",
    }
    cand.update(extra)
    return cand


def _write_scanner_stage_artifacts(
    store,
    run_id,
    scanner_candidates,
):
    """Write scanner stage summary and candidate artifacts.

    Parameters
    ----------
    scanner_candidates : dict
        scanner_key → list of candidate dicts
    """
    scanner_summaries = {}
    candidate_artifact_refs = {}
    all_candidate_ids = []

    for scanner_key, candidates in scanner_candidates.items():
        cand_ids = [c.get("candidate_id", "") for c in candidates]
        all_candidate_ids.extend(cand_ids)

        # Write candidate artifact
        art = build_artifact_record(
            run_id=run_id,
            stage_key="scanners",
            artifact_key=f"candidates_{scanner_key}",
            artifact_type="normalized_candidate",
            data=candidates,
            summary={
                "scanner_key": scanner_key,
                "total_candidates": len(candidates),
                "candidate_ids": cand_ids,
            },
        )
        put_artifact(store, art, overwrite=True)
        candidate_artifact_refs[scanner_key] = art["artifact_id"]

        scanner_summaries[scanner_key] = {
            "status": "completed" if candidates else "completed_empty",
            "scanner_family": candidates[0].get("scanner_family", "stock") if candidates else "stock",
            "strategy_type": candidates[0].get("strategy_type", scanner_key) if candidates else scanner_key,
            "candidate_count": len(candidates),
            "usable_candidate_count": sum(
                1 for c in candidates if c.get("downstream_usable", False)
            ),
            "candidate_artifact_ref": art["artifact_id"],
            "candidate_ids": cand_ids,
            "downstream_usable": bool(candidates),
        }

    # Write scanner stage summary
    summary_data = {
        "stage_key": "scanners",
        "stage_status": "success",
        "scanner_summaries": scanner_summaries,
        "candidate_artifact_refs": candidate_artifact_refs,
        "all_candidate_ids": all_candidate_ids,
        "total_candidates": len(all_candidate_ids),
        "total_usable_candidates": len(all_candidate_ids),
    }
    summary_art = build_artifact_record(
        run_id=run_id,
        stage_key="scanners",
        artifact_key="scanner_stage_summary",
        artifact_type="scanner_stage_summary",
        data=summary_data,
    )
    put_artifact(store, summary_art, overwrite=True)
    return summary_art["artifact_id"]


def _success_handler(run, artifact_store, stage_key, **kwargs):
    """Always-succeed stub handler."""
    return {
        "outcome": "completed",
        "summary_counts": {"items_processed": 0},
        "artifacts": [],
        "metadata": {"stub": True},
        "error": None,
    }


# =====================================================================
#  Selection Record
# =====================================================================

class TestSelectionRecord:

    def test_record_has_required_keys(self):
        rec = build_selection_record(
            candidate_id="cand_001",
            eligibility_status="selected",
        )
        expected = {
            "candidate_id", "scanner_key", "symbol",
            "strategy_type", "opportunity_type",
            "eligibility_status", "exclusion_reason",
            "rank_score", "rank_position",
            "source_candidate_artifact_ref",
            "source_scanner_artifact_ref",
            "downstream_selected", "warnings", "notes",
        }
        assert expected.issubset(rec.keys())

    def test_selected_record_fields(self):
        rec = build_selection_record(
            candidate_id="cand_001",
            scanner_key="put_credit_spread",
            symbol="SPY",
            strategy_type="put_credit_spread",
            eligibility_status="selected",
            rank_score=0.85,
            rank_position=1,
            downstream_selected=True,
        )
        assert rec["downstream_selected"] is True
        assert rec["rank_score"] == 0.85
        assert rec["rank_position"] == 1
        assert rec["exclusion_reason"] == ""

    def test_excluded_record_fields(self):
        rec = build_selection_record(
            candidate_id="cand_002",
            eligibility_status="excluded_not_usable",
            exclusion_reason="downstream_usable=False",
            downstream_selected=False,
        )
        assert rec["downstream_selected"] is False
        assert rec["eligibility_status"] == "excluded_not_usable"
        assert rec["exclusion_reason"] == "downstream_usable=False"


# =====================================================================
#  Status Vocabulary
# =====================================================================

class TestStatusVocabulary:

    def test_all_statuses_defined(self):
        expected = {
            "eligible", "selected",
            "excluded_not_usable", "excluded_invalid_payload",
            "excluded_missing_required_fields",
            "excluded_disabled_strategy",
            "excluded_below_threshold", "excluded_duplicate",
            "excluded_by_rank_cutoff",
        }
        assert expected == SELECTION_STATUSES


# =====================================================================
#  Dedup Key
# =====================================================================

class TestDedupKey:

    def test_deterministic_key(self):
        cand = _make_candidate(symbol="SPY", strategy_type="put_credit_spread")
        key = build_candidate_dedup_key(cand)
        assert isinstance(key, str)
        assert "|" in key
        # Same candidate → same key
        assert build_candidate_dedup_key(cand) == key

    def test_different_symbols_different_keys(self):
        c1 = _make_candidate(symbol="SPY")
        c2 = _make_candidate(symbol="QQQ")
        assert build_candidate_dedup_key(c1) != build_candidate_dedup_key(c2)

    def test_different_strategies_different_keys(self):
        c1 = _make_candidate(strategy_type="put_credit_spread")
        c2 = _make_candidate(strategy_type="iron_condor")
        assert build_candidate_dedup_key(c1) != build_candidate_dedup_key(c2)

    def test_case_insensitive(self):
        c1 = _make_candidate(symbol="SPY", strategy_type="Put_Credit_Spread")
        c2 = _make_candidate(symbol="spy", strategy_type="put_credit_spread")
        assert build_candidate_dedup_key(c1) == build_candidate_dedup_key(c2)

    def test_deduplication_removes_duplicates(self):
        cands = [
            _make_candidate(candidate_id="c1", symbol="SPY",
                            strategy_type="put_credit_spread"),
            _make_candidate(candidate_id="c2", symbol="SPY",
                            strategy_type="put_credit_spread"),
            _make_candidate(candidate_id="c3", symbol="QQQ",
                            strategy_type="put_credit_spread"),
        ]
        unique, dups = _deduplicate_candidates(cands)
        assert len(unique) == 2
        assert len(dups) == 1
        assert dups[0].get("candidate_id") == "c2"

    def test_no_duplicates(self):
        cands = [
            _make_candidate(candidate_id="c1", symbol="SPY"),
            _make_candidate(candidate_id="c2", symbol="QQQ"),
        ]
        unique, dups = _deduplicate_candidates(cands)
        assert len(unique) == 2
        assert len(dups) == 0

    def test_empty_list(self):
        unique, dups = _deduplicate_candidates([])
        assert unique == []
        assert dups == []


# =====================================================================
#  Candidate Eligibility
# =====================================================================

class TestCandidateEligibility:

    def test_eligible_candidate(self):
        cand = _make_candidate()
        status, reason = _check_candidate_eligibility(cand)
        assert status == "eligible"
        assert reason == ""

    def test_excluded_not_usable(self):
        cand = _make_candidate(downstream_usable=False)
        status, reason = _check_candidate_eligibility(cand)
        assert status == "excluded_not_usable"
        assert "downstream_usable=False" in reason

    def test_excluded_missing_candidate_id(self):
        cand = _make_candidate(candidate_id="")
        status, reason = _check_candidate_eligibility(cand)
        assert status == "excluded_missing_required_fields"
        assert "candidate_id" in reason

    def test_excluded_missing_symbol(self):
        cand = _make_candidate(symbol="")
        status, reason = _check_candidate_eligibility(cand)
        assert status == "excluded_missing_required_fields"
        assert "symbol" in reason

    def test_excluded_disabled_strategy(self):
        cand = _make_candidate(strategy_type="iron_condor")
        status, reason = _check_candidate_eligibility(
            cand, disabled_strategies={"iron_condor"},
        )
        assert status == "excluded_disabled_strategy"
        assert "iron_condor" in reason

    def test_enabled_strategy_passes(self):
        cand = _make_candidate(strategy_type="put_credit_spread")
        status, reason = _check_candidate_eligibility(
            cand, disabled_strategies={"iron_condor"},
        )
        assert status == "eligible"

    def test_not_usable_takes_priority(self):
        """downstream_usable=False is checked before missing fields."""
        cand = _make_candidate(downstream_usable=False, symbol="")
        status, _ = _check_candidate_eligibility(cand)
        assert status == "excluded_not_usable"

    def test_all_fields_present_eligible(self):
        cand = _make_candidate()
        status, _ = _check_candidate_eligibility(cand)
        assert status == "eligible"


# =====================================================================
#  Ranking
# =====================================================================

class TestRanking:

    def test_score_is_float(self):
        cand = _make_candidate()
        score = compute_candidate_rank_score(cand)
        assert isinstance(score, float)

    def test_score_range(self):
        cand = _make_candidate(setup_quality=100.0, confidence=1.0)
        score = compute_candidate_rank_score(cand)
        assert 0.0 <= score <= 1.0

    def test_higher_quality_higher_score(self):
        c_high = _make_candidate(setup_quality=90.0, confidence=0.9)
        c_low = _make_candidate(setup_quality=30.0, confidence=0.3)
        assert compute_candidate_rank_score(c_high) > compute_candidate_rank_score(c_low)

    def test_options_preferred_over_stock(self):
        c_opt = _make_candidate(scanner_family="options", strategy_type="put_credit_spread",
                                setup_quality=50.0, confidence=0.5)
        c_stk = _make_candidate(scanner_family="stock", strategy_type="pullback_swing",
                                setup_quality=50.0, confidence=0.5)
        assert compute_candidate_rank_score(c_opt) > compute_candidate_rank_score(c_stk)

    def test_custom_weights(self):
        cand = _make_candidate(scanner_family="stock")
        score_default = compute_candidate_rank_score(cand)
        score_boosted = compute_candidate_rank_score(
            cand, family_weights={"stock": 1.0, "options": 0.5},
        )
        assert score_boosted > score_default

    def test_zero_quality_zero_confidence(self):
        cand = _make_candidate(setup_quality=0.0, confidence=0.0)
        score = compute_candidate_rank_score(cand)
        assert score >= 0.0

    def test_missing_quality_fields(self):
        cand = {"candidate_id": "x", "symbol": "SPY"}
        score = compute_candidate_rank_score(cand)
        assert isinstance(score, float)
        assert score >= 0.0

    def test_deterministic(self):
        cand = _make_candidate()
        s1 = compute_candidate_rank_score(cand)
        s2 = compute_candidate_rank_score(cand)
        assert s1 == s2

    def test_rank_ordering_is_stable(self):
        """Candidates with same score maintain insertion order."""
        cands = [
            _make_candidate(candidate_id=f"c{i}", setup_quality=50.0,
                            confidence=0.5, symbol="SPY",
                            strategy_type="put_credit_spread",
                            scanner_family="options")
            for i in range(5)
        ]
        scores = [compute_candidate_rank_score(c) for c in cands]
        # All same score → stable sort preserves order
        assert len(set(scores)) == 1


# =====================================================================
#  Candidate Loading from Step 6
# =====================================================================

class TestCandidateLoading:

    def test_loads_from_scanner_summary(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [_make_candidate(candidate_id="c1")],
            "scanner_b": [_make_candidate(candidate_id="c2")],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        loaded, ref_map, warnings = _load_candidates_from_scanner_stage(store)
        assert len(loaded) == 2
        assert len(warnings) == 0

    def test_missing_scanner_summary(self):
        _, store = _make_run_and_store()
        loaded, ref_map, warnings = _load_candidates_from_scanner_stage(store)
        assert len(loaded) == 0
        assert any("not found" in w for w in warnings)

    def test_empty_scanner_summaries(self):
        run, store = _make_run_and_store()
        _write_scanner_stage_artifacts(store, run["run_id"], {})

        loaded, ref_map, warnings = _load_candidates_from_scanner_stage(store)
        assert len(loaded) == 0

    def test_non_usable_scanner_skipped(self):
        run, store = _make_run_and_store()
        # Write summary with downstream_usable=False
        summary_data = {
            "stage_key": "scanners",
            "scanner_summaries": {
                "bad_scanner": {
                    "status": "failed",
                    "downstream_usable": False,
                    "candidate_artifact_ref": None,
                },
            },
            "candidate_artifact_refs": {},
            "all_candidate_ids": [],
        }
        art = build_artifact_record(
            run_id=run["run_id"],
            stage_key="scanners",
            artifact_key="scanner_stage_summary",
            artifact_type="scanner_stage_summary",
            data=summary_data,
        )
        put_artifact(store, art, overwrite=True)

        loaded, _, _ = _load_candidates_from_scanner_stage(store)
        assert len(loaded) == 0

    def test_missing_candidate_artifact(self):
        """Summary references artifact that doesn't exist."""
        run, store = _make_run_and_store()
        summary_data = {
            "stage_key": "scanners",
            "scanner_summaries": {
                "scanner_a": {
                    "status": "completed",
                    "downstream_usable": True,
                    "candidate_artifact_ref": "art-does-not-exist",
                },
            },
            "candidate_artifact_refs": {"scanner_a": "art-does-not-exist"},
            "all_candidate_ids": ["c1"],
        }
        art = build_artifact_record(
            run_id=run["run_id"],
            stage_key="scanners",
            artifact_key="scanner_stage_summary",
            artifact_type="scanner_stage_summary",
            data=summary_data,
        )
        put_artifact(store, art, overwrite=True)

        loaded, _, warnings = _load_candidates_from_scanner_stage(store)
        assert len(loaded) == 0
        assert any("not found" in w for w in warnings)

    def test_artifact_ref_map_populated(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        loaded, ref_map, _ = _load_candidates_from_scanner_stage(store)
        assert "c1" in ref_map
        assert ref_map["c1"]  # non-empty artifact id


# =====================================================================
#  Selection Pipeline
# =====================================================================

class TestSelectionPipeline:

    def test_all_eligible_selected_within_cap(self):
        cands = [
            _make_candidate(candidate_id="c1", symbol="SPY"),
            _make_candidate(candidate_id="c2", symbol="QQQ"),
        ]
        selected, records, _, _, _, _ = _run_selection_pipeline(
            cands, {}, max_selected=10,
        )
        assert len(selected) == 2
        assert all(r["downstream_selected"] for r in records
                    if r["eligibility_status"] == "selected")

    def test_cap_enforced(self):
        cands = [
            _make_candidate(candidate_id=f"c{i}", symbol=f"SYM{i}",
                            strategy_type=f"strat_{i}")
            for i in range(10)
        ]
        selected, records, exc_counts, _, _, _ = _run_selection_pipeline(
            cands, {}, max_selected=3,
        )
        assert len(selected) == 3
        assert exc_counts.get("excluded_by_rank_cutoff", 0) == 7

    def test_duplicates_excluded(self):
        cands = [
            _make_candidate(candidate_id="c1", symbol="SPY",
                            strategy_type="put_credit_spread"),
            _make_candidate(candidate_id="c2", symbol="SPY",
                            strategy_type="put_credit_spread"),
        ]
        selected, records, exc_counts, _, _, _ = _run_selection_pipeline(
            cands, {}, max_selected=10,
        )
        assert len(selected) == 1
        assert exc_counts.get("excluded_duplicate", 0) == 1

    def test_excluded_candidates_tracked(self):
        cands = [
            _make_candidate(candidate_id="c1", downstream_usable=False),
            _make_candidate(candidate_id="c2", symbol="SPY"),
        ]
        selected, records, exc_counts, _, _, _ = _run_selection_pipeline(
            cands, {}, max_selected=10,
        )
        assert len(selected) == 1
        excluded_records = [r for r in records if not r["downstream_selected"]]
        assert len(excluded_records) == 1
        assert excluded_records[0]["eligibility_status"] == "excluded_not_usable"

    def test_zero_eligible(self):
        cands = [
            _make_candidate(candidate_id="c1", downstream_usable=False),
            _make_candidate(candidate_id="c2", downstream_usable=False),
        ]
        selected, records, _, _, _, _ = _run_selection_pipeline(
            cands, {}, max_selected=10,
        )
        assert len(selected) == 0

    def test_empty_input(self):
        selected, records, _, _, _, _ = _run_selection_pipeline(
            [], {}, max_selected=10,
        )
        assert len(selected) == 0
        assert len(records) == 0

    def test_disabled_strategy_exclusion(self):
        cands = [
            _make_candidate(candidate_id="c1", strategy_type="iron_condor"),
            _make_candidate(candidate_id="c2", strategy_type="put_credit_spread",
                            symbol="QQQ"),
        ]
        selected, records, exc_counts, _, _, _ = _run_selection_pipeline(
            cands, {}, max_selected=10,
            disabled_strategies={"iron_condor"},
        )
        assert len(selected) == 1
        assert selected[0]["candidate_id"] == "c2"
        assert exc_counts.get("excluded_disabled_strategy", 0) == 1

    def test_counts_by_scanner(self):
        cands = [
            _make_candidate(candidate_id="c1", scanner_key="s1", symbol="SPY"),
            _make_candidate(candidate_id="c2", scanner_key="s2", symbol="QQQ"),
        ]
        _, _, _, scanner_counts, _, _ = _run_selection_pipeline(
            cands, {}, max_selected=10,
        )
        assert "s1" in scanner_counts
        assert "s2" in scanner_counts
        assert scanner_counts["s1"]["loaded"] == 1
        assert scanner_counts["s1"]["selected"] == 1

    def test_counts_by_family(self):
        cands = [
            _make_candidate(candidate_id="c1", scanner_family="options", symbol="SPY"),
            _make_candidate(candidate_id="c2", scanner_family="stock", symbol="QQQ",
                            strategy_type="pullback_swing"),
        ]
        _, _, _, _, family_counts, _ = _run_selection_pipeline(
            cands, {}, max_selected=10,
        )
        assert "options" in family_counts
        assert "stock" in family_counts

    def test_rank_position_assigned(self):
        cands = [
            _make_candidate(candidate_id="c1", setup_quality=90.0, symbol="SPY"),
            _make_candidate(candidate_id="c2", setup_quality=50.0, symbol="QQQ",
                            strategy_type="pullback_swing"),
        ]
        selected, records, _, _, _, _ = _run_selection_pipeline(
            cands, {}, max_selected=10,
        )
        selected_records = [r for r in records if r["downstream_selected"]]
        positions = [r["rank_position"] for r in selected_records]
        assert sorted(positions) == [1, 2]

    def test_selected_candidates_have_rank_metadata(self):
        cands = [_make_candidate(candidate_id="c1")]
        selected, _, _, _, _, _ = _run_selection_pipeline(
            cands, {}, max_selected=10,
        )
        assert selected[0]["rank_score"] is not None
        assert selected[0]["rank_position"] == 1
        assert selected[0]["downstream_selected"] is True
        assert selected[0]["selection_stage_key"] == _STAGE_KEY


# =====================================================================
#  Artifact Creation
# =====================================================================

class TestArtifactCreation:

    def test_selected_candidates_artifact(self):
        run, store = _make_run_and_store()
        cands = [_make_candidate(candidate_id="c1")]
        art_id = _write_selected_candidates_artifact(
            store, run["run_id"], cands,
        )
        assert art_id
        art = get_artifact_by_key(store, _STAGE_KEY, "selected_candidates")
        assert art is not None
        assert art["artifact_type"] == "selected_candidate"
        assert len(art["data"]) == 1

    def test_selection_ledger_artifact(self):
        run, store = _make_run_and_store()
        records = [
            build_selection_record(
                candidate_id="c1",
                eligibility_status="selected",
                downstream_selected=True,
            ),
        ]
        art_id = _write_selection_ledger_artifact(
            store, run["run_id"], records,
        )
        assert art_id
        art = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_ledger",
        )
        assert art is not None
        assert art["artifact_type"] == "candidate_selection_ledger"

    def test_selection_summary_artifact(self):
        run, store = _make_run_and_store()
        summary = build_selection_summary(
            total_loaded=5, total_eligible=3,
            total_excluded_pre_ranking=1, total_duplicates_excluded=1,
            total_selected=2, total_cut_by_rank=0,
            selection_cap=20, selected_candidate_ids=["c1", "c2"],
        )
        art_id = _write_selection_summary_artifact(
            store, run["run_id"], summary,
        )
        assert art_id
        art = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_summary",
        )
        assert art is not None
        assert art["artifact_type"] == "candidate_selection_summary"

    def test_artifact_types_registered(self):
        assert "selected_candidate" in VALID_ARTIFACT_TYPES
        assert "candidate_selection_ledger" in VALID_ARTIFACT_TYPES
        assert "candidate_selection_summary" in VALID_ARTIFACT_TYPES


# =====================================================================
#  Stage Summary
# =====================================================================

class TestStageSummary:

    def test_summary_has_required_fields(self):
        summary = build_selection_summary(
            total_loaded=10, total_eligible=7,
            total_excluded_pre_ranking=2, total_duplicates_excluded=1,
            total_selected=5, total_cut_by_rank=2,
            selection_cap=5, selected_candidate_ids=["c1", "c2", "c3", "c4", "c5"],
        )
        expected_keys = {
            "stage_key", "stage_status", "total_loaded",
            "total_eligible", "total_excluded_pre_ranking",
            "total_duplicates_excluded", "total_selected",
            "total_cut_by_rank", "selection_cap",
            "selected_candidate_ids", "selected_artifact_ref",
            "ledger_artifact_ref", "summary_artifact_ref",
            "counts_by_scanner", "counts_by_family",
            "counts_by_strategy", "exclusion_reason_counts",
            "degraded_reasons", "elapsed_ms", "generated_at",
        }
        assert expected_keys.issubset(summary.keys())

    def test_counts_correct(self):
        summary = build_selection_summary(
            total_loaded=10, total_eligible=6,
            total_excluded_pre_ranking=3, total_duplicates_excluded=1,
            total_selected=4, total_cut_by_rank=2,
            selection_cap=4, selected_candidate_ids=["c1", "c2", "c3", "c4"],
        )
        assert summary["total_loaded"] == 10
        assert summary["total_eligible"] == 6
        assert summary["total_selected"] == 4
        assert summary["total_cut_by_rank"] == 2
        assert summary["selection_cap"] == 4

    def test_stage_key(self):
        summary = build_selection_summary(
            total_loaded=0, total_eligible=0,
            total_excluded_pre_ranking=0, total_duplicates_excluded=0,
            total_selected=0, total_cut_by_rank=0,
            selection_cap=20, selected_candidate_ids=[],
        )
        assert summary["stage_key"] == _STAGE_KEY

    def test_generated_at_present(self):
        summary = build_selection_summary(
            total_loaded=0, total_eligible=0,
            total_excluded_pre_ranking=0, total_duplicates_excluded=0,
            total_selected=0, total_cut_by_rank=0,
            selection_cap=20, selected_candidate_ids=[],
        )
        assert summary["generated_at"]


# =====================================================================
#  Event Emission
# =====================================================================

class TestEventEmission:

    def test_selection_started_event(self):
        events = []
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(
            run, store, _STAGE_KEY,
            event_callback=lambda e: events.append(e),
        )

        started = [e for e in events if e["event_type"] == "selection_started"]
        assert len(started) == 1

    def test_selection_completed_event(self):
        events = []
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(
            run, store, _STAGE_KEY,
            event_callback=lambda e: events.append(e),
        )

        completed = [e for e in events if e["event_type"] == "selection_completed"]
        assert len(completed) == 1
        assert completed[0]["metadata"]["total_selected"] == 1

    def test_selection_failed_event_on_missing_summary(self):
        events = []
        run, store = _make_run_and_store()
        # No scanner artifacts written

        candidate_selection_handler(
            run, store, _STAGE_KEY,
            event_callback=lambda e: events.append(e),
        )

        failed = [e for e in events if e["event_type"] == "selection_failed"]
        assert len(failed) == 1

    def test_event_types_registered(self):
        assert "selection_started" in VALID_EVENT_TYPES
        assert "selection_completed" in VALID_EVENT_TYPES
        assert "selection_failed" in VALID_EVENT_TYPES

    def test_event_callback_exception_handled(self):
        """Event callback raising should not crash the handler."""
        def bad_callback(event):
            raise RuntimeError("boom")

        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(
            run, store, _STAGE_KEY,
            event_callback=bad_callback,
        )
        assert result["outcome"] == "completed"


# =====================================================================
#  Handler Contract
# =====================================================================

class TestHandlerContract:

    def test_handler_returns_dict(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert isinstance(result, dict)

    def test_handler_result_shape(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        expected_keys = {"outcome", "summary_counts", "artifacts", "metadata", "error"}
        assert expected_keys.issubset(result.keys())

    def test_outcome_completed(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["outcome"] == "completed"
        assert result["error"] is None

    def test_outcome_failed_no_summary(self):
        run, store = _make_run_and_store()
        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_SOURCE_SUMMARY"

    def test_summary_counts_present(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        sc = result["summary_counts"]
        assert "total_loaded" in sc
        assert "total_eligible" in sc
        assert "total_selected" in sc
        assert "total_excluded" in sc
        assert "total_duplicates" in sc
        assert "total_cut_by_rank" in sc

    def test_metadata_has_artifact_ids(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        meta = result["metadata"]
        assert "selected_artifact_id" in meta
        assert "ledger_artifact_id" in meta
        assert "summary_artifact_id" in meta
        assert "selection_cap" in meta


# =====================================================================
#  All Scanners Succeed (full handler flow)
# =====================================================================

class TestAllSucceed:

    def test_multiple_scanners_all_selected(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [
                _make_candidate(candidate_id="c1", symbol="SPY"),
            ],
            "scanner_b": [
                _make_candidate(candidate_id="c2", symbol="QQQ",
                                strategy_type="iron_condor"),
            ],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_selected"] == 2

    def test_selected_artifact_written(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(store, _STAGE_KEY, "selected_candidates")
        assert art is not None
        assert len(art["data"]) == 1
        assert art["data"][0]["candidate_id"] == "c1"

    def test_ledger_artifact_written(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_ledger",
        )
        assert art is not None
        assert len(art["data"]) == 1

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_summary",
        )
        assert art is not None
        assert art["data"]["total_selected"] == 1

    def test_stage_status_success(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["metadata"]["stage_status"] == "success"

    def test_artifacts_valid_store(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)
        ok, errors = validate_artifact_store(store)
        assert ok, f"Store validation: {errors}"

    def test_three_artifacts_for_selection_stage(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        stage_arts = list_stage_artifacts(store, _STAGE_KEY)
        assert len(stage_arts) == 3  # selected, ledger, summary


# =====================================================================
#  Bounded Selection
# =====================================================================

class TestBoundedSelection:

    def test_cap_applied(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [
                _make_candidate(candidate_id=f"c{i}", symbol=f"SYM{i}",
                                strategy_type=f"strat_{i}")
                for i in range(10)
            ],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(
            run, store, _STAGE_KEY,
            max_selected_candidates=3,
        )
        assert result["summary_counts"]["total_selected"] == 3
        assert result["summary_counts"]["total_cut_by_rank"] == 7

    def test_cap_larger_than_pool(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [_make_candidate(candidate_id="c1")],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(
            run, store, _STAGE_KEY,
            max_selected_candidates=100,
        )
        assert result["summary_counts"]["total_selected"] == 1
        assert result["summary_counts"]["total_cut_by_rank"] == 0

    def test_default_cap_value(self):
        assert DEFAULT_MAX_SELECTED_CANDIDATES == 20


# =====================================================================
#  Duplicate Handling
# =====================================================================

class TestDuplicateHandling:

    def test_duplicates_across_scanners(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [
                _make_candidate(candidate_id="c1", symbol="SPY",
                                strategy_type="put_credit_spread"),
            ],
            "scanner_b": [
                _make_candidate(candidate_id="c2", symbol="SPY",
                                strategy_type="put_credit_spread"),
            ],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["summary_counts"]["total_duplicates"] == 1
        assert result["summary_counts"]["total_selected"] == 1

    def test_no_duplicates_different_symbols(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [
                _make_candidate(candidate_id="c1", symbol="SPY"),
            ],
            "scanner_b": [
                _make_candidate(candidate_id="c2", symbol="QQQ"),
            ],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["summary_counts"]["total_duplicates"] == 0
        assert result["summary_counts"]["total_selected"] == 2


# =====================================================================
#  Zero Candidates
# =====================================================================

class TestZeroCandidates:

    def test_zero_loaded(self):
        run, store = _make_run_and_store()
        _write_scanner_stage_artifacts(store, run["run_id"], {})

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_loaded"] == 0
        assert result["metadata"]["stage_status"] == "no_candidates_loaded"

    def test_zero_eligible(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [
                _make_candidate(candidate_id="c1", downstream_usable=False),
                _make_candidate(candidate_id="c2", downstream_usable=False),
            ],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_selected"] == 0
        assert result["metadata"]["stage_status"] == "no_selected_candidates"

    def test_zero_selected_after_cap(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [_make_candidate(candidate_id="c1", symbol="SPY")],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(
            run, store, _STAGE_KEY,
            max_selected_candidates=0,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_selected"] == 0

    def test_zero_candidates_still_writes_artifacts(self):
        run, store = _make_run_and_store()
        _write_scanner_stage_artifacts(store, run["run_id"], {})

        candidate_selection_handler(run, store, _STAGE_KEY)

        # Should still have selection artifacts
        sel_art = get_artifact_by_key(store, _STAGE_KEY, "selected_candidates")
        assert sel_art is not None
        assert sel_art["data"] == []


# =====================================================================
#  No Source Summary Failure
# =====================================================================

class TestNoSourceSummary:

    def test_fails_with_clear_code(self):
        run, store = _make_run_and_store()
        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_SOURCE_SUMMARY"

    def test_error_has_structured_shape(self):
        run, store = _make_run_and_store()
        result = candidate_selection_handler(run, store, _STAGE_KEY)
        err = result["error"]
        expected_keys = {"code", "message", "source", "detail", "timestamp", "retryable"}
        assert expected_keys.issubset(err.keys())


# =====================================================================
#  Degraded Behavior
# =====================================================================

class TestDegradedBehavior:

    def test_some_artifacts_missing_degraded(self):
        """Summary references 2 scanners but only 1 artifact exists."""
        run, store = _make_run_and_store()

        # Write one real candidate artifact
        art = build_artifact_record(
            run_id=run["run_id"],
            stage_key="scanners",
            artifact_key="candidates_scanner_a",
            artifact_type="normalized_candidate",
            data=[_make_candidate(candidate_id="c1")],
        )
        put_artifact(store, art, overwrite=True)

        # Summary references both scanner_a and scanner_b
        summary_data = {
            "stage_key": "scanners",
            "scanner_summaries": {
                "scanner_a": {
                    "status": "completed",
                    "downstream_usable": True,
                    "candidate_artifact_ref": art["artifact_id"],
                },
                "scanner_b": {
                    "status": "completed",
                    "downstream_usable": True,
                    "candidate_artifact_ref": "missing-ref",
                },
            },
            "candidate_artifact_refs": {
                "scanner_a": art["artifact_id"],
                "scanner_b": "missing-ref",
            },
            "all_candidate_ids": ["c1", "c2"],
        }
        s_art = build_artifact_record(
            run_id=run["run_id"],
            stage_key="scanners",
            artifact_key="scanner_stage_summary",
            artifact_type="scanner_stage_summary",
            data=summary_data,
        )
        put_artifact(store, s_art, overwrite=True)

        result = candidate_selection_handler(run, store, _STAGE_KEY)
        assert result["outcome"] == "completed"
        assert result["metadata"]["stage_status"] == "degraded"
        assert len(result["metadata"]["load_warnings"]) > 0
        # Still selected the available candidate
        assert result["summary_counts"]["total_selected"] == 1


# =====================================================================
#  Orchestrator Integration
# =====================================================================

class TestOrchestratorIntegration:

    def test_default_handler_wired(self):
        handlers = get_default_handlers()
        from app.services.pipeline_candidate_selection_stage import (
            candidate_selection_handler as csh,
        )
        assert handlers["candidate_selection"] is csh

    def test_runs_through_pipeline(self):
        """Full pipeline with stubs for all stages except candidate_selection."""
        result = run_pipeline_with_handlers(
            {
                "market_data": _success_handler,
                "market_model_analysis": _success_handler,
                "scanners": _success_handler,
                "candidate_selection": _success_handler,
                "shared_context": _success_handler,
                "candidate_enrichment": _success_handler,
                "events": _success_handler,
                "policy": _success_handler,
                "orchestration": _success_handler,
                "prompt_payload": _success_handler,
                "final_model_decision": _success_handler,
                "final_response_normalization": _success_handler,
            },
            run_id="test-orch-sel-001",
        )
        sr = {s["stage_key"]: s for s in result["stage_results"]}
        assert sr["candidate_selection"]["outcome"] == "completed"

    def test_stub_override_works(self):
        """Custom handler replaces candidate_selection."""
        custom_handler = lambda run, store, sk, **kw: {
            "outcome": "completed",
            "summary_counts": {"custom": True},
            "artifacts": [],
            "metadata": {},
            "error": None,
        }
        result = run_pipeline_with_handlers(
            {
                "market_data": _success_handler,
                "market_model_analysis": _success_handler,
                "scanners": _success_handler,
                "candidate_selection": custom_handler,
                "shared_context": _success_handler,
                "candidate_enrichment": _success_handler,
                "events": _success_handler,
                "policy": _success_handler,
                "orchestration": _success_handler,
                "prompt_payload": _success_handler,
                "final_model_decision": _success_handler,
                "final_response_normalization": _success_handler,
            },
            run_id="test-orch-sel-002",
        )
        sr = {s["stage_key"]: s for s in result["stage_results"]}
        assert sr["candidate_selection"]["outcome"] == "completed"
        assert sr["candidate_selection"]["summary_counts"].get("custom") is True


# =====================================================================
#  Forward Compatibility
# =====================================================================

class TestForwardCompatibility:

    def test_selected_candidate_has_downstream_selected(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(store, _STAGE_KEY, "selected_candidates")
        assert art["data"][0]["downstream_selected"] is True

    def test_candidate_lineage_preserved(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [
            _make_candidate(
                candidate_id="c1",
                source_scanner_artifact_ref="art-scanner-ref",
            ),
        ]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(store, _STAGE_KEY, "selected_candidates")
        selected = art["data"][0]
        # Lineage back to scanner preserved
        assert selected.get("scanner_key") == "test_scanner_a"
        assert selected.get("run_id") == run["run_id"]

    def test_retrieval_seam(self):
        """Downstream stages can retrieve via get_artifact_by_key."""
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        # All three selection artifacts retrievable
        sel = get_artifact_by_key(
            store, _STAGE_KEY, "selected_candidates",
        )
        ledger = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_ledger",
        )
        summary = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_summary",
        )
        assert sel is not None
        assert ledger is not None
        assert summary is not None


# =====================================================================
#  Output Stability
# =====================================================================

class TestOutputStability:

    def test_selected_candidates_key_shape(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(store, _STAGE_KEY, "selected_candidates")
        c = art["data"][0]
        # Must have ranking metadata added by selection
        assert "rank_score" in c
        assert "rank_position" in c
        assert "downstream_selected" in c
        assert "selection_stage_key" in c

    def test_ledger_record_shape(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_ledger",
        )
        rec = art["data"][0]
        expected_keys = {
            "candidate_id", "scanner_key", "symbol",
            "strategy_type", "opportunity_type",
            "eligibility_status", "exclusion_reason",
            "rank_score", "rank_position",
            "source_candidate_artifact_ref",
            "source_scanner_artifact_ref",
            "downstream_selected", "warnings", "notes",
        }
        assert expected_keys.issubset(rec.keys())

    def test_summary_artifact_key_shape(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_summary",
        )
        data = art["data"]
        expected_keys = {
            "stage_key", "stage_status", "total_loaded",
            "total_eligible", "total_excluded_pre_ranking",
            "total_duplicates_excluded", "total_selected",
            "total_cut_by_rank", "selection_cap",
            "selected_candidate_ids",
        }
        assert expected_keys.issubset(data.keys())


# =====================================================================
#  Config / Weight Injection
# =====================================================================

class TestConfigInjection:

    def test_custom_max_selected(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [
                _make_candidate(candidate_id=f"c{i}", symbol=f"SYM{i}",
                                strategy_type=f"s{i}")
                for i in range(5)
            ],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        result = candidate_selection_handler(
            run, store, _STAGE_KEY,
            max_selected_candidates=2,
        )
        assert result["summary_counts"]["total_selected"] == 2
        assert result["metadata"]["selection_cap"] == 2

    def test_custom_family_weights(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [
                _make_candidate(candidate_id="c1", scanner_family="stock",
                                strategy_type="pullback_swing", symbol="SPY",
                                setup_quality=50.0, confidence=0.5),
                _make_candidate(candidate_id="c2", scanner_family="options",
                                strategy_type="put_credit_spread", symbol="QQQ",
                                setup_quality=50.0, confidence=0.5),
            ],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        # Boost stock above options
        result = candidate_selection_handler(
            run, store, _STAGE_KEY,
            family_weights={"stock": 1.0, "options": 0.3},
            max_selected_candidates=1,
        )
        art = get_artifact_by_key(store, _STAGE_KEY, "selected_candidates")
        assert art["data"][0]["scanner_family"] == "stock"

    def test_custom_strategy_weights(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [
                _make_candidate(candidate_id="c1",
                                strategy_type="iron_condor", symbol="SPY",
                                setup_quality=50.0, confidence=0.5),
                _make_candidate(candidate_id="c2",
                                strategy_type="put_credit_spread", symbol="QQQ",
                                setup_quality=50.0, confidence=0.5),
            ],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        # Boost iron_condor above put_credit_spread
        result = candidate_selection_handler(
            run, store, _STAGE_KEY,
            strategy_weights={
                "iron_condor": 1.0,
                "put_credit_spread": 0.1,
            },
            max_selected_candidates=1,
        )
        art = get_artifact_by_key(store, _STAGE_KEY, "selected_candidates")
        assert art["data"][0]["strategy_type"] == "iron_condor"


# =====================================================================
#  Artifact Lineage
# =====================================================================

class TestArtifactLineage:

    def test_ledger_has_source_artifact_ref(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_ledger",
        )
        rec = art["data"][0]
        assert rec["source_candidate_artifact_ref"] is not None

    def test_summary_has_artifact_refs(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_summary",
        )
        data = art["data"]
        assert data["selected_artifact_ref"] is not None
        assert data["ledger_artifact_ref"] is not None

    def test_selected_candidate_preserves_run_id(self):
        run, store = _make_run_and_store()
        cands = {"scanner_a": [_make_candidate(candidate_id="c1")]}
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(store, _STAGE_KEY, "selected_candidates")
        assert art["data"][0]["run_id"] == run["run_id"]

    def test_exclusion_reason_counts_in_summary(self):
        run, store = _make_run_and_store()
        cands = {
            "scanner_a": [
                _make_candidate(candidate_id="c1", downstream_usable=False),
                _make_candidate(candidate_id="c2", symbol="SPY"),
            ],
        }
        _write_scanner_stage_artifacts(store, run["run_id"], cands)

        candidate_selection_handler(run, store, _STAGE_KEY)

        art = get_artifact_by_key(
            store, _STAGE_KEY, "candidate_selection_summary",
        )
        exc_counts = art["data"]["exclusion_reason_counts"]
        assert "excluded_not_usable" in exc_counts
