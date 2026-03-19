"""Market Picture Contract — shared normalisation for per-engine overview cards.

Single source of truth for:
  • ENGINE_DISPLAY — stable display-name ordering
  • normalize_engine_card() — deterministic card builder
  • Status semantics:
      engine_status : "ok" | "missing" | "degraded"
      model_status  : "fresh" | "stale" | "missing"

The scoreboard route, history builder, and any future consumer
import from here rather than assembling cards ad hoc.
"""

from __future__ import annotations

from typing import Any

# ── Stable engine ordering for UI ──
ENGINE_DISPLAY: list[tuple[str, str]] = [
    ("breadth_participation", "Breadth & Participation"),
    ("volatility_options", "Volatility & Options"),
    ("cross_asset_macro", "Cross-Asset Macro"),
    ("flows_positioning", "Flows & Positioning"),
    ("liquidity_financial_conditions", "Liquidity & Financial Conditions"),
    ("news_sentiment", "News & Sentiment"),
]

# Recognised engine_status values — anything else maps to "degraded".
_KNOWN_ENGINE_STATUSES = {"ok", "missing", "degraded"}


def _resolve_engine_status(raw: str | None) -> str:
    """Normalise an engine_status string to a known value."""
    if raw is None:
        return "ok"
    val = str(raw).strip().lower()
    return val if val in _KNOWN_ENGINE_STATUSES else "degraded"


def _resolve_model_status(model_score: Any, is_fresh: bool) -> str:
    """Derive model_status from score presence and freshness.

    Returns: "fresh" | "stale" | "missing"
    """
    if model_score is None:
        return "missing"
    return "fresh" if is_fresh else "stale"


def normalize_engine_card(
    key: str,
    display_name: str,
    engine_data: dict[str, Any] | None,
    model_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a single normalised engine card.

    Parameters
    ----------
    key : str
        Engine key (e.g. "breadth_participation").
    display_name : str
        Human-readable engine name.
    engine_data : dict | None
        Raw engine dict from the market-state artifact, or None if the
        engine did not run / is absent.
    model_entry : dict | None
        Entry from load_all_scores() for this engine, or None if no
        durable model score exists.

    Returns
    -------
    dict  — card with fields:
        key, name,
        engine_score, engine_label, engine_summary,
        model_score, model_label, model_summary,
        model_captured_at, model_fresh,
        confidence,
        engine_status, model_status, status (legacy alias for engine_status)
    """
    # ── Engine fields ──
    has_engine = engine_data is not None and isinstance(engine_data, dict)
    engine_score = engine_data.get("score") if has_engine else None
    engine_label = (engine_data.get("short_label") or engine_data.get("label")) if has_engine else None
    engine_summary = engine_data.get("summary") if has_engine else None
    confidence = engine_data.get("confidence") if has_engine else None

    raw_engine_status = engine_data.get("engine_status") if has_engine else None
    engine_status = "missing" if not has_engine else _resolve_engine_status(raw_engine_status)

    # Degraded reasons from engine normalization (for traceable UI badges)
    status_detail = engine_data.get("status_detail") if has_engine else None
    degraded_reasons = (status_detail.get("degraded_reasons") or []) if isinstance(status_detail, dict) else []

    # ── Model fields ──
    model_score = model_entry.get("model_score") if model_entry else None
    model_label = model_entry.get("model_label") if model_entry else None
    model_summary = model_entry.get("model_summary") if model_entry else None
    model_captured_at = model_entry.get("captured_at") if model_entry else None
    is_fresh = model_entry.get("is_fresh", False) if model_entry else False

    model_status = _resolve_model_status(model_score, is_fresh)

    return {
        "key": key,
        "name": display_name,
        # Engine
        "engine_score": engine_score,
        "engine_label": engine_label,
        "engine_summary": engine_summary,
        # Model
        "model_score": model_score,
        "model_label": model_label,
        "model_summary": model_summary,
        "model_captured_at": model_captured_at,
        "model_fresh": model_status == "fresh",
        # Meta
        "confidence": confidence,
        "engine_status": engine_status,
        "model_status": model_status,
        "degraded_reasons": degraded_reasons,
        # Legacy: the original routes exposed a bare "status" field
        # holding the engine status.  Kept for backward compat.
        "status": engine_status,
    }


def build_engine_cards(
    raw_engines: dict[str, Any],
    all_model_scores: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the full ordered list of normalised engine cards.

    Parameters
    ----------
    raw_engines : dict
        artifact["engines"] mapping from the market-state artifact.
    all_model_scores : dict
        Output of load_all_scores() — keyed by engine_key.

    Returns
    -------
    list[dict] — one card per ENGINE_DISPLAY entry, in stable order.
    """
    # ── Temporary backward-compat alias (added 2026-03-18) ──────────────
    # Before the key alignment fix, model scores were persisted under
    # "liquidity_conditions" instead of the canonical
    # "liquidity_financial_conditions".  Existing model_scores_latest.json
    # files may still contain the old key.  This alias resolves it.
    #
    # REMOVAL PLAN: safe to delete once all deployed instances have run
    # at least one full model-analysis cycle (writes canonical key).
    # After removal, delete the related test_model_score_alias_lookup
    # test in test_market_picture_contract.py.
    # ──────────────────────────────────────────────────────────────────────
    _MODEL_KEY_ALIASES: dict[str, str] = {
        "liquidity_financial_conditions": "liquidity_conditions",
    }

    def _get_model_entry(key: str) -> dict[str, Any] | None:
        entry = all_model_scores.get(key)
        if entry is None:
            alias = _MODEL_KEY_ALIASES.get(key)
            if alias:
                entry = all_model_scores.get(alias)
        return entry

    return [
        normalize_engine_card(
            key=key,
            display_name=display_name,
            engine_data=raw_engines.get(key),
            model_entry=_get_model_entry(key),
        )
        for key, display_name in ENGINE_DISPLAY
    ]
