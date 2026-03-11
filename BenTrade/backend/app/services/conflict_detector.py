"""
Contradiction / Conflict Detector v1
======================================

Reusable reasoning-layer module that inspects assembled context (from
``context_assembler.assemble_context``) and surfaces structured areas of
disagreement.

Conflict families detected
--------------------------
1. **market_conflicts**        – modules disagreeing on label/regime/direction
2. **candidate_conflicts**     – candidate direction vs market tone mismatch
3. **time_horizon_conflicts**  – meaningful horizon mismatch across layers
4. **model_conflicts**         – model tone vs structured engine/candidate data
5. **quality_conflicts**       – degraded/stale/low-confidence inputs weakening
                                 apparent alignment

Output contract
---------------
``detect_conflicts(assembled)`` returns::

    {
        "status":                   "clean" | "conflicts_detected" | "insufficient_data",
        "detected_at":              ISO-8601 str,
        "conflict_count":           int,
        "conflict_severity":        "none" | "low" | "moderate" | "high",
        "conflict_summary":         str,           # 1-2 sentence human summary
        "conflict_flags":           list[str],     # machine-readable flag codes
        "market_conflicts":         list[ConflictItem],
        "candidate_conflicts":      list[ConflictItem],
        "model_conflicts":          list[ConflictItem],
        "time_horizon_conflicts":   list[ConflictItem],
        "quality_conflicts":        list[ConflictItem],
        "metadata": {
            "detector_version":     "1.0",
            "engines_inspected":    int,
            "candidates_inspected": int,
            "models_inspected":     int,
            "degraded_inputs":      int,
        },
    }

ConflictItem schema::

    {
        "conflict_type":      str,    # stable taxonomy code
        "severity":           str,    # "low" | "moderate" | "high"
        "title":              str,    # short human label
        "description":        str,    # 1-2 sentence explanation
        "entities":           list[str],  # engine_keys / symbols involved
        "time_horizon":       str | None,
        "evidence":           dict,   # supporting data for reviewability
        "confidence_impact":  str,    # "none" | "minor" | "moderate" | "major"
        "resolution_note":    str | None,
    }

Severity semantics
------------------
- **low**      – minor tension that may not be actionable alone
- **moderate** – meaningful disagreement that should be surfaced
- **high**     – strong contradiction that demands attention

confidence_impact semantics
---------------------------
- **none**     – conflict does not reduce confidence in alignment assessment
- **minor**    – slight reduction in confidence
- **moderate** – noticeable weakening of signal reliability
- **major**    – substantial undermining of apparent consensus
"""

from __future__ import annotations

import datetime as _dt
import re as _re
from typing import Any

from app.utils.time_horizon import (
    HORIZON_ORDER,
    horizon_rank,
    validate_horizon,
)
from app.utils.tone_classification import (
    BULLISH_KEYWORDS,
    BEARISH_KEYWORDS,
    NEUTRAL_KEYWORDS,
    classify_label,
    classify_score,
    engine_tone,
)

# ── Constants ────────────────────────────────────────────────────────

_DETECTOR_VERSION = "1.1"

_SEVERITY_RANK = {"low": 0, "moderate": 1, "high": 2}

# Backward-compatible private aliases – existing tests import these
_BULLISH_KEYWORDS = BULLISH_KEYWORDS
_BEARISH_KEYWORDS = BEARISH_KEYWORDS
_NEUTRAL_KEYWORDS = NEUTRAL_KEYWORDS

# Candidate directions we treat as directional
_BULLISH_DIRECTIONS = frozenset({"long"})
_BEARISH_DIRECTIONS = frozenset({"short"})

# Horizon rank gap that we consider "meaningful" for conflict detection.
# A gap of 1 (e.g. intraday ↔ short_term) is normal; >= 2 is notable.
_HORIZON_GAP_THRESHOLD = 2

# ── Public API ───────────────────────────────────────────────────────


def detect_conflicts(assembled: dict[str, Any]) -> dict[str, Any]:
    """Inspect assembled context and return structured conflict report.

    Parameters
    ----------
    assembled : dict
        Output of ``context_assembler.assemble_context()``.

    Returns
    -------
    dict  – conflict report conforming to the output contract above.
    """
    market_ctx = assembled.get("market_context") or {}
    cand_ctx = assembled.get("candidate_context") or {}
    model_ctx = assembled.get("model_context") or {}
    quality_sum = assembled.get("quality_summary") or {}
    freshness_sum = assembled.get("freshness_summary") or {}
    horizon_sum = assembled.get("horizon_summary") or {}

    # Count inspectable inputs
    engines_inspected = len(market_ctx)
    candidates_list = cand_ctx.get("candidates", [])
    candidates_inspected = len(candidates_list)
    model_analyses = model_ctx.get("analyses", {})
    models_inspected = len(model_analyses)

    degraded_inputs = _count_degraded(market_ctx, candidates_list, model_analyses)

    # Bail early if nothing to inspect
    if engines_inspected == 0 and candidates_inspected == 0 and models_inspected == 0:
        return _build_output(
            status="insufficient_data",
            conflicts=[],
            summary="No market, candidate, or model data available for conflict analysis.",
            engines_inspected=0,
            candidates_inspected=0,
            models_inspected=0,
            degraded_inputs=0,
        )

    # ── Detect each conflict family ──────────────────────────────────
    all_conflicts: list[dict[str, Any]] = []

    all_conflicts.extend(_detect_market_conflicts(market_ctx))
    all_conflicts.extend(_detect_candidate_conflicts(market_ctx, candidates_list))
    all_conflicts.extend(_detect_time_horizon_conflicts(
        market_ctx, candidates_list, model_analyses, horizon_sum,
    ))
    all_conflicts.extend(_detect_model_conflicts(
        market_ctx, candidates_list, model_analyses,
    ))
    all_conflicts.extend(_detect_quality_conflicts(
        market_ctx, candidates_list, model_analyses,
        quality_sum, freshness_sum,
    ))

    # ── Build output ─────────────────────────────────────────────────
    return _build_output(
        status="conflicts_detected" if all_conflicts else "clean",
        conflicts=all_conflicts,
        summary=_build_summary(all_conflicts),
        engines_inspected=engines_inspected,
        candidates_inspected=candidates_inspected,
        models_inspected=models_inspected,
        degraded_inputs=degraded_inputs,
    )


# ── Output builders ──────────────────────────────────────────────────

_CONFLICT_FAMILIES = (
    "market_conflicts",
    "candidate_conflicts",
    "model_conflicts",
    "time_horizon_conflicts",
    "quality_conflicts",
)

_FAMILY_KEY_MAP: dict[str, str] = {
    "market_label_split": "market_conflicts",
    "market_regime_disagreement": "market_conflicts",
    "market_bull_bear_cluster": "market_conflicts",
    "candidate_vs_market_direction": "candidate_conflicts",
    "candidate_vs_market_regime": "candidate_conflicts",
    "model_vs_market_tone": "model_conflicts",
    "model_vs_candidate_tone": "model_conflicts",
    "horizon_candidate_market_gap": "time_horizon_conflicts",
    "horizon_model_market_gap": "time_horizon_conflicts",
    "quality_degraded_consensus": "quality_conflicts",
    "quality_stale_module": "quality_conflicts",
    "quality_low_confidence_module": "quality_conflicts",
    "quality_missing_modules": "quality_conflicts",
}


def _build_output(
    *,
    status: str,
    conflicts: list[dict[str, Any]],
    summary: str,
    engines_inspected: int,
    candidates_inspected: int,
    models_inspected: int,
    degraded_inputs: int,
) -> dict[str, Any]:
    """Assemble the full conflict report."""
    # Bucket conflicts into families
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in _CONFLICT_FAMILIES}
    for c in conflicts:
        family = _FAMILY_KEY_MAP.get(c.get("conflict_type", ""), "quality_conflicts")
        buckets[family].append(c)

    # Determine overall severity
    if not conflicts:
        severity = "none"
    else:
        max_rank = max(_SEVERITY_RANK.get(c.get("severity", "low"), 0) for c in conflicts)
        severity = {0: "low", 1: "moderate", 2: "high"}.get(max_rank, "low")

    # Collect flags
    flags = sorted({c["conflict_type"] for c in conflicts})

    return {
        "status": status,
        "detected_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "conflict_count": len(conflicts),
        "conflict_severity": severity,
        "conflict_summary": summary,
        "conflict_flags": flags,
        **buckets,
        "metadata": {
            "detector_version": _DETECTOR_VERSION,
            "engines_inspected": engines_inspected,
            "candidates_inspected": candidates_inspected,
            "models_inspected": models_inspected,
            "degraded_inputs": degraded_inputs,
        },
    }


def _build_summary(conflicts: list[dict[str, Any]]) -> str:
    """Human-readable 1-2 sentence summary of detected conflicts."""
    if not conflicts:
        return "No conflicts detected. Assembled context appears aligned."
    n = len(conflicts)
    families = sorted({_FAMILY_KEY_MAP.get(c.get("conflict_type", ""), "other") for c in conflicts})
    family_labels = [f.replace("_", " ") for f in families]
    sev_counts = {}
    for c in conflicts:
        s = c.get("severity", "low")
        sev_counts[s] = sev_counts.get(s, 0) + 1
    sev_parts = []
    for s in ("high", "moderate", "low"):
        if sev_counts.get(s):
            sev_parts.append(f"{sev_counts[s]} {s}")
    return (
        f"{n} conflict(s) detected across {', '.join(family_labels)} "
        f"({', '.join(sev_parts)})."
    )


def _make_conflict(
    *,
    conflict_type: str,
    severity: str,
    title: str,
    description: str,
    entities: list[str],
    time_horizon: str | None = None,
    evidence: dict[str, Any] | None = None,
    confidence_impact: str = "none",
    resolution_note: str | None = None,
) -> dict[str, Any]:
    """Create a single ConflictItem dict."""
    return {
        "conflict_type": conflict_type,
        "severity": severity,
        "title": title,
        "description": description,
        "entities": entities,
        "time_horizon": time_horizon,
        "evidence": evidence or {},
        "confidence_impact": confidence_impact,
        "resolution_note": resolution_note,
    }


# ── Helpers ──────────────────────────────────────────────────────────

def _get_normalized(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract normalized sub-dict from a market/model payload."""
    return payload.get("normalized", payload) if isinstance(payload, dict) else {}


# Backward-compatible private aliases — delegates to shared module.
# Existing tests and internal callers use these underscore-prefixed names.

def _classify_label(label: str | None) -> str:
    """Classify a label/short_label → bullish/bearish/neutral/unknown."""
    return classify_label(label)


def _classify_score(score: float | None) -> str:
    """Classify a 0–100 score → bullish/bearish/neutral/unknown."""
    return classify_score(score)


def _engine_tone(norm: dict[str, Any]) -> str:
    """Derive dominant tone from engine label + score."""
    return engine_tone(norm)


def _candidate_tone(cand_norm: dict[str, Any]) -> str:
    """Derive the directional tone of a candidate."""
    direction = (cand_norm.get("direction") or "").lower()
    if direction in _BULLISH_DIRECTIONS:
        return "bullish"
    if direction in _BEARISH_DIRECTIONS:
        return "bearish"
    return "neutral"


def _is_fallback(payload: dict[str, Any]) -> bool:
    """Check whether a payload was built by a fallback path."""
    norm = payload.get("normalized", payload) if isinstance(payload, dict) else {}
    return bool(norm.get("_fallback")) or payload.get("source") == "fallback"


def _count_degraded(
    market_ctx: dict,
    candidates: list,
    model_analyses: dict,
) -> int:
    """Count inputs that are fallback, degraded, or low-confidence."""
    count = 0
    for eng_payload in market_ctx.values():
        if _is_fallback(eng_payload):
            count += 1
    for cand in candidates:
        norm = cand.get("normalized", cand)
        if norm.get("_fallback"):
            count += 1
    for analysis in model_analyses.values():
        norm = _get_normalized(analysis)
        if norm.get("_fallback") or norm.get("status") in ("error", "degraded"):
            count += 1
    return count


def _majority_market_tone(market_ctx: dict) -> tuple[str, dict[str, int]]:
    """Return (dominant_tone, {tone: count}) from market modules.

    Only counts modules with at least moderate signal quality.
    """
    tone_counts: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0, "unknown": 0}
    for eng_payload in market_ctx.values():
        norm = _get_normalized(eng_payload)
        tone = _engine_tone(norm)
        tone_counts[tone] = tone_counts.get(tone, 0) + 1

    # Majority: most common non-unknown tone
    ranked = sorted(
        [(t, c) for t, c in tone_counts.items() if t != "unknown"],
        key=lambda x: -x[1],
    )
    if ranked and ranked[0][1] > 0:
        return ranked[0][0], tone_counts
    return "unknown", tone_counts


# ═════════════════════════════════════════════════════════════════════
# CONFLICT FAMILY 1: Market Module Disagreement
# ═════════════════════════════════════════════════════════════════════

def _detect_market_conflicts(market_ctx: dict) -> list[dict[str, Any]]:
    """Detect disagreement among market modules.

    Heuristics:
    1. Label split — some engines bullish while others bearish.
    2. Bull/bear factor cluster — engines collectively have strong
       opposing factors.
    """
    conflicts: list[dict[str, Any]] = []
    if len(market_ctx) < 2:
        return conflicts

    # ── 1. Label split ───────────────────────────────────────────────
    tone_map: dict[str, str] = {}  # engine_key → tone
    for eng_key, eng_payload in market_ctx.items():
        norm = _get_normalized(eng_payload)
        tone_map[eng_key] = _engine_tone(norm)

    bullish_engines = [k for k, t in tone_map.items() if t == "bullish"]
    bearish_engines = [k for k, t in tone_map.items() if t == "bearish"]

    if bullish_engines and bearish_engines:
        # Severity based on balance
        total_directional = len(bullish_engines) + len(bearish_engines)
        minority = min(len(bullish_engines), len(bearish_engines))
        ratio = minority / total_directional if total_directional else 0
        severity = "high" if ratio >= 0.4 else "moderate" if ratio >= 0.25 else "low"

        conflicts.append(_make_conflict(
            conflict_type="market_label_split",
            severity=severity,
            title="Market modules disagree on direction",
            description=(
                f"{len(bullish_engines)} module(s) bullish vs "
                f"{len(bearish_engines)} module(s) bearish."
            ),
            entities=bullish_engines + bearish_engines,
            evidence={
                "bullish_engines": bullish_engines,
                "bearish_engines": bearish_engines,
                "tone_map": tone_map,
            },
            confidence_impact="moderate" if severity in ("moderate", "high") else "minor",
        ))

    # ── 2. Bull/bear factor cluster ─────────────────────────────────
    total_bull = 0
    total_bear = 0
    engines_with_factors: list[str] = []
    for eng_key, eng_payload in market_ctx.items():
        norm = _get_normalized(eng_payload)
        bull = norm.get("bull_factors") or norm.get("confirming_signals") or []
        bear = norm.get("bear_factors") or norm.get("contradicting_signals") or []
        total_bull += len(bull)
        total_bear += len(bear)
        if bull or bear:
            engines_with_factors.append(eng_key)

    if total_bull >= 3 and total_bear >= 3:
        factor_ratio = min(total_bull, total_bear) / max(total_bull, total_bear)
        if factor_ratio >= 0.4:
            severity = "moderate" if factor_ratio >= 0.6 else "low"
            conflicts.append(_make_conflict(
                conflict_type="market_bull_bear_cluster",
                severity=severity,
                title="Competing bull and bear evidence across modules",
                description=(
                    f"{total_bull} bullish factor(s) coexist with "
                    f"{total_bear} bearish factor(s) across market modules."
                ),
                entities=engines_with_factors,
                evidence={
                    "total_bull_factors": total_bull,
                    "total_bear_factors": total_bear,
                    "factor_ratio": round(factor_ratio, 2),
                },
                confidence_impact="minor",
            ))

    # ── 3. Regime-tag disagreement ───────────────────────────────
    # Check engine regime_tags for contradictory pairings.
    # regime_tags are derived from engine labels via
    # engine_output_contract._derive_regime_tags() and are stable.
    conflicts.extend(_detect_regime_tag_disagreement(market_ctx))

    return conflicts


# Regime tag pairs that are contradictory.
# Each tuple is (tag_a, tag_b) where seeing both across engines = disagreement.
_CONTRADICTORY_TAG_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("bullish", "bearish"),
    ("risk_on", "risk_off"),
    ("expansion", "contraction"),
    ("broadening", "narrowing"),
})


def _detect_regime_tag_disagreement(
    market_ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    """Detect contradictory regime_tags across market engines.

    Checks each known contradictory pair (e.g. bullish vs bearish,
    risk_on vs risk_off).  Fires ``market_regime_disagreement`` when
    engines carry opposing tags.

    Inputs:
    - market_ctx — {engine_key: payload} from assembled context.

    Formula:
    - For each (tag_a, tag_b) in _CONTRADICTORY_TAG_PAIRS:
        - engines_a = engines whose regime_tags include tag_a
        - engines_b = engines whose regime_tags include tag_b
        - If both non-empty → conflict
    - Severity: high if ratio ≥ 0.4, moderate otherwise.
    """
    conflicts: list[dict[str, Any]] = []
    if len(market_ctx) < 2:
        return conflicts

    # Collect regime_tags per engine
    engine_tags: dict[str, list[str]] = {}
    for eng_key, eng_payload in market_ctx.items():
        norm = _get_normalized(eng_payload)
        tags = norm.get("regime_tags") or []
        if isinstance(tags, list) and tags:
            engine_tags[eng_key] = [t.lower() for t in tags if isinstance(t, str)]

    if len(engine_tags) < 2:
        return conflicts

    # Build tag → engine set lookup
    tag_to_engines: dict[str, list[str]] = {}
    for eng_key, tags in engine_tags.items():
        for tag in tags:
            tag_to_engines.setdefault(tag, []).append(eng_key)

    # Check contradictory pairs
    for tag_a, tag_b in _CONTRADICTORY_TAG_PAIRS:
        engines_a = tag_to_engines.get(tag_a, [])
        engines_b = tag_to_engines.get(tag_b, [])
        if engines_a and engines_b:
            total = len(engines_a) + len(engines_b)
            minority = min(len(engines_a), len(engines_b))
            ratio = minority / total if total else 0
            severity = "high" if ratio >= 0.4 else "moderate"

            conflicts.append(_make_conflict(
                conflict_type="market_regime_disagreement",
                severity=severity,
                title=f"Regime tag split: {tag_a} vs {tag_b}",
                description=(
                    f"{len(engines_a)} engine(s) tagged '{tag_a}' while "
                    f"{len(engines_b)} engine(s) tagged '{tag_b}'."
                ),
                entities=sorted(set(engines_a + engines_b)),
                evidence={
                    "tag_pair": [tag_a, tag_b],
                    f"{tag_a}_engines": engines_a,
                    f"{tag_b}_engines": engines_b,
                    "balance_ratio": round(ratio, 2),
                    "all_engine_tags": engine_tags,
                },
                confidence_impact="moderate" if severity == "high" else "minor",
            ))

    return conflicts


# ═════════════════════════════════════════════════════════════════════
# CONFLICT FAMILY 2: Candidate vs Market
# ═════════════════════════════════════════════════════════════════════

def _detect_candidate_conflicts(
    market_ctx: dict,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect candidate direction conflicting with overall market tone.

    Heuristics:
    1. Directional mismatch — bullish candidate in bearish market or vice versa.
    2. Premium-sell in mixed/risk-off backdrop — options short-premium
       candidates when market context is cautionary.
    """
    conflicts: list[dict[str, Any]] = []
    if not candidates or not market_ctx:
        return conflicts

    majority_tone, tone_counts = _majority_market_tone(market_ctx)

    for cand in candidates:
        norm = cand.get("normalized", cand)
        symbol = norm.get("symbol", "?")
        family = norm.get("strategy_family", "")
        scanner_key = norm.get("scanner_key", "")
        cand_direction = _candidate_tone(norm)

        # ── 1. Directional mismatch ─────────────────────────────────
        if (
            cand_direction == "bullish" and majority_tone == "bearish"
        ) or (
            cand_direction == "bearish" and majority_tone == "bullish"
        ):
            severity = "moderate"
            # Strengthen if majority is clear
            directional_total = tone_counts.get("bullish", 0) + tone_counts.get("bearish", 0)
            majority_count = max(tone_counts.get("bullish", 0), tone_counts.get("bearish", 0))
            if directional_total >= 3 and majority_count / directional_total >= 0.7:
                severity = "high"

            conflicts.append(_make_conflict(
                conflict_type="candidate_vs_market_direction",
                severity=severity,
                title=f"{symbol} direction vs market tone",
                description=(
                    f"{symbol} ({scanner_key}) is {cand_direction} but "
                    f"market majority is {majority_tone}."
                ),
                entities=[symbol, scanner_key],
                time_horizon=norm.get("time_horizon"),
                evidence={
                    "candidate_direction": cand_direction,
                    "market_majority_tone": majority_tone,
                    "market_tone_counts": tone_counts,
                    "strategy_family": family,
                },
                confidence_impact="moderate",
            ))

        # ── 2. Premium-sell in cautionary market ────────────────────
        if (
            family == "options"
            and (norm.get("direction") or "").lower() == "short"
            and majority_tone in ("bearish", "neutral")
        ):
            # Check if volatility engine or cross-asset show risk signals
            risk_signals = []
            for eng_key in ("volatility_options", "cross_asset_macro", "news_sentiment"):
                eng_payload = market_ctx.get(eng_key, {})
                eng_norm = _get_normalized(eng_payload)
                eng_tone = _engine_tone(eng_norm)
                if eng_tone == "bearish":
                    risk_signals.append(eng_key)

            if risk_signals or majority_tone == "bearish":
                severity = "moderate" if risk_signals else "low"
                conflicts.append(_make_conflict(
                    conflict_type="candidate_vs_market_regime",
                    severity=severity,
                    title=f"{symbol} premium-sell vs cautionary backdrop",
                    description=(
                        f"{symbol} ({scanner_key}) is a short-premium setup but "
                        f"market context is {majority_tone}"
                        + (f" with risk signals from {', '.join(risk_signals)}" if risk_signals else "")
                        + "."
                    ),
                    entities=[symbol, scanner_key] + risk_signals,
                    time_horizon=norm.get("time_horizon"),
                    evidence={
                        "candidate_direction": "short",
                        "market_majority_tone": majority_tone,
                        "risk_signal_engines": risk_signals,
                        "strategy_family": family,
                    },
                    confidence_impact="moderate" if risk_signals else "minor",
                ))

    return conflicts


# ═════════════════════════════════════════════════════════════════════
# CONFLICT FAMILY 3: Time-Horizon Mismatch
# ═════════════════════════════════════════════════════════════════════

def _detect_time_horizon_conflicts(
    market_ctx: dict,
    candidates: list[dict[str, Any]],
    model_analyses: dict,
    horizon_summary: dict,
) -> list[dict[str, Any]]:
    """Detect meaningful time-horizon mismatches.

    Heuristics:
    1. Candidate horizon vs supporting market horizons — flag if
       the candidate's horizon has no matching-or-adjacent market module.
    2. Model horizon vs market/candidate horizon — flag if model
       commentary is focused on a very different timeframe.
    """
    conflicts: list[dict[str, Any]] = []

    market_horizons: dict[str, str] = horizon_summary.get("market_horizons", {})
    if not market_horizons:
        # Extract from market_ctx directly
        for eng_key, eng_payload in market_ctx.items():
            norm = _get_normalized(eng_payload)
            h = norm.get("time_horizon")
            if h:
                market_horizons[eng_key] = h

    market_ranks = [horizon_rank(h) for h in market_horizons.values() if h != "unknown"]

    # ── 1. Candidate vs market horizon gap ───────────────────────────
    for cand in candidates:
        norm = cand.get("normalized", cand)
        cand_horizon = validate_horizon(norm.get("time_horizon"))
        if cand_horizon == "unknown" or not market_ranks:
            continue

        cand_rank = horizon_rank(cand_horizon)
        # Find closest market rank
        min_gap = min(abs(cand_rank - mr) for mr in market_ranks)

        if min_gap >= _HORIZON_GAP_THRESHOLD:
            symbol = norm.get("symbol", "?")
            scanner_key = norm.get("scanner_key", "")
            closest_horizons = [
                h for h in market_horizons.values()
                if abs(horizon_rank(h) - cand_rank) == min_gap
            ]
            severity = "moderate" if min_gap >= 3 else "low"
            conflicts.append(_make_conflict(
                conflict_type="horizon_candidate_market_gap",
                severity=severity,
                title=f"{symbol} horizon mismatch with market",
                description=(
                    f"{symbol} ({scanner_key}) operates on '{cand_horizon}' horizon "
                    f"but closest market module horizon is '{closest_horizons[0]}' "
                    f"(gap={min_gap})."
                ),
                entities=[symbol, scanner_key],
                time_horizon=cand_horizon,
                evidence={
                    "candidate_horizon": cand_horizon,
                    "candidate_rank": cand_rank,
                    "market_horizons": market_horizons,
                    "min_gap": min_gap,
                },
                confidence_impact="minor",
            ))

    # ── 2. Model vs market/candidate horizon gap ─────────────────────
    cand_horizons = [
        validate_horizon((c.get("normalized", c)).get("time_horizon"))
        for c in candidates
    ]
    cand_ranks = [horizon_rank(h) for h in cand_horizons if h != "unknown"]
    all_context_ranks = market_ranks + cand_ranks

    if all_context_ranks:
        for analysis_type, analysis in model_analyses.items():
            model_norm = _get_normalized(analysis)
            model_h = validate_horizon(model_norm.get("time_horizon"))
            if model_h == "unknown":
                continue
            model_rank = horizon_rank(model_h)
            min_gap = min(abs(model_rank - cr) for cr in all_context_ranks)
            if min_gap >= _HORIZON_GAP_THRESHOLD:
                severity = "low"
                conflicts.append(_make_conflict(
                    conflict_type="horizon_model_market_gap",
                    severity=severity,
                    title=f"Model '{analysis_type}' horizon mismatch",
                    description=(
                        f"Model analysis '{analysis_type}' is focused on "
                        f"'{model_h}' but structured context ranges "
                        f"'{horizon_summary.get('shortest', '?')}' to "
                        f"'{horizon_summary.get('longest', '?')}'."
                    ),
                    entities=[analysis_type],
                    time_horizon=model_h,
                    evidence={
                        "model_horizon": model_h,
                        "model_rank": model_rank,
                        "context_shortest": horizon_summary.get("shortest"),
                        "context_longest": horizon_summary.get("longest"),
                        "min_gap": min_gap,
                    },
                    confidence_impact="minor",
                ))

    return conflicts


# ═════════════════════════════════════════════════════════════════════
# CONFLICT FAMILY 4: Model vs Structured Data
# ═════════════════════════════════════════════════════════════════════

def _detect_model_conflicts(
    market_ctx: dict,
    candidates: list[dict[str, Any]],
    model_analyses: dict,
) -> list[dict[str, Any]]:
    """Detect model-analysis tone conflicting with structured context.

    Heuristics:
    1. Model tone vs market majority — model summary/actions suggest
       a direction that disagrees with market module consensus.
    2. Model tone vs candidate direction — model recommends against
       the candidate's setup direction.
    """
    conflicts: list[dict[str, Any]] = []
    if not model_analyses:
        return conflicts

    majority_tone, tone_counts = _majority_market_tone(market_ctx) if market_ctx else ("unknown", {})

    for analysis_type, analysis in model_analyses.items():
        model_norm = _get_normalized(analysis)

        # Skip degraded/error models — handled by quality conflicts
        if model_norm.get("status") in ("error",):
            continue

        model_tone = _infer_model_tone(model_norm)
        if model_tone == "unknown":
            continue

        # ── 1. Model vs market tone ──────────────────────────────────
        if (
            majority_tone in ("bullish", "bearish")
            and model_tone in ("bullish", "bearish")
            and model_tone != majority_tone
        ):
            # Confidence of model matters
            model_conf = model_norm.get("confidence")
            severity = "moderate"
            ci = "moderate"
            if model_conf is not None and model_conf < 0.5:
                severity = "low"
                ci = "minor"

            conflicts.append(_make_conflict(
                conflict_type="model_vs_market_tone",
                severity=severity,
                title=f"Model '{analysis_type}' disagrees with market",
                description=(
                    f"Model analysis '{analysis_type}' appears {model_tone} "
                    f"but market majority is {majority_tone}."
                ),
                entities=[analysis_type],
                evidence={
                    "model_tone": model_tone,
                    "model_confidence": model_conf,
                    "market_majority_tone": majority_tone,
                    "market_tone_counts": tone_counts,
                },
                confidence_impact=ci,
            ))

        # ── 2. Model vs candidate direction ──────────────────────────
        for cand in candidates:
            cand_norm = cand.get("normalized", cand)
            cand_direction = _candidate_tone(cand_norm)
            symbol = cand_norm.get("symbol", "?")

            if (
                cand_direction in ("bullish", "bearish")
                and model_tone in ("bullish", "bearish")
                and model_tone != cand_direction
            ):
                conflicts.append(_make_conflict(
                    conflict_type="model_vs_candidate_tone",
                    severity="low",
                    title=f"Model '{analysis_type}' vs {symbol} direction",
                    description=(
                        f"Model analysis '{analysis_type}' appears {model_tone} "
                        f"but candidate {symbol} is {cand_direction}."
                    ),
                    entities=[analysis_type, symbol],
                    evidence={
                        "model_tone": model_tone,
                        "candidate_direction": cand_direction,
                        "candidate_symbol": symbol,
                    },
                    confidence_impact="minor",
                ))

    return conflicts


def _infer_model_tone(model_norm: dict[str, Any]) -> str:
    """Infer directional tone from model-analysis normalized output.

    Uses metadata label/score and text-based heuristic on summary + actions.

    Returns "bullish", "bearish", "mixed", or "unknown".

    - "mixed" means both bullish and bearish signals are present in text.
    - "unknown" means insufficient evidence to classify.

    Formula (text heuristic):
    - bull_signals: count of bullish keywords in combined text
    - bear_signals: count of bearish keywords in combined text
      (keywords chosen to avoid false positives — e.g. bare "risk" excluded
       because it's too common in neutral/cautious commentary)
    - If both ≥ 1 → "mixed"
    - If one side ≥ 1 and other == 0 → that side
    - Otherwise → "unknown"
    """
    # 1. Check metadata label/score first (most structured)
    meta = model_norm.get("metadata", {})
    label = meta.get("label")
    score = meta.get("score")

    label_class = _classify_label(label)
    if label_class in ("bullish", "bearish"):
        return label_class

    score_class = _classify_score(score)
    if score_class in ("bullish", "bearish"):
        return score_class

    # 2. Text-based heuristic on summary + actions
    text_parts = []
    summary = model_norm.get("summary") or ""
    if summary:
        text_parts.append(summary.lower())
    for action in (model_norm.get("actions") or []):
        if isinstance(action, str):
            text_parts.append(action.lower())

    combined = " ".join(text_parts)
    if not combined:
        return "unknown"

    # Bull keywords — specific enough to avoid false positives.
    # Word-boundary matching prevents "unfavorable" matching "favorable".
    _BULL_TEXT = (
        "bullish", "supportive", "favorable", "upside",
        "positive", "rally", "strength", "recovery",
    )
    # Bear keywords — "cautious"/"risk" excluded (too broad in model text).
    # "cautious" in model commentary typically = "proceed with care" not "go short".
    _BEAR_TEXT = (
        "bearish", "unfavorable", "downside", "negative",
        "decline", "recession", "sell-off", "selloff",
        "deteriorating", "weakness",
    )

    bull_signals = sum(1 for kw in _BULL_TEXT if _re.search(r"\b" + kw + r"\b", combined))
    bear_signals = sum(1 for kw in _BEAR_TEXT if _re.search(r"\b" + kw + r"\b", combined))

    # Mixed: both directions present → "mixed" (not "unknown")
    if bull_signals >= 1 and bear_signals >= 1:
        return "mixed"
    if bull_signals >= 1:
        return "bullish"
    if bear_signals >= 1:
        return "bearish"

    return "unknown"


# ═════════════════════════════════════════════════════════════════════
# CONFLICT FAMILY 5: Quality / Degradation
# ═════════════════════════════════════════════════════════════════════

def _detect_quality_conflicts(
    market_ctx: dict,
    candidates: list[dict[str, Any]],
    model_analyses: dict,
    quality_summary: dict,
    freshness_summary: dict,
) -> list[dict[str, Any]]:
    """Detect quality/degradation issues that weaken signal alignment.

    Heuristics:
    1. Degraded modules appearing to support consensus — false confidence.
    2. Stale modules — freshness below threshold.
    3. Low-confidence modules — confidence undermining readability.
    4. Missing modules — gaps that weaken alignment interpretation.
    """
    conflicts: list[dict[str, Any]] = []

    # ── 1. Degraded modules masking disagreement ─────────────────────
    degraded_engines = []
    for eng_key, eng_payload in market_ctx.items():
        if _is_fallback(eng_payload):
            degraded_engines.append(eng_key)

    if degraded_engines and len(market_ctx) > 0:
        healthy_count = len(market_ctx) - len(degraded_engines)
        if healthy_count > 0 and len(degraded_engines) >= 2:
            severity = "moderate" if len(degraded_engines) >= 3 else "low"
            conflicts.append(_make_conflict(
                conflict_type="quality_degraded_consensus",
                severity=severity,
                title="Degraded modules may mask true market state",
                description=(
                    f"{len(degraded_engines)} of {len(market_ctx)} market module(s) "
                    f"are on fallback data, potentially masking disagreement."
                ),
                entities=degraded_engines,
                evidence={
                    "degraded_engines": degraded_engines,
                    "total_engines": len(market_ctx),
                    "healthy_engines": healthy_count,
                },
                confidence_impact="moderate",
            ))

    # ── 2. Stale modules ─────────────────────────────────────────────
    freshness_modules = freshness_summary.get("modules", {})
    for eng_key, fm in freshness_modules.items():
        status = fm.get("freshness_status", "unknown")
        if status in ("very_stale", "stale"):
            conflicts.append(_make_conflict(
                conflict_type="quality_stale_module",
                severity="low" if status == "stale" else "moderate",
                title=f"Stale data: {eng_key}",
                description=(
                    f"Module '{eng_key}' has freshness status '{status}'. "
                    f"Signals may not reflect current conditions."
                ),
                entities=[eng_key],
                evidence={
                    "freshness_status": status,
                    "last_update": fm.get("last_update"),
                },
                confidence_impact="minor" if status == "stale" else "moderate",
            ))

    # ── 3. Low-confidence modules ────────────────────────────────────
    quality_modules = quality_summary.get("modules", {})
    for eng_key, qm in quality_modules.items():
        conf = qm.get("confidence", 100)
        # Confidence in quality_summary is 0-100 scale for engines
        if conf < 40 and conf > 0:
            conflicts.append(_make_conflict(
                conflict_type="quality_low_confidence_module",
                severity="low",
                title=f"Low confidence: {eng_key}",
                description=(
                    f"Module '{eng_key}' has confidence {conf}, "
                    f"weakening its contribution to alignment assessment."
                ),
                entities=[eng_key],
                evidence={
                    "confidence": conf,
                    "data_quality_status": qm.get("data_quality_status"),
                    "signal_quality": qm.get("signal_quality"),
                },
                confidence_impact="minor",
            ))

    # ── 4. Missing modules (only if some are present) ────────────────
    # We can check assembled top-level for this, but we have quality_summary
    # which lists all present modules. The expected set is 6 engines.
    _EXPECTED_ENGINES = {
        "breadth_participation", "volatility_options", "cross_asset_macro",
        "flows_positioning", "liquidity_financial_conditions", "news_sentiment",
    }
    present_engines = set(market_ctx.keys())
    missing = _EXPECTED_ENGINES - present_engines
    if missing and present_engines:
        severity = "moderate" if len(missing) >= 3 else "low"
        conflicts.append(_make_conflict(
            conflict_type="quality_missing_modules",
            severity=severity,
            title="Missing market modules",
            description=(
                f"{len(missing)} of 6 expected market module(s) missing: "
                f"{', '.join(sorted(missing))}. Alignment assessment is incomplete."
            ),
            entities=sorted(missing),
            evidence={
                "missing_modules": sorted(missing),
                "present_modules": sorted(present_engines),
                "expected_count": len(_EXPECTED_ENGINES),
            },
            confidence_impact="moderate" if len(missing) >= 3 else "minor",
        ))

    return conflicts
