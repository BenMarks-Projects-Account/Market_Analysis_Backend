"""Latest-valid artifact discovery rules for market_state.json.

This module defines how downstream workflows discover and load the
current consumable market-state artifact.

Greenfield design — does NOT reference archived pipeline code.

Discovery model
---------------
The Market Intelligence Producer writes artifacts to a well-known
directory.  A small pointer file (``latest.json``) is atomically
updated after a successful publish to point at the newly written
artifact.  Consumers read the pointer, then load the artifact.

File layout::

    data/market_state/
        latest.json              ← pointer to current valid artifact
        market_state_<ts>.json   ← timestamped snapshot artifacts
        market_state_<ts>.json
        ...

Atomic publish strategy:
    1. Producer writes the full artifact to a timestamped file.
    2. Producer validates the artifact structurally.
    3. Producer writes ``latest.json`` (small, fast, near-atomic).
    4. Consumers read ``latest.json`` first, then the referenced file.

    Because ``latest.json`` is only updated after the artifact passes
    validation, consumers never see a half-written or invalid artifact
    via the normal discovery path.

    On platforms where atomic rename is available, the producer
    writes ``latest.json.tmp`` then renames to ``latest.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.workflows.market_state_contract import (
    CONSUMABLE_STATUSES,
    FreshnessTier,
    PublicationStatus,
    ValidationResult,
    assess_freshness,
    is_consumable,
    validate_market_state,
)
from app.workflows.architecture import FreshnessPolicy

# ═══════════════════════════════════════════════════════════════════════
# 1. DIRECTORY & FILE CONVENTIONS
# ═══════════════════════════════════════════════════════════════════════

# Relative to the backend data directory (BenTrade/backend/data/).
MARKET_STATE_DIR_NAME = "market_state"

# Pointer filename — always at <data_dir>/market_state/latest.json
POINTER_FILENAME = "latest.json"

# Artifact filename pattern — market_state_<ISO-timestamp>.json
# Timestamp uses compact format: YYYYMMDD_HHMMSS (UTC).
ARTIFACT_FILENAME_PREFIX = "market_state_"
ARTIFACT_FILENAME_SUFFIX = ".json"


def get_market_state_dir(data_dir: str | Path) -> Path:
    """Return the canonical market-state directory path."""
    return Path(data_dir) / MARKET_STATE_DIR_NAME


def make_artifact_filename(generated_at: datetime | None = None) -> str:
    """Create a timestamped artifact filename.

    Parameters
    ----------
    generated_at : datetime | None
        Timestamp for the filename.  Defaults to now (UTC).

    Returns
    -------
    str
        e.g. ``"market_state_20260316_143022.json"``
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    ts = generated_at.strftime("%Y%m%d_%H%M%S")
    return f"{ARTIFACT_FILENAME_PREFIX}{ts}{ARTIFACT_FILENAME_SUFFIX}"


# ═══════════════════════════════════════════════════════════════════════
# 2. POINTER FILE CONTRACT
# ═══════════════════════════════════════════════════════════════════════
#
# ``latest.json`` is a small JSON file with this shape:
#
#   {
#     "artifact_filename": "market_state_20260316_143022.json",
#     "artifact_id":       "<uuid or run-id>",
#     "published_at":      "2026-03-16T14:30:22Z",
#     "status":            "valid",
#     "contract_version":  "1.0"
#   }
#
# The pointer is the *only* file a consumer needs to read to find the
# latest valid artifact.  This avoids scanning the directory.

POINTER_REQUIRED_KEYS: tuple[str, ...] = (
    "artifact_filename",
    "artifact_id",
    "published_at",
    "status",
    "contract_version",
)


@dataclass(frozen=True)
class PointerData:
    """Parsed contents of ``latest.json``."""

    artifact_filename: str
    artifact_id: str
    published_at: str
    status: str
    contract_version: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PointerData:
        """Parse a pointer dict into a PointerData instance.

        Raises KeyError if required keys are missing.
        """
        return cls(
            artifact_filename=d["artifact_filename"],
            artifact_id=d["artifact_id"],
            published_at=d["published_at"],
            status=d["status"],
            contract_version=d["contract_version"],
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_filename": self.artifact_filename,
            "artifact_id": self.artifact_id,
            "published_at": self.published_at,
            "status": self.status,
            "contract_version": self.contract_version,
        }


# ═══════════════════════════════════════════════════════════════════════
# 3. DISCOVERY RESULT
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class DiscoveryResult:
    """Outcome of attempting to discover a market-state artifact."""

    found: bool = False
    artifact_path: Path | None = None
    artifact: dict[str, Any] | None = None
    pointer: PointerData | None = None

    # Consumer assessments
    publication_status: PublicationStatus | None = None
    freshness_tier: FreshnessTier | None = None
    is_usable: bool = False

    # Diagnostics
    validation: ValidationResult | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


# ═══════════════════════════════════════════════════════════════════════
# 4. DISCOVERY LOGIC
# ═══════════════════════════════════════════════════════════════════════


def load_latest_valid(
    data_dir: str | Path,
    policy: FreshnessPolicy | None = None,
    now: datetime | None = None,
) -> DiscoveryResult:
    """Discover and load the latest valid market-state artifact.

    This is the ONLY entry point downstream workflows should use to
    obtain market state.

    Discovery steps
    ---------------
    1. Read ``latest.json`` pointer from the market-state directory.
    2. Check pointer status — if failed/unpublished, report unusable.
    3. Load the referenced artifact file.
    4. Validate the artifact structurally.
    5. Assess freshness against the consumer's wall clock.
    6. Determine consumability (status × freshness × policy).

    Parameters
    ----------
    data_dir : str | Path
        Path to the backend data directory (``BenTrade/backend/data/``).
    policy : FreshnessPolicy | None
        Staleness policy.  Defaults to architecture defaults.
    now : datetime | None
        Reference time for freshness check.  Defaults to now (UTC).

    Returns
    -------
    DiscoveryResult
        Always returned — never raises.  Check ``.found``, ``.is_usable``,
        and ``.error`` to determine outcome.
    """
    if policy is None:
        policy = FreshnessPolicy()
    if now is None:
        now = datetime.now(timezone.utc)

    result = DiscoveryResult()
    ms_dir = get_market_state_dir(data_dir)

    # ── Step 1: Read pointer ──────────────────────────────────────
    pointer_path = ms_dir / POINTER_FILENAME
    if not pointer_path.is_file():
        result.error = f"Pointer file not found: {pointer_path}"
        return result

    try:
        raw = pointer_path.read_text(encoding="utf-8")
        pointer_dict = json.loads(raw)
        pointer = PointerData.from_dict(pointer_dict)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        result.error = f"Invalid pointer file: {exc}"
        return result

    result.pointer = pointer

    # ── Step 2: Check pointer status ──────────────────────────────
    try:
        pub_status = PublicationStatus(pointer.status)
    except ValueError:
        result.error = f"Unknown pointer status: {pointer.status!r}"
        return result

    result.publication_status = pub_status

    if pub_status in CONSUMABLE_STATUSES:
        pass  # proceed to load
    else:
        result.found = True  # file exists but is not consumable
        result.error = f"Latest artifact has unusable status: {pub_status.value}"
        result.is_usable = False
        return result

    # ── Step 3: Load artifact ─────────────────────────────────────
    artifact_path = ms_dir / pointer.artifact_filename
    if not artifact_path.is_file():
        result.error = (
            f"Artifact referenced by pointer not found: {artifact_path}"
        )
        return result

    try:
        artifact_raw = artifact_path.read_text(encoding="utf-8")
        artifact = json.loads(artifact_raw)
    except (json.JSONDecodeError, OSError) as exc:
        result.error = f"Failed to read artifact: {exc}"
        return result

    result.found = True
    result.artifact_path = artifact_path
    result.artifact = artifact

    # ── Step 4: Validate structure ────────────────────────────────
    validation = validate_market_state(artifact)
    result.validation = validation
    if not validation.is_valid:
        result.warnings.append(
            f"Artifact failed structural validation: "
            f"missing={validation.missing_keys}, "
            f"invalid={validation.invalid_sections}"
        )
        # Still allow consumption if status is consumable — the
        # producer already decided it was publishable.

    # ── Step 5: Assess freshness ──────────────────────────────────
    generated_at = artifact.get("generated_at")
    tier = assess_freshness(generated_at, now=now, policy=policy)
    result.freshness_tier = tier

    if tier == FreshnessTier.WARNING:
        result.warnings.append("Market state is approaching staleness")
    elif tier == FreshnessTier.STALE:
        result.warnings.append("Market state is stale")
    elif tier == FreshnessTier.UNKNOWN:
        result.warnings.append("Cannot determine market state freshness")

    # ── Step 6: Determine consumability ───────────────────────────
    result.is_usable = is_consumable(
        pub_status, tier, allow_stale=policy.allow_stale
    )

    return result


# ═══════════════════════════════════════════════════════════════════════
# 5. FALLBACK RULES
# ═══════════════════════════════════════════════════════════════════════
#
# When ``load_latest_valid()`` returns an unusable result, consumers
# have limited options:
#
#   a) If the pointer references a failed/incomplete artifact:
#      - The consumer cannot use it.
#      - A future implementation may scan the directory for the
#        most recent artifact with consumable status, but this is
#        NOT implemented in this prompt.  For now, consumers abort
#        gracefully with a clear error message.
#
#   b) If the artifact is stale and policy forbids stale usage:
#      - The consumer aborts with a staleness error.
#      - A future implementation may allow requesting a fresh
#        Market Intelligence run before retrying.
#
#   c) If the pointer file is missing entirely:
#      - No market state has ever been published.
#      - Consumer aborts with a clear "no market state available"
#        error.
#
# Fallback scanning (reading all timestamped files to find the latest
# valid one) is intentionally deferred to a future prompt.  The
# pointer-based approach is the primary discovery mechanism.
#
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# 6. PUBLISHING HELPERS (for producer use)
# ═══════════════════════════════════════════════════════════════════════


def write_pointer(
    data_dir: str | Path,
    pointer: PointerData,
) -> Path:
    """Write the ``latest.json`` pointer file atomically.

    Uses write-to-tmp + rename for near-atomic updates on most
    platforms.

    Parameters
    ----------
    data_dir : str | Path
        Backend data directory.
    pointer : PointerData
        Pointer contents.

    Returns
    -------
    Path
        Path to the written pointer file.
    """
    ms_dir = get_market_state_dir(data_dir)
    ms_dir.mkdir(parents=True, exist_ok=True)

    pointer_path = ms_dir / POINTER_FILENAME
    tmp_path = ms_dir / f"{POINTER_FILENAME}.tmp"

    content = json.dumps(pointer.to_dict(), indent=2) + "\n"
    tmp_path.write_text(content, encoding="utf-8")

    # Atomic rename (best-effort on Windows)
    try:
        os.replace(str(tmp_path), str(pointer_path))
    except OSError:
        # Fallback: non-atomic write
        if tmp_path.exists():
            pointer_path.write_text(content, encoding="utf-8")
            tmp_path.unlink(missing_ok=True)

    return pointer_path
