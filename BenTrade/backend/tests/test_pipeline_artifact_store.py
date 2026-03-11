"""Tests for Pipeline Artifact Store v1.0.

Coverage targets:
─── Store initialization
    - create_artifact_store produces valid shape
    - required keys present
    - version string locked
    - empty counts
    - run_id passthrough
    - metadata passthrough
─── Artifact record creation
    - build_artifact_record required keys
    - artifact_type validation warning (no crash)
    - custom artifact_id honored
    - auto-generated artifact_id
    - candidate_id optional/null
    - summary defaults empty
    - metadata defaults empty
─── Put / get behavior
    - put stores and retrieves by ID
    - put stores and retrieves by key
    - put updates indices
    - put increments counts
    - collision without overwrite raises ValueError
    - collision with overwrite supersedes old
    - superseded record status updated
    - superseded record still retrievable by ID
    - get_artifact returns None for missing
    - get_artifact_by_key returns None for missing
─── Listing by run / stage / type / candidate
    - list_artifacts unfiltered returns all
    - list_artifacts filtered by type
    - list_artifacts filtered by status
    - list_artifacts filtered by stage
    - list_artifacts filtered by candidate
    - list_artifacts combined filters
    - list_stage_artifacts returns stage artifacts
    - list_stage_artifacts active_only
    - list_candidate_artifacts returns candidate artifacts
    - list_candidate_artifacts active_only
    - get_latest_by_type returns most recent
    - get_latest_by_type active_only
    - get_latest_by_type returns None if empty
─── Summary / store counts
    - summarize_artifact_store keys
    - summary reflects data
    - module_role is "storage"
    - empty store summary
─── Validation
    - valid store passes
    - valid record passes
    - missing store key detected
    - missing record key detected
    - wrong version rejected
    - invalid record status rejected
    - non-dict store fails
    - non-dict record fails
    - round-trip: built store passes validation
─── Serialization / round-trip
    - JSON-serializable empty store
    - JSON-serializable populated store
    - export_store returns deep copy
    - import_store round-trip
    - import_store rejects invalid
    - record JSON-serializable
─── Representative artifact types
    - market_engine_output artifact
    - scanner_output artifact
    - decision_packet candidate-linked
    - final_decision_response artifact
    - policy_output artifact
─── No orchestrator coupling
    - store has no execution logic
    - store has no stage-transition methods
─── Constants
    - VALID_ARTIFACT_TYPES non-empty
    - VALID_ARTIFACT_STATUSES values
    - _COMPATIBLE_VERSIONS
"""

import json

import pytest

from app.services.pipeline_artifact_store import (
    VALID_ARTIFACT_STATUSES,
    VALID_ARTIFACT_TYPES,
    _ARTIFACT_STORE_VERSION,
    _COMPATIBLE_VERSIONS,
    _REQUIRED_RECORD_KEYS,
    _REQUIRED_STORE_KEYS,
    build_artifact_record,
    create_artifact_store,
    export_store,
    get_artifact,
    get_artifact_by_key,
    get_latest_by_type,
    import_store,
    list_artifacts,
    list_candidate_artifacts,
    list_stage_artifacts,
    put_artifact,
    summarize_artifact_store,
    validate_artifact_record,
    validate_artifact_store,
)


# ── Fixtures ──────────────────────────────────────────────────────────

def _fresh_store(**overrides):
    """Build a fresh artifact store with optional overrides."""
    kwargs = {"run_id": "run-test-001"}
    kwargs.update(overrides)
    return create_artifact_store(**kwargs)


def _market_engine_record(run_id="run-test-001", **overrides):
    """Build a market engine output artifact record."""
    kwargs = {
        "run_id": run_id,
        "stage_key": "market_data",
        "artifact_key": "breadth_engine",
        "artifact_type": "market_engine_output",
        "data": {
            "breadth_label": "positive",
            "breadth_score": 0.72,
            "summary": "Broad participation, healthy internals.",
        },
        "summary": {"label": "positive", "score": 0.72},
    }
    kwargs.update(overrides)
    return build_artifact_record(**kwargs)


def _scanner_record(run_id="run-test-001", **overrides):
    """Build a scanner output artifact record."""
    kwargs = {
        "run_id": run_id,
        "stage_key": "scanners",
        "artifact_key": "put_credit_spread_candidates",
        "artifact_type": "scanner_output",
        "data": {
            "candidates_found": 12,
            "candidates": [{"id": "SPY_pcs_001"}, {"id": "QQQ_pcs_002"}],
        },
        "summary": {"candidates_found": 12},
    }
    kwargs.update(overrides)
    return build_artifact_record(**kwargs)


def _decision_packet_record(
    run_id="run-test-001",
    candidate_id="SPY_pcs_001",
    **overrides,
):
    """Build a candidate-linked decision packet artifact record."""
    kwargs = {
        "run_id": run_id,
        "stage_key": "orchestration",
        "artifact_key": f"candidate_{candidate_id}_decision_packet",
        "artifact_type": "decision_packet",
        "candidate_id": candidate_id,
        "data": {
            "candidate_id": candidate_id,
            "market": {"market_state": "neutral"},
            "policy": {"policy_decision": "allow"},
        },
        "summary": {"candidate_id": candidate_id, "policy_decision": "allow"},
    }
    kwargs.update(overrides)
    return build_artifact_record(**kwargs)


def _final_response_record(
    run_id="run-test-001",
    candidate_id="SPY_pcs_001",
    **overrides,
):
    """Build a final decision response artifact record."""
    kwargs = {
        "run_id": run_id,
        "stage_key": "final_response_normalization",
        "artifact_key": f"candidate_{candidate_id}_final_response",
        "artifact_type": "final_decision_response",
        "candidate_id": candidate_id,
        "data": {
            "decision": "approve",
            "conviction": "high",
            "summary": "Trade approved with high conviction.",
        },
        "summary": {"decision": "approve", "conviction": "high"},
    }
    kwargs.update(overrides)
    return build_artifact_record(**kwargs)


# =====================================================================
#  Store initialization
# =====================================================================

class TestStoreInitialization:

    def test_required_keys_present(self):
        store = _fresh_store()
        for key in _REQUIRED_STORE_KEYS:
            assert key in store, f"missing: {key}"

    def test_version_string(self):
        store = _fresh_store()
        assert store["artifact_store_version"] == _ARTIFACT_STORE_VERSION
        assert store["artifact_store_version"] == "1.0"

    def test_run_id_passthrough(self):
        store = _fresh_store(run_id="run-abc-123")
        assert store["run_id"] == "run-abc-123"

    def test_metadata_passthrough(self):
        store = _fresh_store(metadata={"replay": True})
        assert store["metadata"]["replay"] is True

    def test_metadata_defaults_empty(self):
        store = _fresh_store()
        assert store["metadata"] == {}

    def test_empty_counts(self):
        store = _fresh_store()
        assert store["counts"]["total"] == 0
        assert store["counts"]["active"] == 0
        assert store["counts"]["by_stage"] == {}
        assert store["counts"]["by_type"] == {}

    def test_created_at_present(self):
        store = _fresh_store()
        assert isinstance(store["created_at"], str)
        assert len(store["created_at"]) > 0

    def test_empty_indices(self):
        store = _fresh_store()
        assert store["artifacts"] == {}
        assert store["artifact_index"] == {}
        assert store["stage_index"] == {}
        assert store["type_index"] == {}
        assert store["candidate_index"] == {}


# =====================================================================
#  Artifact record creation
# =====================================================================

class TestBuildArtifactRecord:

    def test_required_keys(self):
        rec = _market_engine_record()
        for key in _REQUIRED_RECORD_KEYS:
            assert key in rec, f"missing: {key}"

    def test_custom_artifact_id(self):
        rec = _market_engine_record(artifact_id="custom-art-001")
        assert rec["artifact_id"] == "custom-art-001"

    def test_auto_generated_artifact_id(self):
        rec = _market_engine_record()
        assert rec["artifact_id"].startswith("art-")
        assert len(rec["artifact_id"]) > 4

    def test_candidate_id_none(self):
        rec = _market_engine_record()
        assert rec["candidate_id"] is None

    def test_candidate_id_passthrough(self):
        rec = _decision_packet_record(candidate_id="SPY_001")
        assert rec["candidate_id"] == "SPY_001"

    def test_summary_defaults_empty(self):
        rec = build_artifact_record(
            run_id="r1", stage_key="s1",
            artifact_key="k1", artifact_type="market_engine_output",
        )
        assert rec["summary"] == {}

    def test_metadata_defaults_empty(self):
        rec = _market_engine_record()
        assert rec["metadata"] == {}

    def test_status_active(self):
        rec = _market_engine_record()
        assert rec["status"] == "active"

    def test_timestamp_present(self):
        rec = _market_engine_record()
        assert isinstance(rec["created_at"], str)
        assert isinstance(rec["updated_at"], str)

    def test_unknown_type_no_crash(self):
        """Unknown artifact_type logs warning but doesn't raise."""
        rec = build_artifact_record(
            run_id="r1", stage_key="s1",
            artifact_key="k1", artifact_type="invented_type",
        )
        assert rec["artifact_type"] == "invented_type"

    def test_data_passthrough(self):
        data = {"key": "value", "nested": {"a": 1}}
        rec = build_artifact_record(
            run_id="r1", stage_key="s1",
            artifact_key="k1", artifact_type="market_engine_output",
            data=data,
        )
        assert rec["data"] == data


# =====================================================================
#  Put / get behavior
# =====================================================================

class TestPutGet:

    def test_put_and_get_by_id(self):
        store = _fresh_store()
        rec = _market_engine_record(artifact_id="art-001")
        put_artifact(store, rec)
        result = get_artifact(store, "art-001")
        assert result is not None
        assert result["artifact_id"] == "art-001"

    def test_put_and_get_by_key(self):
        store = _fresh_store()
        rec = _market_engine_record()
        put_artifact(store, rec)
        result = get_artifact_by_key(store, "market_data", "breadth_engine")
        assert result is not None
        assert result["artifact_key"] == "breadth_engine"

    def test_put_updates_stage_index(self):
        store = _fresh_store()
        rec = _market_engine_record()
        put_artifact(store, rec)
        assert "market_data" in store["stage_index"]
        assert len(store["stage_index"]["market_data"]) == 1

    def test_put_updates_type_index(self):
        store = _fresh_store()
        rec = _market_engine_record()
        put_artifact(store, rec)
        assert "market_engine_output" in store["type_index"]

    def test_put_updates_candidate_index(self):
        store = _fresh_store()
        rec = _decision_packet_record(candidate_id="SPY_001")
        put_artifact(store, rec)
        assert "SPY_001" in store["candidate_index"]

    def test_put_increments_counts(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record())
        assert store["counts"]["total"] == 1
        assert store["counts"]["active"] == 1
        assert store["counts"]["by_stage"]["market_data"] == 1
        assert store["counts"]["by_type"]["market_engine_output"] == 1

    def test_collision_without_overwrite_raises(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        with pytest.raises(ValueError, match="collision"):
            put_artifact(store, _market_engine_record(artifact_id="art-002"))

    def test_collision_with_overwrite_supersedes(self):
        store = _fresh_store()
        rec1 = _market_engine_record(artifact_id="art-001")
        put_artifact(store, rec1)
        rec2 = _market_engine_record(artifact_id="art-002",
                                      data={"updated": True})
        put_artifact(store, rec2, overwrite=True)
        # Old record superseded
        old = get_artifact(store, "art-001")
        assert old["status"] == "superseded"
        # New record is active
        new = get_artifact_by_key(store, "market_data", "breadth_engine")
        assert new["artifact_id"] == "art-002"
        assert new["status"] == "active"

    def test_superseded_counts(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        put_artifact(store, _market_engine_record(artifact_id="art-002"),
                     overwrite=True)
        assert store["counts"]["active"] == 1
        assert store["counts"]["superseded"] == 1
        assert store["counts"]["total"] == 2

    def test_get_artifact_returns_none_for_missing(self):
        store = _fresh_store()
        assert get_artifact(store, "nonexistent") is None

    def test_get_artifact_by_key_returns_none_for_missing(self):
        store = _fresh_store()
        assert get_artifact_by_key(store, "x", "y") is None

    def test_put_no_candidate_index_for_none(self):
        store = _fresh_store()
        rec = _market_engine_record()  # candidate_id=None
        put_artifact(store, rec)
        assert store["candidate_index"] == {}

    def test_updated_at_changes(self):
        store = _fresh_store()
        original = store["updated_at"]
        put_artifact(store, _market_engine_record())
        assert store["updated_at"] >= original


# =====================================================================
#  Listing by run / stage / type / candidate
# =====================================================================

class TestListArtifacts:

    def _populated_store(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-m1"))
        put_artifact(store, _scanner_record(artifact_id="art-s1"))
        put_artifact(store, _decision_packet_record(
            artifact_id="art-dp1", candidate_id="SPY_001"))
        put_artifact(store, _final_response_record(
            artifact_id="art-fr1", candidate_id="SPY_001"))
        return store

    def test_list_all(self):
        store = self._populated_store()
        all_arts = list_artifacts(store)
        assert len(all_arts) == 4

    def test_list_by_type(self):
        store = self._populated_store()
        results = list_artifacts(store, artifact_type="scanner_output")
        assert len(results) == 1
        assert results[0]["artifact_type"] == "scanner_output"

    def test_list_by_status(self):
        store = self._populated_store()
        results = list_artifacts(store, status="active")
        assert len(results) == 4

    def test_list_by_stage(self):
        store = self._populated_store()
        results = list_artifacts(store, stage_key="orchestration")
        assert len(results) == 1

    def test_list_by_candidate(self):
        store = self._populated_store()
        results = list_artifacts(store, candidate_id="SPY_001")
        assert len(results) == 2  # decision_packet + final_response

    def test_list_combined_filters(self):
        store = self._populated_store()
        results = list_artifacts(
            store, candidate_id="SPY_001",
            artifact_type="decision_packet",
        )
        assert len(results) == 1

    def test_list_stage_artifacts(self):
        store = self._populated_store()
        results = list_stage_artifacts(store, "market_data")
        assert len(results) == 1
        assert results[0]["stage_key"] == "market_data"

    def test_list_stage_artifacts_active_only(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        put_artifact(store, _market_engine_record(artifact_id="art-002"),
                     overwrite=True)
        all_arts = list_stage_artifacts(store, "market_data")
        assert len(all_arts) == 2  # includes superseded
        active = list_stage_artifacts(store, "market_data", active_only=True)
        assert len(active) == 1

    def test_list_candidate_artifacts(self):
        store = self._populated_store()
        results = list_candidate_artifacts(store, "SPY_001")
        assert len(results) == 2

    def test_list_candidate_artifacts_active_only(self):
        store = self._populated_store()
        results = list_candidate_artifacts(store, "SPY_001", active_only=True)
        assert len(results) == 2
        assert all(r["status"] == "active" for r in results)

    def test_list_candidate_artifacts_empty(self):
        store = self._populated_store()
        results = list_candidate_artifacts(store, "NONEXISTENT")
        assert results == []

    def test_list_stage_artifacts_empty(self):
        store = _fresh_store()
        results = list_stage_artifacts(store, "scanners")
        assert results == []

    def test_get_latest_by_type(self):
        store = _fresh_store()
        rec1 = build_artifact_record(
            run_id="r1", stage_key="market_data",
            artifact_key="breadth", artifact_type="market_engine_output",
            artifact_id="art-001", data={"v": 1},
        )
        rec2 = build_artifact_record(
            run_id="r1", stage_key="market_data",
            artifact_key="volatility", artifact_type="market_engine_output",
            artifact_id="art-002", data={"v": 2},
        )
        put_artifact(store, rec1)
        put_artifact(store, rec2)
        latest = get_latest_by_type(store, "market_engine_output")
        assert latest is not None
        assert latest["artifact_id"] == "art-002"

    def test_get_latest_by_type_none(self):
        store = _fresh_store()
        assert get_latest_by_type(store, "scanner_output") is None

    def test_get_latest_by_type_active_only(self):
        store = _fresh_store()
        rec = build_artifact_record(
            run_id="r1", stage_key="s1",
            artifact_key="k1", artifact_type="market_engine_output",
            artifact_id="art-001",
        )
        put_artifact(store, rec)
        # Manually mark as superseded
        store["artifacts"]["art-001"]["status"] = "superseded"
        assert get_latest_by_type(
            store, "market_engine_output", active_only=True
        ) is None
        assert get_latest_by_type(
            store, "market_engine_output", active_only=False
        ) is not None


# =====================================================================
#  Summary / store counts
# =====================================================================

class TestSummarize:

    def test_summary_keys(self):
        store = _fresh_store()
        s = summarize_artifact_store(store)
        expected = {
            "run_id", "artifact_store_version", "total_artifacts",
            "active_artifacts", "stages_with_artifacts",
            "artifact_types_present", "candidates_tracked",
            "counts", "module_role",
        }
        assert expected.issubset(s.keys())

    def test_module_role(self):
        store = _fresh_store()
        s = summarize_artifact_store(store)
        assert s["module_role"] == "storage"

    def test_empty_summary(self):
        store = _fresh_store()
        s = summarize_artifact_store(store)
        assert s["total_artifacts"] == 0
        assert s["active_artifacts"] == 0
        assert s["stages_with_artifacts"] == []
        assert s["candidates_tracked"] == 0

    def test_populated_summary(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        put_artifact(store, _scanner_record(artifact_id="art-002"))
        put_artifact(store, _decision_packet_record(
            artifact_id="art-003", candidate_id="SPY_001"))
        s = summarize_artifact_store(store)
        assert s["total_artifacts"] == 3
        assert s["active_artifacts"] == 3
        assert "market_data" in s["stages_with_artifacts"]
        assert "scanners" in s["stages_with_artifacts"]
        assert "market_engine_output" in s["artifact_types_present"]
        assert s["candidates_tracked"] == 1


# =====================================================================
#  Validation
# =====================================================================

class TestValidation:

    def test_valid_store_passes(self):
        store = _fresh_store()
        ok, errors = validate_artifact_store(store)
        assert ok, f"Errors: {errors}"

    def test_valid_record_passes(self):
        rec = _market_engine_record()
        ok, errors = validate_artifact_record(rec)
        assert ok, f"Errors: {errors}"

    def test_non_dict_store_fails(self):
        ok, errors = validate_artifact_store("not_a_dict")
        assert not ok

    def test_non_dict_record_fails(self):
        ok, errors = validate_artifact_record("not_a_dict")
        assert not ok

    def test_missing_store_key(self):
        store = _fresh_store()
        del store["artifacts"]
        ok, errors = validate_artifact_store(store)
        assert not ok
        assert any("artifacts" in e for e in errors)

    def test_missing_record_key(self):
        rec = _market_engine_record()
        del rec["artifact_type"]
        ok, errors = validate_artifact_record(rec)
        assert not ok
        assert any("artifact_type" in e for e in errors)

    def test_wrong_version(self):
        store = _fresh_store()
        store["artifact_store_version"] = "99.0"
        ok, errors = validate_artifact_store(store)
        assert not ok
        assert any("99.0" in e for e in errors)

    def test_invalid_record_status(self):
        rec = _market_engine_record()
        rec["status"] = "exploded"
        ok, errors = validate_artifact_record(rec)
        assert not ok
        assert any("exploded" in e for e in errors)

    def test_store_validates_nested_records(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        # Corrupt a record
        store["artifacts"]["art-001"]["status"] = "bad"
        ok, errors = validate_artifact_store(store)
        assert not ok
        assert any("art-001" in e for e in errors)

    def test_round_trip_all_cases(self):
        """Every build output must pass validation."""
        # Empty store
        store = _fresh_store()
        ok, errors = validate_artifact_store(store)
        assert ok, f"Empty: {errors}"

        # After put
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        ok, errors = validate_artifact_store(store)
        assert ok, f"After put: {errors}"

        # After overwrite
        put_artifact(store, _market_engine_record(artifact_id="art-002"),
                     overwrite=True)
        ok, errors = validate_artifact_store(store)
        assert ok, f"After overwrite: {errors}"

        # After candidate artifact
        put_artifact(store, _decision_packet_record(artifact_id="art-003"))
        ok, errors = validate_artifact_store(store)
        assert ok, f"After candidate: {errors}"


# =====================================================================
#  Serialization / round-trip
# =====================================================================

class TestSerialization:

    def test_json_serializable_empty(self):
        store = _fresh_store()
        serialized = json.dumps(store)
        assert isinstance(serialized, str)

    def test_json_serializable_populated(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        put_artifact(store, _scanner_record(artifact_id="art-002"))
        put_artifact(store, _decision_packet_record(
            artifact_id="art-003", candidate_id="SPY_001"))
        serialized = json.dumps(store)
        roundtrip = json.loads(serialized)
        ok, errors = validate_artifact_store(roundtrip)
        assert ok, f"Round-trip: {errors}"

    def test_export_store_deep_copy(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        exported = export_store(store)
        # Mutate export — original should be unaffected
        exported["artifacts"]["art-001"]["data"] = {"mutated": True}
        original = get_artifact(store, "art-001")
        assert original["data"] != {"mutated": True}

    def test_import_store_round_trip(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        exported = export_store(store)
        imported = import_store(exported)
        assert imported["run_id"] == store["run_id"]
        assert "art-001" in imported["artifacts"]

    def test_import_store_rejects_invalid(self):
        with pytest.raises(ValueError, match="Cannot import"):
            import_store({"bad": "data"})

    def test_record_json_serializable(self):
        rec = _market_engine_record()
        serialized = json.dumps(rec)
        roundtrip = json.loads(serialized)
        assert roundtrip["artifact_type"] == "market_engine_output"

    def test_import_store_deep_copy(self):
        store = _fresh_store()
        put_artifact(store, _market_engine_record(artifact_id="art-001"))
        exported = export_store(store)
        imported = import_store(exported)
        # Mutate import — exported should be unaffected
        imported["run_id"] = "mutated"
        assert exported["run_id"] == "run-test-001"


# =====================================================================
#  Representative artifact types
# =====================================================================

class TestRepresentativeArtifacts:

    def test_market_engine_output(self):
        store = _fresh_store()
        rec = _market_engine_record()
        put_artifact(store, rec)
        result = get_artifact_by_key(store, "market_data", "breadth_engine")
        assert result["artifact_type"] == "market_engine_output"
        assert result["data"]["breadth_label"] == "positive"

    def test_scanner_output(self):
        store = _fresh_store()
        rec = _scanner_record()
        put_artifact(store, rec)
        result = get_artifact_by_key(
            store, "scanners", "put_credit_spread_candidates"
        )
        assert result["artifact_type"] == "scanner_output"
        assert result["data"]["candidates_found"] == 12

    def test_decision_packet_candidate_linked(self):
        store = _fresh_store()
        rec = _decision_packet_record(candidate_id="SPY_pcs_001")
        put_artifact(store, rec)
        # Retrieve via candidate lineage
        cand_arts = list_candidate_artifacts(store, "SPY_pcs_001")
        assert len(cand_arts) == 1
        assert cand_arts[0]["artifact_type"] == "decision_packet"
        assert cand_arts[0]["candidate_id"] == "SPY_pcs_001"

    def test_final_decision_response(self):
        store = _fresh_store()
        rec = _final_response_record(candidate_id="SPY_pcs_001")
        put_artifact(store, rec)
        result = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_SPY_pcs_001_final_response",
        )
        assert result["artifact_type"] == "final_decision_response"
        assert result["data"]["decision"] == "approve"

    def test_policy_output(self):
        store = _fresh_store()
        rec = build_artifact_record(
            run_id="run-test-001",
            stage_key="policy",
            artifact_key="candidate_SPY_001_policy",
            artifact_type="policy_output",
            candidate_id="SPY_001",
            data={"policy_decision": "allow", "severity": "none"},
            summary={"decision": "allow"},
        )
        put_artifact(store, rec)
        result = get_artifact_by_key(
            store, "policy", "candidate_SPY_001_policy"
        )
        assert result["artifact_type"] == "policy_output"
        assert result["candidate_id"] == "SPY_001"

    def test_multi_candidate_lineage(self):
        """Multiple candidates each get their own artifacts."""
        store = _fresh_store()
        for cid in ("SPY_001", "QQQ_002", "IWM_003"):
            put_artifact(store, _decision_packet_record(
                candidate_id=cid,
                artifact_id=f"art-dp-{cid}",
            ))
            put_artifact(store, _final_response_record(
                candidate_id=cid,
                artifact_id=f"art-fr-{cid}",
            ))
        # Each candidate has 2 artifacts
        for cid in ("SPY_001", "QQQ_002", "IWM_003"):
            arts = list_candidate_artifacts(store, cid)
            assert len(arts) == 2
        assert store["counts"]["total"] == 6

    def test_full_pipeline_artifact_flow(self):
        """Representative artifacts across several stages."""
        store = _fresh_store()
        # 1. Market data
        put_artifact(store, _market_engine_record(artifact_id="art-01"))
        # 2. Scanners
        put_artifact(store, _scanner_record(artifact_id="art-02"))
        # 3. Shared context
        put_artifact(store, build_artifact_record(
            run_id="run-test-001", stage_key="shared_context",
            artifact_key="assembled_context",
            artifact_type="assembled_context",
            artifact_id="art-03",
            data={"modules_assembled": 6},
        ))
        # 4. Decision packet for candidate
        put_artifact(store, _decision_packet_record(
            artifact_id="art-04", candidate_id="SPY_001"))
        # 5. Final response for candidate
        put_artifact(store, _final_response_record(
            artifact_id="art-05", candidate_id="SPY_001"))

        s = summarize_artifact_store(store)
        assert s["total_artifacts"] == 5
        assert s["candidates_tracked"] == 1
        assert len(s["stages_with_artifacts"]) == 5

        ok, errors = validate_artifact_store(store)
        assert ok, f"Errors: {errors}"


# =====================================================================
#  No orchestrator coupling
# =====================================================================

class TestNoCoupling:

    def test_no_stage_transition_methods(self):
        """Store should not have stage execution/transition methods."""
        import app.services.pipeline_artifact_store as module
        public = [n for n in dir(module)
                  if not n.startswith("_") and callable(getattr(module, n))]
        transition_words = {"mark_stage", "run_stage", "execute",
                           "start_pipeline", "tick"}
        for name in public:
            for tw in transition_words:
                assert tw not in name, (
                    f"Store should not have execution method '{name}'"
                )


# =====================================================================
#  Constants
# =====================================================================

class TestConstants:

    def test_valid_artifact_types_non_empty(self):
        assert len(VALID_ARTIFACT_TYPES) >= 10

    def test_valid_artifact_statuses(self):
        expected = {"active", "superseded", "invalid"}
        assert VALID_ARTIFACT_STATUSES == expected

    def test_compatible_versions(self):
        assert "1.0" in _COMPATIBLE_VERSIONS

    def test_key_artifact_types_present(self):
        expected_types = {
            "market_engine_output", "scanner_output",
            "decision_packet", "final_decision_response",
            "policy_output", "prompt_payload",
            "shared_context", "event_context",
        }
        assert expected_types.issubset(VALID_ARTIFACT_TYPES)
