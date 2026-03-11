"""
Normalized Scanner Candidate Output Contract
==============================================

Defines the canonical output shape that ALL scanner families (stock and
options) must produce for downstream consumers (dashboard trade cards,
candidate comparison views, context assembly, portfolio-aware trade
review, policy checks, higher-order trade-decision orchestration).

The contract sits alongside (not replacing) the existing candidate dict
so frontends keep working unchanged.  Each scanner service calls
``normalize_candidate_output()`` after building its candidate list and
attaches the result under the ``normalized`` key of each candidate.

Pattern mirrors ``engine_output_contract.py`` (Prompt 1).

Contract fields
---------------
candidate_id        – unique identifier for this candidate
scanner_key         – stable machine identifier for the scanner
scanner_name        – human-readable display name for the scanner
strategy_family     – "stock" | "options"
setup_type          – specific setup/strategy label
asset_class         – "equity" | "option"
symbol              – uppercase ticker
underlying          – underlying ticker (same as symbol for stocks)
direction           – "long" | "short" | "neutral" | "mixed"
thesis_summary      – 1-2 sentence or bullet list describing the thesis
entry_context       – price/strike/spread context for entry
time_horizon        – from shared vocabulary (see app.utils.time_horizon)
setup_quality       – 0-100 composite quality score
confidence          – 0.0-1.0 data/source confidence
risk_definition     – structured risk parameters
reward_profile      – structured reward parameters
supporting_signals  – key bullish/confirming signals
risk_flags          – concerns or warning signals
invalidation_signals – conditions that would invalidate the setup
market_context_tags – machine-readable context tags
position_sizing_notes – sizing guidance (if available)
data_quality        – normalized quality summary
source_status       – data source availability / confidence
pricing_snapshot    – current pricing context
strategy_structure  – strategy-specific structure (legs for options, N/A for stocks)
candidate_metrics   – key computed numbers (differs by family)
detail_sections     – scanner-specific extras (score breakdown, enrichment metrics, etc.)
generated_at        – ISO 8601 timestamp when candidate was generated
"""

from __future__ import annotations

import logging
from typing import Any

from app.utils.time_horizon import resolve_scanner_horizon

_log = logging.getLogger("bentrade.scanner_candidate_contract")

# ── Scanner metadata ─────────────────────────────────────────────────

SCANNER_METADATA: dict[str, dict[str, str]] = {
    # Stock scanners
    "stock_pullback_swing": {
        "name": "Pullback Swing",
        "strategy_family": "stock",
        "asset_class": "equity",
        "setup_type": "pullback_swing",
        "direction": "long",
        "time_horizon": "swing",
    },
    "stock_momentum_breakout": {
        "name": "Momentum Breakout",
        "strategy_family": "stock",
        "asset_class": "equity",
        "setup_type": "momentum_breakout",
        "direction": "long",
        "time_horizon": "swing",
    },
    "stock_mean_reversion": {
        "name": "Mean Reversion",
        "strategy_family": "stock",
        "asset_class": "equity",
        "setup_type": "mean_reversion",
        "direction": "long",
        "time_horizon": "swing",
    },
    "stock_volatility_expansion": {
        "name": "Volatility Expansion",
        "strategy_family": "stock",
        "asset_class": "equity",
        "setup_type": "volatility_expansion",
        "direction": "long",
        "time_horizon": "swing",
    },
    # Options scanners
    "put_credit_spread": {
        "name": "Put Credit Spread",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "put_credit_spread",
        "direction": "short",
        "time_horizon": "days_to_expiry",
    },
    "call_credit_spread": {
        "name": "Call Credit Spread",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "call_credit_spread",
        "direction": "short",
        "time_horizon": "days_to_expiry",
    },
    "put_debit": {
        "name": "Put Debit Spread",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "put_debit",
        "direction": "long",
        "time_horizon": "days_to_expiry",
    },
    "call_debit": {
        "name": "Call Debit Spread",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "call_debit",
        "direction": "long",
        "time_horizon": "days_to_expiry",
    },
    "iron_condor": {
        "name": "Iron Condor",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "iron_condor",
        "direction": "neutral",
        "time_horizon": "days_to_expiry",
    },
    "butterfly_debit": {
        "name": "Debit Butterfly",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "butterfly_debit",
        "direction": "neutral",
        "time_horizon": "days_to_expiry",
    },
    "calendar_spread": {
        "name": "Calendar Spread",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "calendar_spread",
        "direction": "neutral",
        "time_horizon": "days_to_expiry",
    },
    "calendar_call_spread": {
        "name": "Call Calendar Spread",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "calendar_call_spread",
        "direction": "neutral",
        "time_horizon": "days_to_expiry",
    },
    "calendar_put_spread": {
        "name": "Put Calendar Spread",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "calendar_put_spread",
        "direction": "neutral",
        "time_horizon": "days_to_expiry",
    },
    "csp": {
        "name": "Cash Secured Put",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "csp",
        "direction": "short",
        "time_horizon": "days_to_expiry",
    },
    "covered_call": {
        "name": "Covered Call",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "covered_call",
        "direction": "short",
        "time_horizon": "days_to_expiry",
    },
    "income": {
        "name": "Income Strategy",
        "strategy_family": "options",
        "asset_class": "option",
        "setup_type": "income",
        "direction": "short",
        "time_horizon": "days_to_expiry",
    },
}

# ── Required contract fields (for validation in tests) ───────────────

REQUIRED_FIELDS: frozenset[str] = frozenset({
    "candidate_id",
    "scanner_key",
    "scanner_name",
    "strategy_family",
    "setup_type",
    "asset_class",
    "symbol",
    "underlying",
    "direction",
    "thesis_summary",
    "entry_context",
    "time_horizon",
    "setup_quality",
    "confidence",
    "risk_definition",
    "reward_profile",
    "supporting_signals",
    "risk_flags",
    "invalidation_signals",
    "market_context_tags",
    "position_sizing_notes",
    "data_quality",
    "source_status",
    "pricing_snapshot",
    "strategy_structure",
    "candidate_metrics",
    "detail_sections",
    "generated_at",
})


# ── Helpers ──────────────────────────────────────────────────────────

def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


# ── Public API ───────────────────────────────────────────────────────

def normalize_candidate_output(
    scanner_key: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Convert a raw candidate dict into the normalized contract shape.

    Parameters
    ----------
    scanner_key:
        Identifier from SCANNER_METADATA (e.g. ``"stock_pullback_swing"``)
        or a canonical options strategy_id (e.g. ``"put_credit_spread"``).
    candidate:
        The raw candidate dict produced by a scanner service or options
        strategy plugin (post-normalize_trade for options).

    Returns
    -------
    dict  The normalized candidate contract dict (attached as
          ``candidate["normalized"]`` by the calling service).
    """
    meta = SCANNER_METADATA.get(scanner_key, {})
    strategy_family = meta.get("strategy_family", _infer_family(scanner_key, candidate))

    if strategy_family == "stock":
        return _normalize_stock_candidate(scanner_key, candidate, meta)
    return _normalize_options_candidate(scanner_key, candidate, meta)


# ── Stock candidate normalization ────────────────────────────────────

def _normalize_stock_candidate(
    scanner_key: str,
    candidate: dict[str, Any],
    meta: dict[str, str],
) -> dict[str, Any]:
    """Normalize a stock scanner candidate into the contract shape.

    Maps from the stock scanner's flat dict with composite_score, metrics,
    score_breakdown, thesis, data_source into the canonical contract fields.
    """
    symbol = _safe_str(candidate.get("symbol"), "UNKNOWN").upper()
    price = _to_float(candidate.get("price"))
    composite = _to_float(candidate.get("composite_score"))
    data_source = candidate.get("data_source") or {}
    metrics = candidate.get("metrics") or {}
    score_breakdown = candidate.get("score_breakdown") or {}
    thesis = candidate.get("thesis") or []

    # State field varies by scanner
    state_field = _get_state_field(scanner_key)
    state_value = _safe_str(candidate.get(state_field), "unknown")

    # Confidence: use data_source.confidence, or top-level confidence
    confidence = _to_float(candidate.get("confidence"))
    if confidence is None:
        confidence = _to_float(data_source.get("confidence"))

    # Build supporting signals from thesis bullets
    supporting_signals = list(thesis) if isinstance(thesis, list) else []

    # Risk flags from risk_notes (always [] on stock scanners currently)
    risk_notes = candidate.get("risk_notes") or []

    # Invalidation: ATR-based or state-based
    invalidation = []
    atr_pct = _to_float(metrics.get("atr_pct"))
    if atr_pct is not None and atr_pct > 0.05:
        invalidation.append(f"High volatility (ATR%={atr_pct:.1%})")
    if state_value in ("unknown", "no_trend", "no_breakout", "no_reversion", "no_expansion"):
        invalidation.append(f"Setup state not confirmed: {state_value}")

    # Market context tags
    context_tags = [scanner_key, state_value]
    if composite is not None and composite >= 70:
        context_tags.append("high_quality_setup")
    elif composite is not None and composite >= 50:
        context_tags.append("moderate_setup")

    return {
        "candidate_id": _safe_str(candidate.get("trade_key"), f"{symbol}|{scanner_key}"),
        "scanner_key": scanner_key,
        "scanner_name": meta.get("name", scanner_key),
        "strategy_family": "stock",
        "setup_type": meta.get("setup_type", scanner_key),
        "asset_class": "equity",
        "symbol": symbol,
        "underlying": symbol,
        "direction": meta.get("direction", "long"),
        "thesis_summary": thesis if isinstance(thesis, list) else [str(thesis)],
        "entry_context": {
            "price": price,
            "entry_reference": _to_float(candidate.get("entry_reference")),
            "state": state_value,
        },
        "time_horizon": resolve_scanner_horizon(scanner_key, "stock"),
        "setup_quality": round(composite, 1) if composite is not None else None,
        "confidence": round(confidence, 2) if confidence is not None else None,
        "risk_definition": {
            "type": "stop_loss_based",
            "notes": risk_notes,
        },
        "reward_profile": {
            "type": "price_target_based",
            "composite_score": round(composite, 1) if composite is not None else None,
        },
        "supporting_signals": supporting_signals,
        "risk_flags": risk_notes,
        "invalidation_signals": invalidation,
        "market_context_tags": context_tags,
        "position_sizing_notes": None,
        "data_quality": {
            "source": _safe_str(data_source.get("history"), "unknown"),
            "source_confidence": confidence,
            "missing_fields": [],
        },
        "source_status": {
            "history": _safe_str(data_source.get("history"), "unknown"),
            "confidence": confidence,
        },
        "pricing_snapshot": {
            "price": price,
            "underlying_price": _to_float(candidate.get("underlying_price")),
        },
        "strategy_structure": None,  # No legs/structure for stock candidates
        "candidate_metrics": {
            "composite_score": round(composite, 1) if composite is not None else None,
            "score_breakdown": {k: round(v, 1) if isinstance(v, (int, float)) else v
                                for k, v in score_breakdown.items()},
            **{k: (round(v, 4) if isinstance(v, float) else v)
               for k, v in metrics.items()},
        },
        "detail_sections": _extract_stock_detail_sections(scanner_key, candidate),
        "generated_at": _safe_str(candidate.get("as_of")),
    }


def _extract_stock_detail_sections(
    scanner_key: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Pull scanner-specific extras into detail_sections."""
    sections: dict[str, Any] = {}

    # Strategy-specific sub-score fields
    score_fields = _get_score_fields(scanner_key)
    sub_scores = {}
    for field in score_fields:
        val = _to_float(candidate.get(field))
        if val is not None:
            sub_scores[field] = round(val, 1)
    if sub_scores:
        sections["sub_scores"] = sub_scores

    # State
    state_field = _get_state_field(scanner_key)
    state_val = candidate.get(state_field)
    if state_val is not None:
        sections["state"] = {state_field: state_val}

    # Rank if present
    rank = candidate.get("rank")
    if rank is not None:
        sections["rank"] = rank

    return sections


# ── Options confidence derivation ────────────────────────────────────

# Key computed fields whose presence signals data completeness.
_OPTIONS_CONFIDENCE_FIELDS: tuple[str, ...] = (
    "max_profit", "max_loss", "pop", "expected_value",
    "return_on_risk", "iv_rank", "bid_ask_pct",
)


def _derive_options_confidence(
    computed: dict[str, Any],
    metrics_status: dict[str, Any],
    validation_warnings: list[str],
    legs: list[dict[str, Any]],
    pricing: dict[str, Any],
) -> float:
    """Derive confidence (0.0–1.0) from the quality and completeness
    of the candidate's observable setup attributes.

    This is NOT a quality score for the trade itself (that is setup_quality
    from rank_score).  It answers: "How well-defined and internally
    consistent is this candidate's data?"

    Factors and weights:
        data_completeness  (0.30) — fraction of 7 key computed fields present
        risk_clarity       (0.25) — max_loss + pop + max_profit all defined
        structure_quality  (0.25) — legs present, pricing available, spread tight
        consistency        (0.20) — low warnings, metrics ready, no EV/POP mismatch

    Formula:
        confidence = Σ(weight_i × factor_i), clamped to [0.0, 1.0]
    """
    # Factor 1: data_completeness — how many key metrics exist?
    present = sum(
        1 for f in _OPTIONS_CONFIDENCE_FIELDS if _to_float(computed.get(f)) is not None
    )
    data_completeness = present / len(_OPTIONS_CONFIDENCE_FIELDS)

    # Factor 2: risk_clarity — are the risk/reward anchors defined?
    max_loss = _to_float(computed.get("max_loss"))
    pop = _to_float(computed.get("pop"))
    max_profit = _to_float(computed.get("max_profit"))

    risk_sub = 0.0
    if max_loss is not None:
        risk_sub += 0.40
    if pop is not None and 0.0 <= pop <= 1.0:
        risk_sub += 0.35
    if max_profit is not None:
        risk_sub += 0.25
    risk_clarity = min(risk_sub, 1.0)

    # Factor 3: structure_quality — legs + pricing + spread tightness
    struct_sub = 0.0
    if legs:
        struct_sub += 0.40
    if _to_float(pricing.get("spread_mid")) is not None:
        struct_sub += 0.30
    bid_ask_pct = _to_float(computed.get("bid_ask_pct"))
    if bid_ask_pct is not None:
        # tight ≤ 0.10 → full credit; wide ≥ 0.30 → floor at 0.10
        tightness = max(0.10, 1.0 - (bid_ask_pct - 0.10) / 0.20) if bid_ask_pct > 0.10 else 1.0
        struct_sub += 0.30 * min(tightness, 1.0)
    else:
        struct_sub += 0.10  # can't tell → small credit
    structure_quality = min(struct_sub, 1.0)

    # Factor 4: consistency — warnings and signal alignment
    consistency = 1.0
    # metrics readiness
    if not metrics_status.get("ready", False):
        consistency -= 0.25
    # validation warnings penalty (−0.08 each, floor 0.20)
    consistency -= 0.08 * len(validation_warnings)
    # EV/POP directional consistency check
    ev = _to_float(computed.get("expected_value"))
    if ev is not None and pop is not None:
        if pop >= 0.50 and ev < 0:
            consistency -= 0.15  # POP favourable but EV negative → suspicious data
    consistency = max(consistency, 0.20)

    # Weighted sum
    score = (
        0.30 * data_completeness
        + 0.25 * risk_clarity
        + 0.25 * structure_quality
        + 0.20 * consistency
    )
    return round(max(0.0, min(1.0, score)), 2)


# ── Options candidate normalization ──────────────────────────────────

def _normalize_options_candidate(
    scanner_key: str,
    candidate: dict[str, Any],
    meta: dict[str, str],
) -> dict[str, Any]:
    """Normalize an options trade (post-normalize_trade) into the contract shape.

    Reads from the canonical computed/details/pills/pricing sub-dicts
    that normalize_trade() already built.
    """
    symbol = _safe_str(
        candidate.get("symbol")
        or candidate.get("underlying")
        or candidate.get("underlying_symbol"),
        "UNKNOWN",
    ).upper()

    computed = candidate.get("computed") or {}
    details = candidate.get("details") or {}
    pills = candidate.get("pills") or {}
    pricing = candidate.get("pricing") or {}
    metrics_status = candidate.get("metrics_status") or {}
    computed_metrics = candidate.get("computed_metrics") or {}
    legs = candidate.get("legs") or []
    validation_warnings = candidate.get("validation_warnings") or []

    # Setup quality: use composite_score (aliased from rank_score)
    composite = _to_float(candidate.get("composite_score"))
    if composite is None:
        composite = _to_float(candidate.get("rank_score"))

    # Confidence: multi-factor derivation from setup attribute quality.
    # See _derive_options_confidence() for factor breakdown.
    missing = metrics_status.get("missing_fields") or metrics_status.get("missing_required") or []
    confidence = _derive_options_confidence(
        computed, metrics_status, validation_warnings, legs, pricing,
    )

    # Risk definition
    max_loss = _to_float(computed.get("max_loss"))
    pop = _to_float(computed.get("pop"))
    risk_def = {
        "type": "defined_risk_spread",
        "max_loss_per_contract": max_loss,
        "pop": round(pop, 4) if pop is not None else None,
    }

    # Reward profile
    max_profit = _to_float(computed.get("max_profit"))
    ev = _to_float(computed.get("expected_value"))
    ror = _to_float(computed.get("return_on_risk"))
    reward = {
        "type": "defined_reward_spread",
        "max_profit_per_contract": max_profit,
        "expected_value_per_contract": ev,
        "return_on_risk": ror,
    }

    # Supporting signals
    signals: list[str] = []
    if pop is not None and pop >= 0.65:
        signals.append(f"POP={pop:.0%}")
    if ev is not None and ev > 0:
        signals.append(f"EV=${ev:.2f}/contract")
    kelly = _to_float(computed.get("kelly_fraction"))
    if kelly is not None and kelly > 0:
        signals.append(f"Kelly={kelly:.2%}")
    iv_rank = _to_float(computed.get("iv_rank"))
    if iv_rank is not None and iv_rank > 30:
        signals.append(f"IV Rank={iv_rank:.0f}")

    # Risk flags from validation_warnings
    risk_flags = list(validation_warnings)

    # Invalidation signals
    invalidation: list[str] = []
    if pop is not None and pop < 0.50:
        invalidation.append(f"Low POP ({pop:.0%})")
    if ev is not None and ev < 0:
        invalidation.append(f"Negative EV (${ev:.2f})")
    bid_ask_pct = _to_float(computed.get("bid_ask_pct"))
    if bid_ask_pct is not None and bid_ask_pct > 0.15:
        invalidation.append(f"Wide bid-ask spread ({bid_ask_pct:.1%})")

    # Engine gate as additional risk info
    gate = candidate.get("engine_gate_status") or {}
    if not gate.get("passed", True):
        for reason in gate.get("failed_reasons", []):
            risk_flags.append(f"Gate: {reason}")

    # Market context tags
    context_tags = [scanner_key]
    regime = _safe_str(details.get("market_regime"))
    if regime:
        context_tags.append(regime)
    dte = _to_float(details.get("dte"))
    if dte is not None:
        if dte <= 7:
            context_tags.append("short_dte")
        elif dte <= 30:
            context_tags.append("medium_dte")
        else:
            context_tags.append("long_dte")

    # Strategy structure (legs)
    strategy_structure = None
    if legs:
        strategy_structure = {
            "legs": legs,
            "short_strike": candidate.get("short_strike"),
            "long_strike": candidate.get("long_strike"),
            "expiration": candidate.get("expiration"),
            "dte": candidate.get("dte"),
        }

    return {
        "candidate_id": _safe_str(
            candidate.get("trade_key") or candidate.get("trade_id"),
            f"{symbol}|{scanner_key}",
        ),
        "scanner_key": scanner_key,
        "scanner_name": meta.get("name", _safe_str(pills.get("strategy_label"), scanner_key)),
        "strategy_family": "options",
        "setup_type": meta.get("setup_type", scanner_key),
        "asset_class": "option",
        "symbol": symbol,
        "underlying": symbol,
        "direction": meta.get("direction", _infer_direction(scanner_key)),
        "thesis_summary": _build_options_thesis(candidate, computed, details),
        "entry_context": {
            "spread_mid": _to_float(pricing.get("spread_mid")),
            "spread_natural": _to_float(pricing.get("spread_natural")),
            "spread_mark": _to_float(pricing.get("spread_mark")),
            "short_strike": candidate.get("short_strike"),
            "long_strike": candidate.get("long_strike"),
            "expiration": candidate.get("expiration"),
            "dte": candidate.get("dte"),
        },
        "time_horizon": resolve_scanner_horizon(scanner_key, "options"),
        "setup_quality": round(composite, 1) if composite is not None else None,
        "confidence": round(confidence, 2),
        "risk_definition": risk_def,
        "reward_profile": reward,
        "supporting_signals": signals,
        "risk_flags": risk_flags,
        "invalidation_signals": invalidation,
        "market_context_tags": context_tags,
        "position_sizing_notes": None,
        "data_quality": {
            "metrics_ready": metrics_status.get("ready", False),
            "missing_fields": list(missing),
            "warning_count": len(validation_warnings),
        },
        "source_status": {
            "metrics_ready": metrics_status.get("ready", False),
            "validation_warnings": validation_warnings,
        },
        "pricing_snapshot": {
            "spread_mid": _to_float(pricing.get("spread_mid")),
            "spread_natural": _to_float(pricing.get("spread_natural")),
            "spread_mark": _to_float(pricing.get("spread_mark")),
            "underlying_price": _to_float(candidate.get("underlying_price"))
                                or _to_float(candidate.get("price")),
        },
        "strategy_structure": strategy_structure,
        "candidate_metrics": {
            "composite_score": round(composite, 1) if composite is not None else None,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "pop": pop,
            "expected_value": ev,
            "return_on_risk": ror,
            "kelly_fraction": kelly,
            "iv_rank": iv_rank,
            "ev_to_risk": _to_float(computed.get("ev_to_risk")),
            "bid_ask_pct": bid_ask_pct,
            "break_even": _to_float(details.get("break_even")),
        },
        "detail_sections": _extract_options_detail_sections(candidate, computed, details, computed_metrics),
        "generated_at": _safe_str(candidate.get("as_of")),
    }


def _build_options_thesis(
    candidate: dict[str, Any],
    computed: dict[str, Any],
    details: dict[str, Any],
) -> list[str]:
    """Build thesis bullets for an options candidate."""
    bullets: list[str] = []
    strategy_label = _safe_str(
        candidate.get("pills", {}).get("strategy_label")
        or candidate.get("strategy_id"),
        "Trade",
    )
    symbol = _safe_str(candidate.get("symbol"), "?")

    pop = _to_float(computed.get("pop"))
    dte = _to_float(details.get("dte"))
    max_profit = _to_float(computed.get("max_profit"))

    bullets.append(f"{strategy_label} on {symbol}")
    if pop is not None:
        bullets.append(f"POP: {pop:.0%}")
    if dte is not None:
        bullets.append(f"DTE: {int(dte)}")
    if max_profit is not None:
        bullets.append(f"Max profit: ${max_profit:.2f}/contract")

    return bullets


def _extract_options_detail_sections(
    candidate: dict[str, Any],
    computed: dict[str, Any],
    details: dict[str, Any],
    computed_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Pull options-specific extras into detail_sections."""
    sections: dict[str, Any] = {}

    # Tie breaks from scoring
    tie_breaks = candidate.get("tie_breaks")
    if tie_breaks:
        sections["tie_breaks"] = tie_breaks

    # Computed metrics (full set from apply_metrics_contract)
    if computed_metrics:
        sections["computed_metrics"] = computed_metrics

    # Engine gate status
    gate = candidate.get("engine_gate_status")
    if gate:
        sections["engine_gate_status"] = gate

    # Pills (pre-formatted UI badges)
    pills = candidate.get("pills")
    if pills:
        sections["pills"] = pills

    return sections


# ── Private helpers ──────────────────────────────────────────────────

def _infer_family(scanner_key: str, candidate: dict[str, Any]) -> str:
    """Infer strategy family from scanner key or candidate shape."""
    if scanner_key.startswith("stock_"):
        return "stock"
    if candidate.get("trade_type") == "stock_long":
        return "stock"
    if candidate.get("legs") or candidate.get("computed"):
        return "options"
    return "options"


def _infer_direction(scanner_key: str) -> str:
    """Infer direction for unknown options strategies."""
    key = scanner_key.lower()
    if "credit" in key or "csp" in key or "covered_call" in key or "income" in key:
        return "short"
    if "debit" in key or "long_call" in key or "long_put" in key:
        return "long"
    if "condor" in key or "butterfly" in key or "calendar" in key:
        return "neutral"
    return "mixed"


_STATE_FIELDS: dict[str, str] = {
    "stock_pullback_swing": "trend_state",
    "stock_momentum_breakout": "breakout_state",
    "stock_mean_reversion": "reversion_state",
    "stock_volatility_expansion": "expansion_state",
}


def _get_state_field(scanner_key: str) -> str:
    return _STATE_FIELDS.get(scanner_key, "state")


_SCORE_FIELDS: dict[str, list[str]] = {
    "stock_pullback_swing": ["trend_score", "pullback_score", "reset_score", "liquidity_score"],
    "stock_momentum_breakout": ["breakout_score", "volume_score", "trend_score", "base_quality_score"],
    "stock_mean_reversion": ["oversold_score", "stabilization_score", "room_score", "liquidity_score"],
    "stock_volatility_expansion": ["expansion_score", "compression_score", "confirmation_score", "risk_score"],
}


def _get_score_fields(scanner_key: str) -> list[str]:
    return _SCORE_FIELDS.get(scanner_key, [])
