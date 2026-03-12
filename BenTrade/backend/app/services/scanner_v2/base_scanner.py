"""Options Scanner V2 — base scanner ABC.

``BaseV2Scanner`` is the abstract base class for all V2 scanner families.
It provides the 6-phase runner and defines the hooks that family
implementations must provide.

Usage
-----
Subclass ``BaseV2Scanner`` and implement:
- ``construct_candidates()``  (Phase B — family-specific)
- Optionally override ``family_structural_checks()`` (Phase C hook)
- Optionally override ``family_math()`` (Phase E hook)

The runner calls phases A→F in order and produces a ``V2ScanResult``.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.services.scanner_v2.contracts import (
    SCANNER_V2_CONTRACT_VERSION,
    V2Candidate,
    V2CheckResult,
    V2RecomputedMath,
    V2ScanResult,
)
from app.services.scanner_v2.data import (
    V2NarrowedUniverse,
    V2NarrowingRequest,
    narrow_chain,
)
from app.services.scanner_v2.phases import (
    phase_c_structural_validation,
    phase_d_quote_liquidity_sanity,
    phase_d2_trust_hygiene,
    phase_e_recomputed_math,
    phase_f_normalize,
)

_log = logging.getLogger("bentrade.scanner_v2.base_scanner")


class BaseV2Scanner(ABC):
    """Abstract base for all V2 options scanner families.

    Subclasses MUST implement:
        construct_candidates(chain, underlying, context) → list[V2Candidate]

    Subclasses MAY override:
        family_structural_checks(candidate) → list[V2CheckResult]
        family_math(candidate) → V2RecomputedMath
        scanner_version → str
    """

    # ── Identity (override in subclass) ─────────────────────────

    family_key: str = "base"
    """Family grouping key (e.g. ``"vertical_spreads"``)."""

    scanner_version: str = "0.0.0"
    """Semantic version of this family implementation."""

    # ── Structural DTE window ───────────────────────────────────

    dte_min: int = 1
    """Minimum DTE for expirations to consider (structural, not preference)."""

    dte_max: int = 90
    """Maximum DTE for expirations to consider (structural, not preference)."""

    # ── Main runner ─────────────────────────────────────────────

    def run(
        self,
        *,
        scanner_key: str,
        strategy_id: str,
        symbol: str,
        chain: dict[str, Any],
        underlying_price: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> V2ScanResult:
        """Execute the full 6-phase V2 scanner pipeline.

        Parameters
        ----------
        scanner_key
            Scanner key as registered in the pipeline (e.g.
            ``"put_credit_spread"``).
        strategy_id
            Canonical strategy ID (e.g. ``"put_credit_spread"``).
        symbol
            Uppercase ticker symbol.
        chain
            Option chain data from Tradier.  Expected shape:
            ``{"options": {"option": [...]}}`` or a list of option
            contract dicts.
        underlying_price
            Current underlying price.  None if unavailable.
        context
            Additional context passed from the pipeline (market data,
            run_id, etc.).

        Returns
        -------
        V2ScanResult
            Complete scan result with passed + rejected candidates and
            run-level diagnostics.
        """
        t0 = time.monotonic()
        ctx = context or {}

        # ── Phase A: Universe & chain loading ───────────────────
        narrowing_request = self.build_narrowing_request(context=ctx)
        price = underlying_price if underlying_price is not None else 0.0
        narrowed = narrow_chain(
            chain=chain,
            symbol=symbol,
            underlying_price=price,
            request=narrowing_request,
        )
        expirations = narrowed.diagnostics.expirations_kept_list
        _log.info(
            "V2 %s %s: Phase A — %d expirations, %d contracts narrowed "
            "(loaded=%d, kept=%d)",
            scanner_key, symbol,
            narrowed.diagnostics.expirations_kept,
            narrowed.diagnostics.contracts_final,
            narrowed.diagnostics.total_contracts_loaded,
            narrowed.diagnostics.contracts_final,
        )

        # ── Phase B: Candidate construction (family-specific) ──
        candidates = self.construct_candidates(
            chain=chain,
            symbol=symbol,
            underlying_price=underlying_price,
            expirations=expirations,
            strategy_id=strategy_id,
            scanner_key=scanner_key,
            context=ctx,
            narrowed_universe=narrowed,
        )
        total_constructed = len(candidates)
        _log.info(
            "V2 %s %s: Phase B — %d candidates constructed",
            scanner_key, symbol, total_constructed,
        )

        phase_counts: list[dict[str, Any]] = [
            {"phase": "constructed", "remaining": total_constructed},
        ]

        # ── Phase C: Structural validation ──────────────────────
        family_checks = self._get_family_checks_fn()
        candidates = phase_c_structural_validation(
            candidates, family_checks=family_checks,
        )
        remaining_c = sum(1 for c in candidates if not c.diagnostics.reject_reasons)
        phase_counts.append({"phase": "structural_validation", "remaining": remaining_c})
        _log.info(
            "V2 %s %s: Phase C — %d/%d survived structural validation",
            scanner_key, symbol, remaining_c, total_constructed,
        )

        # ── Phase D: Quote & liquidity sanity ───────────────────
        candidates = phase_d_quote_liquidity_sanity(candidates)
        remaining_d = sum(1 for c in candidates if not c.diagnostics.reject_reasons)
        phase_counts.append({"phase": "quote_liquidity_sanity", "remaining": remaining_d})
        _log.info(
            "V2 %s %s: Phase D — %d/%d survived quote/liquidity sanity",
            scanner_key, symbol, remaining_d, total_constructed,
        )

        # ── Phase D2: Trust hygiene (quote/liq sanity + dedup) ──
        dedup_key_fn = self._get_dedup_key_fn()
        candidates, hygiene_summary = phase_d2_trust_hygiene(
            candidates, dedup_key_fn=dedup_key_fn,
        )
        remaining_d2 = sum(1 for c in candidates if not c.diagnostics.reject_reasons)
        phase_counts.append({"phase": "trust_hygiene", "remaining": remaining_d2})
        _log.info(
            "V2 %s %s: Phase D2 — %d/%d survived trust hygiene "
            "(dedup suppressed %d)",
            scanner_key, symbol, remaining_d2, total_constructed,
            hygiene_summary.get("dedup", {}).get("duplicates_suppressed", 0),
        )

        # ── Phase E: Recomputed math ────────────────────────────
        family_math = self._get_family_math_fn()
        candidates = phase_e_recomputed_math(
            candidates, family_math=family_math, family_key=self.family_key,
        )
        remaining_e = sum(1 for c in candidates if not c.diagnostics.reject_reasons)
        phase_counts.append({"phase": "recomputed_math", "remaining": remaining_e})
        _log.info(
            "V2 %s %s: Phase E — %d/%d survived recomputed math",
            scanner_key, symbol, remaining_e, total_constructed,
        )

        # ── Phase F: Normalization & packaging ──────────────────
        candidates = phase_f_normalize(
            candidates, scanner_version=self.scanner_version,
        )
        remaining_f = sum(1 for c in candidates if c.passed)
        phase_counts.append({"phase": "normalized", "remaining": remaining_f})

        # ── Separate passed / rejected ──────────────────────────
        passed = [c for c in candidates if c.passed]
        rejected = [c for c in candidates if not c.passed]

        # ── Aggregate diagnostics ───────────────────────────────
        reject_counter: Counter[str] = Counter()
        warning_counter: Counter[str] = Counter()
        for c in rejected:
            for reason in c.diagnostics.reject_reasons:
                reject_counter[reason] += 1
        for c in candidates:
            for w in c.diagnostics.warnings:
                warning_counter[w] += 1

        elapsed_ms = (time.monotonic() - t0) * 1000

        _log.info(
            "V2 %s %s: done — %d passed, %d rejected, %.1f ms",
            scanner_key, symbol, len(passed), len(rejected), elapsed_ms,
        )

        return V2ScanResult(
            scanner_key=scanner_key,
            strategy_id=strategy_id,
            family_key=self.family_key,
            symbol=symbol,
            candidates=passed,
            rejected=rejected,
            total_constructed=total_constructed,
            total_passed=len(passed),
            total_rejected=len(rejected),
            reject_reason_counts=dict(reject_counter),
            warning_counts=dict(warning_counter),
            phase_counts=phase_counts,
            narrowing_diagnostics=narrowed.diagnostics.to_dict(),
            scanner_version=self.scanner_version,
            contract_version=SCANNER_V2_CONTRACT_VERSION,
            elapsed_ms=round(elapsed_ms, 1),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    # ── Abstract: Phase B ───────────────────────────────────────

    def build_narrowing_request(
        self, *, context: dict[str, Any] | None = None,
    ) -> V2NarrowingRequest:
        """Build the narrowing request for Phase A.

        Override in family subclasses to customize DTE windows,
        strike distance, moneyness, multi-expiry, etc.

        The default implementation uses the instance's dte_min/dte_max.
        """
        return V2NarrowingRequest(
            dte_min=self.dte_min,
            dte_max=self.dte_max,
        )

    @abstractmethod
    def construct_candidates(
        self,
        *,
        chain: dict[str, Any],
        symbol: str,
        underlying_price: float | None,
        expirations: list[str],
        strategy_id: str,
        scanner_key: str,
        context: dict[str, Any],
        narrowed_universe: V2NarrowedUniverse | None = None,
    ) -> list[V2Candidate]:
        """Phase B — build all valid leg combinations for this family.

        Must return a list of V2Candidate with at minimum:
        - candidate_id set
        - scanner_key, strategy_id, family_key set
        - symbol set
        - legs populated (V2Leg instances)
        - math.width set (if applicable)
        - math.net_credit or math.net_debit set
        - expiration, dte set

        ``narrowed_universe`` contains the pre-narrowed chain data
        from Phase A.  Family builders should prefer this over
        raw ``chain`` + ``expirations`` when available.

        Phases C–F will handle validation, math recomputation, and
        normalization.
        """
        ...

    # ── Optional hooks ──────────────────────────────────────────

    def family_structural_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Phase C hook: family-specific structural checks.

        Override to add checks like iron condor wing ordering,
        butterfly body positioning, etc.

        Returns list of V2CheckResult.  If any check fails, add the
        appropriate reject reason to candidate.diagnostics.reject_reasons.
        """
        return []

    def family_math(
        self, candidate: V2Candidate,
    ) -> V2RecomputedMath | None:
        """Phase E hook: family-specific math recomputation.

        Override for families with different math (iron condors,
        butterflies, calendars).  Return a V2RecomputedMath or None
        to use the default vertical-spread math.
        """
        return None

    def family_dedup_key(self, candidate: V2Candidate) -> tuple:
        """Phase D2 hook: family-specific dedup key.

        Override for families that need richer structural equivalence
        (e.g. iron condors with inner/outer wings, calendars with
        multiple expirations).  The default dedup key uses
        (symbol, strategy_id, expiration, frozenset of leg tuples).
        """
        from app.services.scanner_v2.hygiene.dedup import candidate_dedup_key
        return candidate_dedup_key(candidate)

    # ── Internal helpers ────────────────────────────────────────

    def _get_family_checks_fn(self):
        """Wrap family_structural_checks as a callable for Phase C."""
        def _fn(cand: V2Candidate) -> list[V2CheckResult]:
            return self.family_structural_checks(cand)
        return _fn

    def _get_family_math_fn(self):
        """Wrap family_math as a callable for Phase E, or None."""
        # Only provide a math override if the subclass actually overrode it
        if type(self).family_math is BaseV2Scanner.family_math:
            return None  # Use default vertical math

        def _fn(cand: V2Candidate) -> V2RecomputedMath:
            result = self.family_math(cand)
            if result is not None:
                return result
            # Fallback: run default math if family returns None
            from app.services.scanner_v2.phases import _recompute_vertical_math
            _recompute_vertical_math(cand)
            return cand.math
        return _fn

    def _get_dedup_key_fn(self):
        """Return the dedup key function for this family.

        Override ``family_dedup_key()`` in subclasses to provide
        family-specific duplicate detection (e.g. multi-leg condors,
        multi-expiry calendars).  Returns None to use the default
        generic key function.
        """
        if type(self).family_dedup_key is BaseV2Scanner.family_dedup_key:
            return None  # Use default from dedup module
        return self.family_dedup_key

    def _filter_expirations(
        self, chain: dict[str, Any], symbol: str,
    ) -> list[str]:
        """Phase A helper: filter chain expirations to DTE window.

        Parses the Tradier chain format and returns ISO date strings
        for expirations within [dte_min, dte_max].
        """
        from datetime import date

        today = date.today()
        options = _extract_options_list(chain)

        expirations: set[str] = set()
        for opt in options:
            exp_str = opt.get("expiration_date") or opt.get("expiration", "")
            if not exp_str:
                continue
            try:
                exp_date = date.fromisoformat(exp_str)
            except (ValueError, TypeError):
                continue
            dte = (exp_date - today).days
            if self.dte_min <= dte <= self.dte_max:
                expirations.add(exp_str)

        return sorted(expirations)


# ── Module-level helpers ────────────────────────────────────────────

def _extract_options_list(chain: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the flat list of option contracts from a Tradier chain.

    Handles both ``{"options": {"option": [...]}}`` and a direct list.
    """
    if isinstance(chain, list):
        return chain
    options_wrapper = chain.get("options")
    if isinstance(options_wrapper, dict):
        inner = options_wrapper.get("option")
        if isinstance(inner, list):
            return inner
        if isinstance(inner, dict):
            return [inner]
    if isinstance(options_wrapper, list):
        return options_wrapper
    return []
