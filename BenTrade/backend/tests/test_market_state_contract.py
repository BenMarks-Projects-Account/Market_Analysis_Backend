"""Tests for market_state_contract and market_state_discovery modules.

Focused tests only — no broad regression, no archived pipeline code.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ── Imports under test ────────────────────────────────────────────────
from app.workflows.market_state_contract import (
    CONSUMABLE_STATUSES,
    ENGINE_KEYS,
    MACRO_METRIC_KEYS,
    MARKET_STATE_CONTRACT_VERSION,
    MARKET_STATES,
    NULLABLE_SECTIONS,
    REQUIRED_TOP_LEVEL_KEYS,
    SECTION_SCHEMAS,
    STABILITY_STATES,
    SUPPORT_STATES,
    UNUSABLE_STATUSES,
    FreshnessTier,
    OverallQuality,
    PublicationStatus,
    SectionSchema,
    ValidationResult,
    assess_freshness,
    is_consumable,
    validate_market_state,
)
from app.workflows.market_state_discovery import (
    ARTIFACT_FILENAME_PREFIX,
    ARTIFACT_FILENAME_SUFFIX,
    MARKET_STATE_DIR_NAME,
    POINTER_FILENAME,
    POINTER_REQUIRED_KEYS,
    DiscoveryResult,
    PointerData,
    get_market_state_dir,
    load_latest_valid,
    make_artifact_filename,
    write_pointer,
)
from app.workflows.architecture import FreshnessPolicy


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

NOW = datetime(2026, 3, 16, 14, 30, 0, tzinfo=timezone.utc)


def _make_minimal_artifact(
    status: str = "valid",
    generated_at: str | None = None,
) -> dict:
    """Build a minimal valid market-state artifact dict."""
    if generated_at is None:
        generated_at = NOW.isoformat()

    return {
        "contract_version": MARKET_STATE_CONTRACT_VERSION,
        "artifact_id": "test-run-001",
        "workflow_id": "market_intelligence",
        "generated_at": generated_at,
        "publication": {
            "status": status,
            "published_at": generated_at,
        },
        "freshness": {
            "overall": "fresh",
            "per_source": {},
        },
        "quality": {
            "sources_total": 4,
            "sources_available": 4,
            "sources_degraded": 0,
            "sources_failed": 0,
            "engines_total": 6,
            "engines_succeeded": 6,
            "engines_degraded": 0,
            "engines_failed": 0,
            "overall_quality": "good",
        },
        "market_snapshot": {
            "metrics": {},
            "snapshot_at": generated_at,
        },
        "engines": {},
        "composite": {
            "market_state": "neutral",
            "support_state": "mixed",
            "stability_state": "orderly",
            "confidence": 0.75,
            "summary": "Test composite summary.",
        },
        "conflicts": {
            "status": "clean",
            "conflict_count": 0,
            "conflict_severity": "none",
        },
        "model_interpretation": {
            "status": "ok",
        },
        "consumer_summary": {
            "market_state": "neutral",
            "support_state": "mixed",
            "stability_state": "orderly",
            "confidence": 0.75,
            "vix": 15.0,
            "regime_tags": [],
            "is_degraded": False,
            "summary_text": "Test consumer summary.",
        },
        "lineage": {
            "workflow_id": "market_intelligence",
            "workflow_version": "1.0",
            "run_id": "test-run-001",
        },
        "warnings": [],
    }


def _write_artifact_and_pointer(
    tmp_path: Path,
    artifact: dict | None = None,
    pointer_status: str = "valid",
    generated_at: str | None = None,
) -> tuple[Path, Path]:
    """Write an artifact + pointer into a temp data directory.

    Returns (data_dir, artifact_path).
    """
    if generated_at is None:
        generated_at = NOW.isoformat()
    if artifact is None:
        artifact = _make_minimal_artifact(
            status=pointer_status, generated_at=generated_at
        )

    data_dir = tmp_path / "data"
    ms_dir = data_dir / MARKET_STATE_DIR_NAME
    ms_dir.mkdir(parents=True, exist_ok=True)

    filename = make_artifact_filename(NOW)
    artifact_path = ms_dir / filename
    artifact_path.write_text(
        json.dumps(artifact, indent=2), encoding="utf-8"
    )

    pointer = PointerData(
        artifact_filename=filename,
        artifact_id=artifact.get("artifact_id", "test"),
        published_at=generated_at,
        status=pointer_status,
        contract_version=MARKET_STATE_CONTRACT_VERSION,
    )
    write_pointer(data_dir, pointer)

    return data_dir, artifact_path


# ═══════════════════════════════════════════════════════════════════════
# Contract constants tests
# ═══════════════════════════════════════════════════════════════════════


class TestContractConstants:
    def test_version_is_string(self):
        assert isinstance(MARKET_STATE_CONTRACT_VERSION, str)
        assert MARKET_STATE_CONTRACT_VERSION == "1.0"

    def test_engine_keys_count(self):
        assert len(ENGINE_KEYS) == 6

    def test_engine_keys_are_strings(self):
        for k in ENGINE_KEYS:
            assert isinstance(k, str)

    def test_macro_metric_keys_count(self):
        assert len(MACRO_METRIC_KEYS) == 8

    def test_required_top_level_keys_count(self):
        assert len(REQUIRED_TOP_LEVEL_KEYS) == 15

    def test_required_top_level_keys_include_essentials(self):
        for key in ("contract_version", "artifact_id", "generated_at",
                     "publication", "engines", "composite", "consumer_summary"):
            assert key in REQUIRED_TOP_LEVEL_KEYS


# ═══════════════════════════════════════════════════════════════════════
# Publication status tests
# ═══════════════════════════════════════════════════════════════════════


class TestPublicationStatus:
    def test_five_statuses(self):
        assert len(PublicationStatus) == 5

    @pytest.mark.parametrize("status,expected_consumable", [
        (PublicationStatus.VALID, True),
        (PublicationStatus.DEGRADED, True),
        (PublicationStatus.INCOMPLETE, False),
        (PublicationStatus.FAILED, False),
        (PublicationStatus.UNPUBLISHED, False),
    ])
    def test_consumable_classification(self, status, expected_consumable):
        assert (status in CONSUMABLE_STATUSES) == expected_consumable

    def test_consumable_and_unusable_are_exhaustive(self):
        all_statuses = set(PublicationStatus)
        assert CONSUMABLE_STATUSES | UNUSABLE_STATUSES == all_statuses

    def test_consumable_and_unusable_are_disjoint(self):
        assert CONSUMABLE_STATUSES & UNUSABLE_STATUSES == frozenset()

    def test_str_enum(self):
        assert PublicationStatus.VALID == "valid"
        assert isinstance(PublicationStatus.DEGRADED, str)


# ═══════════════════════════════════════════════════════════════════════
# Freshness assessment tests
# ═══════════════════════════════════════════════════════════════════════


class TestFreshnessAssessment:
    def test_fresh_within_threshold(self):
        gen = (NOW - timedelta(seconds=300)).isoformat()
        assert assess_freshness(gen, now=NOW) == FreshnessTier.FRESH

    def test_warning_at_boundary(self):
        gen = (NOW - timedelta(seconds=600)).isoformat()
        assert assess_freshness(gen, now=NOW) == FreshnessTier.WARNING

    def test_warning_between_thresholds(self):
        gen = (NOW - timedelta(seconds=1200)).isoformat()
        assert assess_freshness(gen, now=NOW) == FreshnessTier.WARNING

    def test_stale_at_degrade_boundary(self):
        gen = (NOW - timedelta(seconds=1800)).isoformat()
        assert assess_freshness(gen, now=NOW) == FreshnessTier.STALE

    def test_stale_beyond_threshold(self):
        gen = (NOW - timedelta(seconds=7200)).isoformat()
        assert assess_freshness(gen, now=NOW) == FreshnessTier.STALE

    def test_unknown_when_none(self):
        assert assess_freshness(None, now=NOW) == FreshnessTier.UNKNOWN

    def test_unknown_when_invalid_string(self):
        assert assess_freshness("not-a-date", now=NOW) == FreshnessTier.UNKNOWN

    def test_fresh_with_future_timestamp(self):
        gen = (NOW + timedelta(seconds=60)).isoformat()
        assert assess_freshness(gen, now=NOW) == FreshnessTier.FRESH

    def test_custom_policy(self):
        policy = FreshnessPolicy(
            warn_after_seconds=60, degrade_after_seconds=120
        )
        gen = (NOW - timedelta(seconds=90)).isoformat()
        assert assess_freshness(gen, now=NOW, policy=policy) == FreshnessTier.WARNING


# ═══════════════════════════════════════════════════════════════════════
# Consumability tests
# ═══════════════════════════════════════════════════════════════════════


class TestIsConsumable:
    def test_valid_fresh(self):
        assert is_consumable("valid", "fresh") is True

    def test_valid_stale_allowed(self):
        assert is_consumable("valid", "stale", allow_stale=True) is True

    def test_valid_stale_disallowed(self):
        assert is_consumable("valid", "stale", allow_stale=False) is False

    def test_degraded_fresh(self):
        assert is_consumable("degraded", "fresh") is True

    def test_failed_fresh(self):
        assert is_consumable("failed", "fresh") is False

    def test_incomplete_fresh(self):
        assert is_consumable("incomplete", "fresh") is False

    def test_invalid_status_string(self):
        assert is_consumable("bogus", "fresh") is False

    def test_invalid_freshness_string(self):
        assert is_consumable("valid", "bogus") is False

    def test_enum_values_accepted(self):
        assert is_consumable(
            PublicationStatus.VALID, FreshnessTier.FRESH
        ) is True


# ═══════════════════════════════════════════════════════════════════════
# Validation tests
# ═══════════════════════════════════════════════════════════════════════


class TestValidation:
    def test_minimal_valid_artifact(self):
        artifact = _make_minimal_artifact()
        result = validate_market_state(artifact)
        assert result.is_valid is True
        assert result.missing_keys == []
        assert result.invalid_sections == []

    def test_missing_top_level_key(self):
        artifact = _make_minimal_artifact()
        del artifact["engines"]
        result = validate_market_state(artifact)
        assert result.is_valid is False
        assert "engines" in result.missing_keys

    def test_nullable_section_none_is_ok(self):
        artifact = _make_minimal_artifact()
        artifact["conflicts"] = None
        result = validate_market_state(artifact)
        assert result.is_valid is True

    def test_non_nullable_section_none_is_invalid(self):
        artifact = _make_minimal_artifact()
        artifact["quality"] = None
        result = validate_market_state(artifact)
        assert result.is_valid is False
        assert "quality" in result.invalid_sections

    def test_missing_section_subkey(self):
        artifact = _make_minimal_artifact()
        del artifact["composite"]["market_state"]
        result = validate_market_state(artifact)
        assert result.is_valid is False
        assert "composite.market_state" in result.invalid_sections

    def test_version_mismatch_warning(self):
        artifact = _make_minimal_artifact()
        artifact["contract_version"] = "99.0"
        result = validate_market_state(artifact)
        assert any("contract_version mismatch" in w for w in result.warnings)

    def test_unknown_status_warning(self):
        artifact = _make_minimal_artifact()
        artifact["publication"]["status"] = "banana"
        result = validate_market_state(artifact)
        assert any("Unknown publication status" in w for w in result.warnings)

    def test_missing_engines_warning(self):
        artifact = _make_minimal_artifact()
        artifact["engines"] = {"breadth_participation": {}}
        result = validate_market_state(artifact)
        assert any("Missing engines" in w for w in result.warnings)

    def test_empty_artifact_fails(self):
        result = validate_market_state({})
        assert result.is_valid is False
        assert len(result.missing_keys) == len(REQUIRED_TOP_LEVEL_KEYS)


# ═══════════════════════════════════════════════════════════════════════
# Section schemas tests
# ═══════════════════════════════════════════════════════════════════════


class TestSectionSchemas:
    def test_all_non_trivial_sections_have_schemas(self):
        for name in ("publication", "freshness", "quality",
                      "market_snapshot", "composite", "conflicts",
                      "model_interpretation", "consumer_summary", "lineage"):
            assert name in SECTION_SCHEMAS

    def test_schema_is_frozen(self):
        s = SECTION_SCHEMAS["publication"]
        assert isinstance(s, SectionSchema)
        with pytest.raises(AttributeError):
            s.section_name = "x"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# Composite state vocabulary tests
# ═══════════════════════════════════════════════════════════════════════


class TestStateVocabularies:
    def test_market_states(self):
        assert MARKET_STATES == {"risk_on", "neutral", "risk_off"}

    def test_support_states(self):
        assert SUPPORT_STATES == {"supportive", "mixed", "fragile"}

    def test_stability_states(self):
        assert STABILITY_STATES == {"orderly", "noisy", "unstable"}

    def test_overall_quality_enum(self):
        assert len(OverallQuality) == 5


# ═══════════════════════════════════════════════════════════════════════
# Discovery module tests
# ═══════════════════════════════════════════════════════════════════════


class TestDiscoveryConstants:
    def test_dir_name(self):
        assert MARKET_STATE_DIR_NAME == "market_state"

    def test_pointer_filename(self):
        assert POINTER_FILENAME == "latest.json"

    def test_artifact_filename_format(self):
        fn = make_artifact_filename(NOW)
        assert fn.startswith(ARTIFACT_FILENAME_PREFIX)
        assert fn.endswith(ARTIFACT_FILENAME_SUFFIX)
        assert "20260316_143000" in fn

    def test_get_market_state_dir(self):
        d = get_market_state_dir("/some/data")
        assert d == Path("/some/data/market_state")


class TestPointerData:
    def test_roundtrip(self):
        p = PointerData(
            artifact_filename="market_state_20260316_143000.json",
            artifact_id="abc-123",
            published_at=NOW.isoformat(),
            status="valid",
            contract_version="1.0",
        )
        d = p.to_dict()
        p2 = PointerData.from_dict(d)
        assert p == p2

    def test_from_dict_missing_key(self):
        with pytest.raises(KeyError):
            PointerData.from_dict({"artifact_filename": "x"})


class TestDiscovery:
    def test_load_valid_artifact(self, tmp_path):
        data_dir, _ = _write_artifact_and_pointer(tmp_path)
        result = load_latest_valid(data_dir, now=NOW)
        assert result.found is True
        assert result.is_usable is True
        assert result.publication_status == PublicationStatus.VALID
        assert result.freshness_tier == FreshnessTier.FRESH
        assert result.artifact is not None

    def test_load_degraded_artifact(self, tmp_path):
        artifact = _make_minimal_artifact(status="degraded")
        data_dir, _ = _write_artifact_and_pointer(
            tmp_path, artifact=artifact, pointer_status="degraded"
        )
        result = load_latest_valid(data_dir, now=NOW)
        assert result.found is True
        assert result.is_usable is True
        assert result.publication_status == PublicationStatus.DEGRADED

    def test_load_failed_artifact_not_usable(self, tmp_path):
        artifact = _make_minimal_artifact(status="failed")
        data_dir, _ = _write_artifact_and_pointer(
            tmp_path, artifact=artifact, pointer_status="failed"
        )
        result = load_latest_valid(data_dir, now=NOW)
        assert result.found is True
        assert result.is_usable is False
        assert result.publication_status == PublicationStatus.FAILED

    def test_missing_pointer_returns_error(self, tmp_path):
        data_dir = tmp_path / "data"
        result = load_latest_valid(data_dir, now=NOW)
        assert result.found is False
        assert result.is_usable is False
        assert "Pointer file not found" in result.error

    def test_stale_artifact_usable_by_default(self, tmp_path):
        old_time = (NOW - timedelta(hours=1)).isoformat()
        data_dir, _ = _write_artifact_and_pointer(
            tmp_path, generated_at=old_time
        )
        result = load_latest_valid(data_dir, now=NOW)
        assert result.found is True
        assert result.freshness_tier == FreshnessTier.STALE
        assert result.is_usable is True  # default policy allows stale
        assert any("stale" in w.lower() for w in result.warnings)

    def test_stale_artifact_unusable_with_strict_policy(self, tmp_path):
        old_time = (NOW - timedelta(hours=1)).isoformat()
        data_dir, _ = _write_artifact_and_pointer(
            tmp_path, generated_at=old_time
        )
        strict = FreshnessPolicy(allow_stale=False)
        result = load_latest_valid(data_dir, now=NOW, policy=strict)
        assert result.found is True
        assert result.freshness_tier == FreshnessTier.STALE
        assert result.is_usable is False

    def test_corrupted_pointer_returns_error(self, tmp_path):
        data_dir = tmp_path / "data"
        ms_dir = data_dir / MARKET_STATE_DIR_NAME
        ms_dir.mkdir(parents=True)
        (ms_dir / POINTER_FILENAME).write_text("not json", encoding="utf-8")
        result = load_latest_valid(data_dir, now=NOW)
        assert result.found is False
        assert "Invalid pointer file" in result.error

    def test_missing_artifact_file_returns_error(self, tmp_path):
        data_dir = tmp_path / "data"
        ms_dir = data_dir / MARKET_STATE_DIR_NAME
        ms_dir.mkdir(parents=True)
        pointer = PointerData(
            artifact_filename="nonexistent.json",
            artifact_id="x",
            published_at=NOW.isoformat(),
            status="valid",
            contract_version="1.0",
        )
        write_pointer(data_dir, pointer)
        result = load_latest_valid(data_dir, now=NOW)
        assert result.found is False
        assert "not found" in result.error

    def test_validation_warnings_propagated(self, tmp_path):
        artifact = _make_minimal_artifact()
        del artifact["composite"]["confidence"]
        data_dir, _ = _write_artifact_and_pointer(
            tmp_path, artifact=artifact
        )
        result = load_latest_valid(data_dir, now=NOW)
        assert result.found is True
        assert result.validation is not None
        assert not result.validation.is_valid

    def test_lineage_preserved_in_artifact(self, tmp_path):
        data_dir, _ = _write_artifact_and_pointer(tmp_path)
        result = load_latest_valid(data_dir, now=NOW)
        lineage = result.artifact["lineage"]
        assert lineage["workflow_id"] == "market_intelligence"
        assert lineage["run_id"] == "test-run-001"


class TestWritePointer:
    def test_creates_directory_and_file(self, tmp_path):
        data_dir = tmp_path / "data"
        pointer = PointerData(
            artifact_filename="test.json",
            artifact_id="x",
            published_at=NOW.isoformat(),
            status="valid",
            contract_version="1.0",
        )
        path = write_pointer(data_dir, pointer)
        assert path.is_file()
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["artifact_filename"] == "test.json"
        assert content["status"] == "valid"

    def test_overwrites_existing_pointer(self, tmp_path):
        data_dir = tmp_path / "data"
        p1 = PointerData("a.json", "1", NOW.isoformat(), "valid", "1.0")
        p2 = PointerData("b.json", "2", NOW.isoformat(), "degraded", "1.0")
        write_pointer(data_dir, p1)
        write_pointer(data_dir, p2)
        content = json.loads(
            (data_dir / MARKET_STATE_DIR_NAME / POINTER_FILENAME)
            .read_text(encoding="utf-8")
        )
        assert content["artifact_filename"] == "b.json"
        assert content["status"] == "degraded"
