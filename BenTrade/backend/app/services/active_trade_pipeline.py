"""Active Trade Pipeline v1 — Analyse open positions and produce recommendations.

Workflow
────────
  1. Ingest active trades from broker (Tradier positions via routes_active_trades helpers)
  2. Normalize each trade into a stable analysis packet
  3. Attach market context (regime, VIX, indicators)
  4. Attach existing monitor evaluation (score/triggers/status)
  5. Run internal deterministic analysis engine (trade health, risk flags)
  6. Run model/prompt reasoning layer using the SAME raw reassessment packet
  7. Combine engine + model outputs into a normalized recommendation contract

Design principles
─────────────────
  - Contract-driven: every run produces a stable output shape.
  - Inspectable: engine metrics, model reasoning, and degradation are all explicit.
  - Reuses existing services (ActiveTradeMonitorService, RegimeService, model_router).
  - Honest degradation: missing data → degraded_reasons, not fake values.
  - Engine and model see the SAME reassessment packet — complementary, not contradictory.

Public API
──────────
    run_active_trade_pipeline(trades, monitor_service, regime_service,
                              base_data_service, *, model_executor=None)
        Main entry point — returns an ActiveTradePipelineResult dict.

    build_reassessment_packet(trade, market_context, monitor_result, indicators)
        Build the raw packet that both engine and model consume.

    run_analysis_engine(packet)
        Deterministic engine — returns structured metrics, flags, scores.

    run_model_analysis(packet, engine_output, *, model_executor)
        Model reasoning — returns recommendation, rationale, supporting points.

    normalize_recommendation(trade, engine_output, model_output, packet)
        Combine engine + model into the final normalized recommendation.

Recommendation vocabulary
─────────────────────────
    HOLD          — position is healthy, continue holding
    REDUCE        — warning signs, consider trimming
    CLOSE         — deteriorated, exit recommended
    URGENT_REVIEW — critical condition, review immediately
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("bentrade.active_trade_pipeline")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "active_trade_pipeline"
_PIPELINE_VERSION = "1.0"

# ── Recommendation vocabulary ──────────────────────────────────
RECOMMENDATION_HOLD = "HOLD"
RECOMMENDATION_REDUCE = "REDUCE"
RECOMMENDATION_CLOSE = "CLOSE"
RECOMMENDATION_URGENT_REVIEW = "URGENT_REVIEW"

VALID_RECOMMENDATIONS = frozenset({
    RECOMMENDATION_HOLD,
    RECOMMENDATION_REDUCE,
    RECOMMENDATION_CLOSE,
    RECOMMENDATION_URGENT_REVIEW,
})

# ── Engine scoring weights (v1, tunable) ───────────────────────
# Each component produces a 0–100 sub-score.  Final trade_health_score
# is weighted average of all non-None components.
ENGINE_WEIGHTS: dict[str, float] = {
    "pnl_health": 0.25,
    "time_pressure": 0.15,
    "market_alignment": 0.20,
    "structure_health": 0.15,
    "monitor_alignment": 0.15,
    "event_risk": 0.10,
}

# ── Engine recommendation thresholds ───────────────────────────
#   trade_health_score >= 70 → HOLD
#   trade_health_score >= 45 → REDUCE
#   trade_health_score >= 25 → CLOSE
#   trade_health_score < 25  → URGENT_REVIEW
ENGINE_THRESHOLDS: list[tuple[int, str]] = [
    (70, RECOMMENDATION_HOLD),
    (45, RECOMMENDATION_REDUCE),
    (25, RECOMMENDATION_CLOSE),
    (0, RECOMMENDATION_URGENT_REVIEW),
]

# ── Pipeline stage order and dependency graph ──────────────────
# Canonical ordered list — execution MUST follow this sequence.
ATP_STAGES: tuple[str, ...] = (
    "load_positions",
    "market_context",
    "build_packets",
    "engine_analysis",
    "model_analysis",
    "normalize",
    "complete",
)

# Dependency map: stage → set of stages that MUST be completed first.
# Derived from real data contracts:
#   load_positions   → (none) — only needs the input trades list
#   market_context   → (none) — only needs regime_service (external)
#   build_packets    → {load_positions, market_context} — needs trades + regime context
#   engine_analysis  → {build_packets} — needs assembled packets
#   model_analysis   → {build_packets, engine_analysis} — needs packets + engine output
#   normalize        → {engine_analysis, model_analysis} — combines both outputs
#   complete         → {normalize} — finalizes from recommendations list
ATP_DEPENDENCY_MAP: dict[str, set[str]] = {
    "load_positions":  set(),
    "market_context":  set(),
    "build_packets":   {"load_positions", "market_context"},
    "engine_analysis": {"build_packets"},
    "model_analysis":  {"build_packets", "engine_analysis"},
    "normalize":       {"engine_analysis", "model_analysis"},
    "complete":        {"normalize"},
}


def _check_dependencies(
    stage_key: str,
    stages: dict[str, Any],
) -> list[str]:
    """Return list of unsatisfied dependencies for *stage_key*.

    A dependency is satisfied when its status is 'completed' or 'skipped'.
    Returns an empty list when all prerequisites are met.
    """
    required = ATP_DEPENDENCY_MAP.get(stage_key, set())
    unsatisfied: list[str] = []
    for dep in sorted(required):
        dep_entry = stages.get(dep)
        if dep_entry is None or dep_entry.get("status") not in ("completed", "skipped"):
            unsatisfied.append(dep)
    return unsatisfied


# =====================================================================
#  Reassessment packet builder
# =====================================================================

def build_reassessment_packet(
    trade: dict[str, Any],
    market_context: dict[str, Any],
    monitor_result: dict[str, Any] | None,
    indicators: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the raw reassessment packet consumed by BOTH engine and model.

    This is the single source of truth for trade analysis inputs.
    Neither the engine nor the model should access data outside this packet.

    Input fields:
        trade         — normalized active trade from routes_active_trades
        market_context — regime_label, regime_score, vix, etc.
        monitor_result — existing monitor evaluation from ActiveTradeMonitorService
        indicators    — sma20, sma50, rsi14 for the trade's underlying

    Output:
        dict with sections: identity, position, market, monitor, indicators, data_quality
    """
    symbol = str(trade.get("symbol") or "???").upper()
    strategy = trade.get("strategy") or trade.get("strategy_id") or "unknown"
    dte = trade.get("dte")

    # ── Position context ────────────────────────────────────────
    avg_open = _to_float(trade.get("avg_open_price"))
    mark = _to_float(trade.get("mark_price"))
    unrealized = _to_float(trade.get("unrealized_pnl"))
    unrealized_pct = _to_float(trade.get("unrealized_pnl_pct"))

    # Derive P&L pct if not provided
    # Formula: unrealized_pnl_pct = unrealized_pnl / |cost_basis|
    if unrealized_pct is None and unrealized is not None:
        cost_basis = _to_float(trade.get("cost_basis_total"))
        if cost_basis is not None and abs(cost_basis) > 0:
            unrealized_pct = unrealized / abs(cost_basis)

    # ── Data quality tracking ───────────────────────────────────
    degraded: list[str] = []
    if avg_open is None:
        degraded.append("avg_open_price")
    if mark is None:
        degraded.append("mark_price")
    if unrealized is None:
        degraded.append("unrealized_pnl")
    if dte is None:
        degraded.append("dte")
    if not market_context:
        degraded.append("market_context")
    if market_context and market_context.get("regime_label") is None:
        degraded.append("regime_label")
    if not indicators:
        degraded.append("indicators")
    if monitor_result is None:
        degraded.append("monitor_result")

    # ── Build packet ────────────────────────────────────────────
    return {
        "packet_version": _PIPELINE_VERSION,
        "symbol": symbol,
        "identity": {
            "trade_key": trade.get("trade_key"),
            "trade_id": trade.get("trade_id"),
            "symbol": symbol,
            "strategy": strategy,
            "strategy_id": trade.get("strategy_id") or strategy,
            "spread_type": trade.get("spread_type"),
            "short_strike": _to_float(trade.get("short_strike")),
            "long_strike": _to_float(trade.get("long_strike")),
            "expiration": trade.get("expiration"),
            "dte": dte,
            "quantity": _to_int(trade.get("quantity")),
            "legs": trade.get("legs") or [],
            "trade_status": trade.get("status") or "OPEN",
        },
        "position": {
            "avg_open_price": avg_open,
            "mark_price": mark,
            "unrealized_pnl": unrealized,
            "unrealized_pnl_pct": unrealized_pct,
            "cost_basis_total": _to_float(trade.get("cost_basis_total")),
            "market_value": _to_float(trade.get("market_value")),
            "day_change": _to_float(trade.get("day_change")),
            "day_change_pct": _to_float(trade.get("day_change_pct")),
        },
        "market": {
            "regime_label": (market_context or {}).get("regime_label"),
            "regime_score": _to_float((market_context or {}).get("regime_score")),
            "vix": _to_float((market_context or {}).get("vix")),
        },
        "monitor": {
            "status": (monitor_result or {}).get("status"),
            "score_0_100": _to_int((monitor_result or {}).get("score_0_100")),
            "breakdown": (monitor_result or {}).get("breakdown"),
            "triggers": (monitor_result or {}).get("triggers") or [],
            "recommended_action": (monitor_result or {}).get("recommended_action"),
        },
        "indicators": {
            "sma20": _to_float((indicators or {}).get("sma20")),
            "sma50": _to_float((indicators or {}).get("sma50")),
            "rsi14": _to_float((indicators or {}).get("rsi14")),
        },
        "data_quality": {
            "degraded_fields": degraded,
            "is_degraded": len(degraded) > 0,
            "degraded_count": len(degraded),
        },
    }


# =====================================================================
#  Internal deterministic analysis engine
# =====================================================================

def run_analysis_engine(packet: dict[str, Any]) -> dict[str, Any]:
    """Deterministic analysis engine — evaluate trade health from the raw packet.

    Produces structured, inspectable metrics and scores.
    Operates purely on the reassessment packet — no external calls.

    Component scores (each 0–100):
        pnl_health       — Based on unrealized P&L %
        time_pressure    — Based on DTE (lower DTE → more pressure)
        market_alignment — Based on regime vs position direction
        structure_health — Based on width / strikes / structure integrity
        monitor_alignment — Reuses existing monitor score if available
        event_risk       — Based on DTE proximity to known risk windows

    Output:
        trade_health_score  — 0–100 weighted composite
        component_scores    — per-component breakdown
        risk_flags          — list of explicit risk conditions detected
        engine_recommendation — suggested action from deterministic rules
        urgency             — review_priority from engine logic
        degraded_flags     — what was missing during evaluation
    """
    identity = packet.get("identity") or {}
    position = packet.get("position") or {}
    market = packet.get("market") or {}
    monitor = packet.get("monitor") or {}
    indicators = packet.get("indicators") or {}
    data_quality = packet.get("data_quality") or {}

    component_scores: dict[str, float | None] = {}
    risk_flags: list[str] = []
    degraded_flags: list[str] = list(data_quality.get("degraded_fields") or [])

    # ── 1. P&L health (0–100) ───────────────────────────────────
    # Formula: maps unrealized_pnl_pct to a score.
    #   >= +10% → 95,  0% → 70,  -5% → 45,  -10% → 20,  <= -20% → 0
    pnl_pct = _to_float(position.get("unrealized_pnl_pct"))
    if pnl_pct is not None:
        if pnl_pct >= 0.10:
            pnl_score = 95.0
        elif pnl_pct >= 0.0:
            # Linear interpolation: 0% → 70, 10% → 95
            pnl_score = 70.0 + (pnl_pct / 0.10) * 25.0
        elif pnl_pct >= -0.05:
            # 0% → 70, -5% → 45
            pnl_score = 70.0 + (pnl_pct / 0.05) * 25.0
        elif pnl_pct >= -0.10:
            # -5% → 45, -10% → 20
            pnl_score = 45.0 + ((pnl_pct + 0.05) / 0.05) * 25.0
        elif pnl_pct >= -0.20:
            # -10% → 20, -20% → 0
            pnl_score = 20.0 + ((pnl_pct + 0.10) / 0.10) * 20.0
        else:
            pnl_score = 0.0
        component_scores["pnl_health"] = max(0.0, min(100.0, pnl_score))

        if pnl_pct <= -0.10:
            risk_flags.append("SIGNIFICANT_LOSS")
        if pnl_pct <= -0.20:
            risk_flags.append("SEVERE_LOSS")
        if pnl_pct >= 0.50:
            risk_flags.append("LARGE_UNREALIZED_GAIN")
    else:
        component_scores["pnl_health"] = None
        degraded_flags.append("pnl_health_missing")

    # ── 2. Time pressure (0–100) ────────────────────────────────
    # Formula: DTE-based.  Higher DTE → less pressure → higher score.
    #   >= 45 DTE → 90,  30 DTE → 75,  14 DTE → 50,  7 DTE → 25,
    #   3 DTE → 10,  0-1 DTE → 0
    dte = _to_int(identity.get("dte"))
    if dte is not None:
        if dte >= 45:
            time_score = 90.0
        elif dte >= 30:
            time_score = 75.0 + ((dte - 30) / 15.0) * 15.0
        elif dte >= 14:
            time_score = 50.0 + ((dte - 14) / 16.0) * 25.0
        elif dte >= 7:
            time_score = 25.0 + ((dte - 7) / 7.0) * 25.0
        elif dte >= 3:
            time_score = 10.0 + ((dte - 3) / 4.0) * 15.0
        else:
            time_score = max(0.0, float(dte) * 5.0)
        component_scores["time_pressure"] = max(0.0, min(100.0, time_score))

        if dte <= 3:
            risk_flags.append("EXPIRY_IMMINENT")
        elif dte <= 7:
            risk_flags.append("EXPIRY_NEAR")
    else:
        component_scores["time_pressure"] = None
        if "dte" not in degraded_flags:
            degraded_flags.append("time_pressure_missing")

    # ── 3. Market alignment (0–100) ─────────────────────────────
    # Formula: regime alignment with position direction.
    #   Credit spreads benefit from stable/risk-on; hurt by risk-off.
    #   Stock longs benefit from risk-on; hurt by risk-off.
    regime_label = market.get("regime_label")
    strategy = identity.get("strategy") or ""
    is_credit = "credit" in strategy.lower()
    is_put = "put" in strategy.lower()

    if regime_label:
        regime_upper = str(regime_label).upper()
        if regime_upper in ("RISK_ON", "BULLISH"):
            # Credit puts love risk-on, credit calls are neutral-ok
            if is_credit and is_put:
                market_score = 90.0
            elif is_credit:
                market_score = 65.0
            else:
                market_score = 80.0
        elif regime_upper in ("NEUTRAL", "MIXED"):
            market_score = 60.0
        elif regime_upper in ("RISK_OFF", "BEARISH"):
            if is_credit and is_put:
                market_score = 20.0
                risk_flags.append("REGIME_ADVERSE")
            elif is_credit:
                market_score = 70.0
            else:
                market_score = 30.0
                risk_flags.append("REGIME_ADVERSE")
        else:
            market_score = 50.0  # unknown regime
        component_scores["market_alignment"] = market_score
    else:
        component_scores["market_alignment"] = None
        if "regime_label" not in degraded_flags:
            degraded_flags.append("market_alignment_missing")

    # ── 4. Structure health (0–100) ─────────────────────────────
    # Formula: For spreads, checks width, mark vs entry, structure integrity.
    #   Full structure data → 80 base.
    #   Profitable mark → +10.  Adverse mark → -10.
    #   Width > 0 → +10.
    short_strike = _to_float(identity.get("short_strike"))
    long_strike = _to_float(identity.get("long_strike"))
    avg_open = _to_float(position.get("avg_open_price"))
    mark = _to_float(position.get("mark_price"))

    if short_strike is not None and long_strike is not None:
        struct_score = 80.0
        width = abs(short_strike - long_strike)
        if width > 0:
            struct_score += 10.0
        if avg_open is not None and mark is not None:
            # For credit spreads: mark < entry is good (spread decaying)
            if is_credit and mark < avg_open:
                struct_score += 10.0
            elif is_credit and mark > avg_open:
                struct_score -= 10.0
        component_scores["structure_health"] = max(0.0, min(100.0, struct_score))
    elif strategy == "single":
        # Single leg — no spread structure to evaluate
        component_scores["structure_health"] = 60.0
    else:
        component_scores["structure_health"] = None
        degraded_flags.append("structure_health_missing")

    # ── 5. Monitor alignment (0–100) ────────────────────────────
    # Formula: Reuse existing monitor score if available.
    monitor_score = _to_float(monitor.get("score_0_100"))
    if monitor_score is not None:
        component_scores["monitor_alignment"] = monitor_score
        # Carry forward critical triggers as risk flags
        triggers = monitor.get("triggers") or []
        for trigger in triggers:
            if isinstance(trigger, dict) and trigger.get("hit") and trigger.get("level") == "CRITICAL":
                flag = f"MONITOR_CRITICAL_{str(trigger.get('id', '')).upper()}"
                if flag not in risk_flags:
                    risk_flags.append(flag)
    else:
        component_scores["monitor_alignment"] = None
        if "monitor_result" not in degraded_flags:
            degraded_flags.append("monitor_alignment_missing")

    # ── 6. Event risk (0–100) ───────────────────────────────────
    # Formula: DTE-based proxy for event proximity.
    #   Very near expiry overlaps with earnings/events window.
    #   > 14 DTE → 80 (low event pressure).
    #   7-14 DTE → 60.  3-7 → 40.  < 3 → 20.
    if dte is not None:
        if dte > 14:
            event_score = 80.0
        elif dte > 7:
            event_score = 60.0
        elif dte > 3:
            event_score = 40.0
        else:
            event_score = 20.0
            if "EXPIRY_IMMINENT" not in risk_flags:
                risk_flags.append("EVENT_WINDOW_RISK")
        component_scores["event_risk"] = event_score
    else:
        component_scores["event_risk"] = None

    # ── Compute weighted composite ──────────────────────────────
    # Formula: trade_health_score = Σ(weight_i × score_i) / Σ(weight_i)
    #   Only non-None components participate.
    total_weight = 0.0
    weighted_sum = 0.0
    for key, weight in ENGINE_WEIGHTS.items():
        score = component_scores.get(key)
        if score is not None:
            weighted_sum += weight * score
            total_weight += weight

    if total_weight > 0:
        trade_health_score = int(round(weighted_sum / total_weight))
    else:
        trade_health_score = None

    # ── Engine recommendation ───────────────────────────────────
    engine_recommendation = None
    if trade_health_score is not None:
        for threshold, rec in ENGINE_THRESHOLDS:
            if trade_health_score >= threshold:
                engine_recommendation = rec
                break

    # Override: critical risk flags force escalation
    critical_count = sum(1 for f in risk_flags if "SEVERE" in f or "IMMINENT" in f)
    if critical_count >= 2 and engine_recommendation not in (
        RECOMMENDATION_CLOSE, RECOMMENDATION_URGENT_REVIEW,
    ):
        engine_recommendation = RECOMMENDATION_URGENT_REVIEW

    # ── Urgency / review priority ───────────────────────────────
    # Formula: 1 (low) to 5 (critical)
    if engine_recommendation == RECOMMENDATION_URGENT_REVIEW:
        urgency = 5
    elif engine_recommendation == RECOMMENDATION_CLOSE:
        urgency = 4
    elif engine_recommendation == RECOMMENDATION_REDUCE:
        urgency = 3
    elif len(risk_flags) >= 2:
        urgency = 3
    elif len(risk_flags) >= 1:
        urgency = 2
    else:
        urgency = 1

    return {
        "engine_version": _PIPELINE_VERSION,
        "trade_health_score": trade_health_score,
        "component_scores": component_scores,
        "risk_flags": risk_flags,
        "engine_recommendation": engine_recommendation,
        "urgency": urgency,
        "degraded_flags": degraded_flags,
    }


# =====================================================================
#  Model / prompt reasoning layer
# =====================================================================

# ── System prompt for active trade reassessment ─────────────────
_ACTIVE_TRADE_SYSTEM_PROMPT = """\
You are BenTrade's active trade reassessment engine.
You will receive a structured reassessment packet for an open options position.

The packet contains:
- Trade identity (symbol, strategy, strikes, expiration, DTE)
- Position state (P&L, entry vs current price)
- Market context (regime, VIX, indicators)
- Existing monitor evaluation (score, triggers, recommended action)
- Internal engine metrics (trade health score, risk flags, component scores)

Analyse the position and return ONLY valid JSON (no markdown, no commentary) with exactly these keys:
{
  "recommendation": "HOLD" | "REDUCE" | "CLOSE" | "URGENT_REVIEW",
  "conviction": <float 0.0 to 1.0>,
  "rationale_summary": "<2-4 sentence summary explaining why>",
  "key_supporting_points": ["<point1>", "<point2>", ...],
  "key_risks": ["<risk1>", "<risk2>", ...],
  "market_alignment": "<how current market conditions affect this position>",
  "portfolio_fit": "<whether this position still makes sense in context>",
  "event_sensitivity": "high" | "moderate" | "low" | "none",
  "suggested_next_move": "<specific actionable guidance>"
}

Rules:
- recommendation must be one of: HOLD, REDUCE, CLOSE, URGENT_REVIEW
- conviction must honestly reflect your certainty (0.0 = no confidence, 1.0 = maximum)
- rationale_summary should explain the WHY, not just restate the recommendation
- key_supporting_points: 2-5 concrete reasons supporting the recommendation
- key_risks: 1-4 specific risks to the position
- suggested_next_move: a practical, actionable step the trader should consider
- If data is limited, say so explicitly rather than guessing
- Do NOT wrap your response in markdown code fences
"""


# Type for model executor: (payload, rendered_text) -> dict
ModelExecutor = Callable[[dict[str, Any], str | None], dict[str, Any]]


def _render_reassessment_prompt(
    packet: dict[str, Any],
    engine_output: dict[str, Any],
) -> str:
    """Render the reassessment packet + engine output into prompt text.

    The model sees everything the engine saw, plus the engine's conclusions.
    """
    import json as _json
    prompt_data = {
        "trade_identity": packet.get("identity"),
        "position_state": packet.get("position"),
        "market_context": packet.get("market"),
        "technical_indicators": packet.get("indicators"),
        "existing_monitor": packet.get("monitor"),
        "data_quality": packet.get("data_quality"),
        "internal_engine_output": {
            "trade_health_score": engine_output.get("trade_health_score"),
            "component_scores": engine_output.get("component_scores"),
            "risk_flags": engine_output.get("risk_flags"),
            "engine_recommendation": engine_output.get("engine_recommendation"),
            "urgency": engine_output.get("urgency"),
        },
    }
    return _json.dumps(prompt_data, indent=2, default=str, ensure_ascii=False)


def _default_model_executor(
    payload: dict[str, Any],
    rendered_text: str | None,
) -> dict[str, Any]:
    """Live model executor — calls LLM via model_router.

    Input:
        payload      — structured reassessment data for metadata
        rendered_text — the full prompt text to send

    Output:
        {status, raw_response, provider, model_name, latency_ms, metadata}
    """
    import json as _json
    from app.services.model_router import get_model_endpoint, model_request
    from common.json_repair import extract_and_repair_json

    symbol = payload.get("symbol", "unknown")

    messages_payload = {
        "messages": [
            {"role": "system", "content": _ACTIVE_TRADE_SYSTEM_PROMPT},
            {"role": "user", "content": rendered_text or _json.dumps(payload)},
        ],
        "max_tokens": 1200,
        "temperature": 0.0,
    }

    t0 = time.monotonic()
    try:
        endpoint = get_model_endpoint()
        from app.services.model_state import get_model_source
        source_key = get_model_source()

        raw_api_response = model_request(
            messages_payload, timeout=120, retries=1,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "[active_trade_model] Model call failed for %s after %dms: %s",
            symbol, latency_ms, exc,
        )
        return {
            "status": "error",
            "raw_response": {},
            "provider": "model_router",
            "model_name": "unavailable",
            "latency_ms": latency_ms,
            "error": str(exc),
            "metadata": {},
        }

    # Extract assistant content
    assistant_text = ""
    choices = raw_api_response.get("choices", [])
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message", {})
        if isinstance(message, dict):
            assistant_text = message.get("content", "")

    if not assistant_text:
        return {
            "status": "error",
            "raw_response": {},
            "provider": source_key if "source_key" in dir() else "unknown",
            "model_name": raw_api_response.get("model", "unknown"),
            "latency_ms": latency_ms,
            "error": "empty_assistant_content",
            "metadata": {},
        }

    # Strip <think> tags if present
    from common.model_sanitize import had_think_tags
    if had_think_tags(assistant_text):
        from common.model_sanitize import strip_think_tags
        assistant_text = strip_think_tags(assistant_text)

    # Parse JSON from model response
    parsed = extract_and_repair_json(assistant_text)
    if not parsed or not isinstance(parsed, dict):
        return {
            "status": "error",
            "raw_response": {"raw_text": assistant_text[:2000]},
            "provider": source_key if "source_key" in dir() else "unknown",
            "model_name": raw_api_response.get("model", "unknown"),
            "latency_ms": latency_ms,
            "error": "json_parse_failed",
            "metadata": {},
        }

    return {
        "status": "success",
        "raw_response": parsed,
        "provider": source_key if "source_key" in dir() else "unknown",
        "model_name": raw_api_response.get("model", "unknown"),
        "latency_ms": latency_ms,
        "error": None,
        "metadata": {},
    }


def run_model_analysis(
    packet: dict[str, Any],
    engine_output: dict[str, Any],
    *,
    model_executor: ModelExecutor | None = None,
) -> dict[str, Any]:
    """Run model/prompt reasoning layer on the reassessment packet.

    The model receives:
        1. The raw reassessment packet (same data the engine used)
        2. The engine's output (so it can reference deterministic findings)

    Returns a normalized model output dict with recommendation fields,
    or a degraded/error result if the model is unavailable.
    """
    executor = model_executor or _default_model_executor

    rendered_text = _render_reassessment_prompt(packet, engine_output)
    prompt_payload = {
        "symbol": packet.get("symbol"),
        "trade_key": (packet.get("identity") or {}).get("trade_key"),
    }

    try:
        result = executor(prompt_payload, rendered_text)
    except Exception as exc:
        logger.error(
            "[active_trade_model] Executor raised for %s: %s",
            packet.get("symbol"), exc,
        )
        return _degraded_model_output(str(exc))

    if result.get("status") != "success":
        return _degraded_model_output(
            result.get("error") or "model_call_failed",
            provider=result.get("provider"),
            model_name=result.get("model_name"),
            latency_ms=result.get("latency_ms"),
        )

    raw = result.get("raw_response") or {}

    # Validate and normalize model output
    recommendation = str(raw.get("recommendation") or "").upper()
    if recommendation not in VALID_RECOMMENDATIONS:
        recommendation = None

    conviction = _to_float(raw.get("conviction"))
    if conviction is not None:
        conviction = max(0.0, min(1.0, conviction))

    return {
        "model_available": True,
        "recommendation": recommendation,
        "conviction": conviction,
        "rationale_summary": raw.get("rationale_summary"),
        "key_supporting_points": raw.get("key_supporting_points") or [],
        "key_risks": raw.get("key_risks") or [],
        "market_alignment": raw.get("market_alignment"),
        "portfolio_fit": raw.get("portfolio_fit"),
        "event_sensitivity": raw.get("event_sensitivity"),
        "suggested_next_move": raw.get("suggested_next_move"),
        "provider": result.get("provider"),
        "model_name": result.get("model_name"),
        "latency_ms": result.get("latency_ms"),
        "degraded_reasons": [],
    }


def _degraded_model_output(
    reason: str,
    *,
    provider: str | None = None,
    model_name: str | None = None,
    latency_ms: int | None = None,
) -> dict[str, Any]:
    """Build a degraded model output when the model is unavailable."""
    return {
        "model_available": False,
        "recommendation": None,
        "conviction": None,
        "rationale_summary": None,
        "key_supporting_points": [],
        "key_risks": [],
        "market_alignment": None,
        "portfolio_fit": None,
        "event_sensitivity": None,
        "suggested_next_move": None,
        "provider": provider,
        "model_name": model_name,
        "latency_ms": latency_ms,
        "degraded_reasons": [reason],
    }


# =====================================================================
#  Normalized recommendation output
# =====================================================================

def normalize_recommendation(
    trade: dict[str, Any],
    engine_output: dict[str, Any],
    model_output: dict[str, Any],
    packet: dict[str, Any],
) -> dict[str, Any]:
    """Combine engine + model outputs into the final normalized recommendation.

    Resolution priority:
        1. If model is available and produced a valid recommendation, use it.
        2. Otherwise, fall back to engine recommendation.
        3. Conviction: model conviction if available, else map engine score.

    The output preserves BOTH engine and model outputs for inspection.
    """
    # ── Resolve recommendation ──────────────────────────────────
    model_rec = model_output.get("recommendation") if model_output.get("model_available") else None
    engine_rec = engine_output.get("engine_recommendation")

    if model_rec and model_rec in VALID_RECOMMENDATIONS:
        recommendation = model_rec
        recommendation_source = "model"
    elif engine_rec and engine_rec in VALID_RECOMMENDATIONS:
        recommendation = engine_rec
        recommendation_source = "engine"
    else:
        recommendation = RECOMMENDATION_HOLD
        recommendation_source = "default"

    # ── Resolve conviction ──────────────────────────────────────
    model_conviction = _to_float(model_output.get("conviction"))
    if model_conviction is not None:
        conviction = model_conviction
    else:
        # Map engine health score to 0-1 conviction
        health = _to_float(engine_output.get("trade_health_score"))
        conviction = (health / 100.0) if health is not None else 0.5

    # ── Resolve rationale ───────────────────────────────────────
    if model_output.get("rationale_summary"):
        rationale_summary = model_output["rationale_summary"]
    else:
        # Build a rationale from engine findings
        rationale_summary = _build_engine_rationale(engine_output, packet)

    # ── Collect degraded reasons ────────────────────────────────
    degraded_reasons: list[str] = []
    degraded_reasons.extend(engine_output.get("degraded_flags") or [])
    degraded_reasons.extend(model_output.get("degraded_reasons") or [])
    # Deduplicate preserving order
    seen: set[str] = set()
    unique_degraded: list[str] = []
    for r in degraded_reasons:
        if r not in seen:
            seen.add(r)
            unique_degraded.append(r)

    identity = packet.get("identity") or {}

    return {
        "active_trade_recommendation_version": _PIPELINE_VERSION,
        "active_trade_id": identity.get("trade_key"),
        "symbol": packet.get("symbol"),
        "strategy": identity.get("strategy"),
        "strategy_id": identity.get("strategy_id"),
        "expiration": identity.get("expiration"),
        "dte": identity.get("dte"),

        "recommendation": recommendation,
        "recommendation_source": recommendation_source,
        "conviction": round(conviction, 3) if conviction is not None else None,
        "urgency": engine_output.get("urgency", 1),

        "rationale_summary": rationale_summary,
        "key_supporting_points": model_output.get("key_supporting_points") or [],
        "key_risks": model_output.get("key_risks") or [],
        "market_alignment": model_output.get("market_alignment"),
        "portfolio_fit": model_output.get("portfolio_fit"),
        "event_sensitivity": model_output.get("event_sensitivity"),
        "suggested_next_move": model_output.get("suggested_next_move"),

        "internal_engine_summary": {
            "trade_health_score": engine_output.get("trade_health_score"),
            "engine_recommendation": engine_output.get("engine_recommendation"),
            "urgency": engine_output.get("urgency"),
            "risk_flags": engine_output.get("risk_flags") or [],
        },
        "internal_engine_metrics": engine_output.get("component_scores") or {},
        "internal_engine_flags": engine_output.get("risk_flags") or [],

        "model_summary": {
            "model_available": model_output.get("model_available", False),
            "model_recommendation": model_output.get("recommendation"),
            "model_conviction": model_output.get("conviction"),
            "provider": model_output.get("provider"),
            "model_name": model_output.get("model_name"),
            "latency_ms": model_output.get("latency_ms"),
        },

        "position_snapshot": {
            "avg_open_price": (packet.get("position") or {}).get("avg_open_price"),
            "mark_price": (packet.get("position") or {}).get("mark_price"),
            "unrealized_pnl": (packet.get("position") or {}).get("unrealized_pnl"),
            "unrealized_pnl_pct": (packet.get("position") or {}).get("unrealized_pnl_pct"),
        },

        "degraded_reasons": unique_degraded,
        "is_degraded": len(unique_degraded) > 0,
    }


def _build_engine_rationale(
    engine_output: dict[str, Any],
    packet: dict[str, Any],
) -> str:
    """Build a human-readable rationale from engine findings when model is unavailable."""
    parts: list[str] = []
    rec = engine_output.get("engine_recommendation") or "HOLD"
    score = engine_output.get("trade_health_score")
    symbol = packet.get("symbol") or "position"

    parts.append(f"Engine assessment: {rec} for {symbol}")
    if score is not None:
        parts.append(f"(health score {score}/100).")
    else:
        parts.append("(health score unavailable).")

    flags = engine_output.get("risk_flags") or []
    if flags:
        parts.append(f"Risk flags: {', '.join(flags)}.")

    components = engine_output.get("component_scores") or {}
    low_scores = [
        f"{k}={int(v)}" for k, v in components.items()
        if v is not None and v < 40
    ]
    if low_scores:
        parts.append(f"Weak areas: {', '.join(low_scores)}.")

    degraded = engine_output.get("degraded_flags") or []
    if degraded:
        parts.append(f"Note: analysis degraded due to missing: {', '.join(degraded[:3])}.")

    return " ".join(parts)


# =====================================================================
#  Stage tracking helpers
# =====================================================================

def _make_stage_tracker() -> dict[str, Any]:
    """Create a mutable stage-tracking dict for per-stage telemetry."""
    return {}


def _start_stage(
    stages: dict[str, Any], stage_key: str,
) -> float:
    """Mark a stage as running after verifying dependencies, return monotonic start time.

    Raises RuntimeError if any prerequisite stage has not completed/skipped.
    Records dependency_check metadata for auditability.
    """
    unsatisfied = _check_dependencies(stage_key, stages)
    if unsatisfied:
        raise RuntimeError(
            f"Stage '{stage_key}' cannot start: unsatisfied dependencies "
            f"{unsatisfied}. Current stage statuses: "
            f"{ {k: v.get('status') for k, v in stages.items()} }"
        )

    t = time.monotonic()
    required = sorted(ATP_DEPENDENCY_MAP.get(stage_key, set()))
    stages[stage_key] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": None,
        "dependencies": required,
        "dependency_satisfied_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {},
    }
    return t


def _complete_stage(
    stages: dict[str, Any], stage_key: str, t_start: float,
    *, metadata: dict[str, Any] | None = None,
    status: str = "completed",
) -> None:
    """Mark a stage as completed/failed with timing."""
    entry = stages.get(stage_key, {})
    entry["status"] = status
    entry["ended_at"] = datetime.now(timezone.utc).isoformat()
    entry["duration_ms"] = int((time.monotonic() - t_start) * 1000)
    if metadata:
        entry.setdefault("metadata", {}).update(metadata)
    stages[stage_key] = entry


def _skip_stage(
    stages: dict[str, Any], stage_key: str,
    reason: str = "no_trades",
) -> None:
    """Mark a stage as skipped."""
    required = sorted(ATP_DEPENDENCY_MAP.get(stage_key, set()))
    stages[stage_key] = {
        "status": "skipped",
        "reason": reason,
        "duration_ms": 0,
        "dependencies": required,
        "metadata": {},
    }


def _count_values(values: list) -> dict[str, int]:
    """Count occurrences of each non-None value in a list."""
    counts: dict[str, int] = {}
    for v in values:
        if v is not None:
            key = str(v)
            counts[key] = counts.get(key, 0) + 1
    return counts


# =====================================================================
#  Pipeline runner
# =====================================================================

async def run_active_trade_pipeline(
    trades: list[dict[str, Any]],
    monitor_service: Any,
    regime_service: Any,
    base_data_service: Any,
    *,
    model_executor: ModelExecutor | None = None,
    skip_model: bool = False,
    positions_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full Active Trade Pipeline on all provided trades.

    Parameters
    ----------
    trades : list
        Normalized active trades from routes_active_trades._build_active_trades().
    monitor_service : ActiveTradeMonitorService
        For existing monitor evaluations.
    regime_service : any
        For market regime context.
    base_data_service : any
        For technical indicators (SMA, RSI).
    model_executor : callable | None
        Optional override for model calls (for testing).
    skip_model : bool
        If True, skip model analysis entirely (engine-only mode).
    positions_metadata : dict | None
        Metadata about the position fetch (source, account_mode, etc.)

    Returns
    -------
    dict with:
        run_id, started_at, ended_at, duration_ms, status,
        trade_count, recommendations[], summary, degraded_reasons,
        stages (per-stage timing/status/metadata)
    """
    run_id = f"atp-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    stages = _make_stage_tracker()

    logger.info(
        "[active_trade_pipeline] Starting run %s with %d trades (skip_model=%s)",
        run_id, len(trades), skip_model,
    )

    # ══════════════════════════════════════════════════════════════
    #  Stage 1: load_positions — record what was fetched
    # ══════════════════════════════════════════════════════════════
    t_stage = _start_stage(stages, "load_positions")
    positions_loaded = len(trades)
    _complete_stage(stages, "load_positions", t_stage, metadata={
        "positions_loaded": positions_loaded,
        "source": (positions_metadata or {}).get("source", "tradier"),
        "account_mode": (positions_metadata or {}).get("account_mode"),
    })
    logger.info(
        "[active_trade_pipeline] Stage load_positions: %d positions loaded",
        positions_loaded,
    )

    # ══════════════════════════════════════════════════════════════
    #  Stage 2: market_context — fetch regime and macro data
    # ══════════════════════════════════════════════════════════════
    t_stage = _start_stage(stages, "market_context")
    market_context: dict[str, Any] = {}
    try:
        regime = await regime_service.get_regime()
        market_context["regime_label"] = regime.get("label")
        market_context["regime_score"] = regime.get("score")
    except Exception as exc:
        logger.warning("[active_trade_pipeline] Regime unavailable: %s", exc)
        market_context["regime_label"] = None
        market_context["regime_score"] = None
    _complete_stage(stages, "market_context", t_stage, metadata={
        "regime_label": market_context.get("regime_label"),
        "regime_score": market_context.get("regime_score"),
    })

    # ── Handle zero positions honestly ──────────────────────────
    if not trades:
        logger.info(
            "[active_trade_pipeline] No active positions found. "
            "Skipping analysis stages.",
        )
        for skip_key in (
            "build_packets", "engine_analysis",
            "model_analysis", "normalize",
        ):
            _skip_stage(stages, skip_key, reason="no_active_positions")

        t_stage = _start_stage(stages, "complete")
        duration_ms = int((time.monotonic() - t0) * 1000)
        _complete_stage(stages, "complete", t_stage, metadata={
            "total_duration_ms": duration_ms,
        })
        return {
            "run_id": run_id,
            "pipeline_version": _PIPELINE_VERSION,
            "started_at": started_at,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "status": "completed",
            "trade_count": 0,
            "recommendation_counts": {},
            "recommendations": [],
            "market_context_snapshot": market_context,
            "summary": {
                "total_trades": 0,
                "hold_count": 0, "reduce_count": 0,
                "close_count": 0, "urgent_review_count": 0,
                "model_available_count": 0, "degraded_count": 0,
            },
            "degraded_reasons": [],
            "stages": stages,
            "stage_order": list(ATP_STAGES),
            "dependency_graph": {k: sorted(v) for k, v in ATP_DEPENDENCY_MAP.items()},
        }

    # ══════════════════════════════════════════════════════════════
    #  Stage 3: build_packets — monitor evaluation + indicators
    # ══════════════════════════════════════════════════════════════
    t_stage = _start_stage(stages, "build_packets")
    symbol_indicators: dict[str, dict[str, Any]] = {}
    monitor_results: dict[str, dict[str, Any]] = {}

    # Get monitor results for all trades
    try:
        monitor_list = await monitor_service.evaluate_batch(trades)
        for mr in monitor_list:
            sym = mr.get("symbol", "???")
            monitor_results[sym] = mr
    except Exception as exc:
        logger.warning("[active_trade_pipeline] Monitor evaluation failed: %s", exc)

    # Fetch indicators per unique symbol
    unique_symbols = sorted({str(t.get("symbol") or "").upper() for t in trades})
    for sym in unique_symbols:
        if not sym:
            continue
        try:
            indicators = await _fetch_indicators(base_data_service, sym)
            symbol_indicators[sym] = indicators
        except Exception as exc:
            logger.warning("[active_trade_pipeline] Indicators unavailable for %s: %s", sym, exc)
            symbol_indicators[sym] = {"sma20": None, "sma50": None, "rsi14": None}

    # Build packets
    packets: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for trade in trades:
        symbol = str(trade.get("symbol") or "???").upper()
        packet = build_reassessment_packet(
            trade,
            market_context,
            monitor_results.get(symbol),
            symbol_indicators.get(symbol),
        )
        packets.append((trade, packet))

    _complete_stage(stages, "build_packets", t_stage, metadata={
        "packets_built": len(packets),
        "symbols": unique_symbols,
        "monitor_results_count": len(monitor_results),
        "indicators_fetched": len(symbol_indicators),
    })

    # ══════════════════════════════════════════════════════════════
    #  Stage 4: engine_analysis — deterministic engine per trade
    # ══════════════════════════════════════════════════════════════
    t_stage = _start_stage(stages, "engine_analysis")
    engine_outputs: list[dict[str, Any]] = []
    for _trade, packet in packets:
        engine_output = run_analysis_engine(packet)
        engine_outputs.append(engine_output)

    _complete_stage(stages, "engine_analysis", t_stage, metadata={
        "trades_analyzed": len(engine_outputs),
        "recommendation_distribution": _count_values(
            [eo.get("engine_recommendation") for eo in engine_outputs]
        ),
    })

    # ══════════════════════════════════════════════════════════════
    #  Stage 5: model_analysis — LLM reasoning per trade
    # ══════════════════════════════════════════════════════════════
    t_stage = _start_stage(stages, "model_analysis")
    model_outputs: list[dict[str, Any]] = []
    model_count = 0
    model_failed = 0

    if skip_model:
        for _ in packets:
            model_outputs.append(_degraded_model_output("model_skipped"))
        _complete_stage(stages, "model_analysis", t_stage,
                        status="skipped", metadata={
                            "reason": "skip_model=true",
                        })
    else:
        for (_trade, packet), engine_output in zip(packets, engine_outputs):
            model_output = run_model_analysis(
                packet, engine_output,
                model_executor=model_executor,
            )
            model_outputs.append(model_output)
            if model_output.get("model_available"):
                model_count += 1
            else:
                model_failed += 1

        _complete_stage(stages, "model_analysis", t_stage, metadata={
            "model_calls": len(packets),
            "model_succeeded": model_count,
            "model_failed": model_failed,
        })

    # ══════════════════════════════════════════════════════════════
    #  Stage 6: normalize — combine engine + model into final recs
    # ══════════════════════════════════════════════════════════════
    t_stage = _start_stage(stages, "normalize")
    recommendations: list[dict[str, Any]] = []
    pipeline_degraded: list[str] = []

    for (trade, packet), engine_output, model_output in zip(
        packets, engine_outputs, model_outputs,
    ):
        rec = normalize_recommendation(trade, engine_output, model_output, packet)
        rec["run_id"] = run_id
        recommendations.append(rec)

        if rec.get("is_degraded"):
            for reason in rec.get("degraded_reasons") or []:
                if reason not in pipeline_degraded:
                    pipeline_degraded.append(reason)

    _complete_stage(stages, "normalize", t_stage, metadata={
        "recommendations_produced": len(recommendations),
        "degraded_count": sum(1 for r in recommendations if r.get("is_degraded")),
    })

    # ══════════════════════════════════════════════════════════════
    #  Stage 7: complete — finalize and build result
    # ══════════════════════════════════════════════════════════════
    t_stage = _start_stage(stages, "complete")
    duration_ms = int((time.monotonic() - t0) * 1000)
    ended_at = datetime.now(timezone.utc).isoformat()

    rec_counts: dict[str, int] = {}
    for rec in recommendations:
        r = rec.get("recommendation") or "UNKNOWN"
        rec_counts[r] = rec_counts.get(r, 0) + 1

    _complete_stage(stages, "complete", t_stage, metadata={
        "total_duration_ms": duration_ms,
        "recommendation_counts": rec_counts,
    })

    return {
        "run_id": run_id,
        "pipeline_version": _PIPELINE_VERSION,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
        "status": "completed",
        "trade_count": len(trades),
        "recommendation_counts": rec_counts,
        "recommendations": recommendations,
        "market_context_snapshot": market_context,
        "summary": {
            "total_trades": len(trades),
            "hold_count": rec_counts.get("HOLD", 0),
            "reduce_count": rec_counts.get("REDUCE", 0),
            "close_count": rec_counts.get("CLOSE", 0),
            "urgent_review_count": rec_counts.get("URGENT_REVIEW", 0),
            "model_available_count": sum(
                1 for r in recommendations
                if (r.get("model_summary") or {}).get("model_available")
            ),
            "degraded_count": sum(
                1 for r in recommendations if r.get("is_degraded")
            ),
        },
        "degraded_reasons": pipeline_degraded,
        "stages": stages,
        "stage_order": list(ATP_STAGES),
        "dependency_graph": {k: sorted(v) for k, v in ATP_DEPENDENCY_MAP.items()},
    }


async def _fetch_indicators(
    base_data_service: Any, symbol: str,
) -> dict[str, Any]:
    """Fetch SMA20, SMA50, RSI14 for a symbol."""
    from common.quant_analysis import rsi, simple_moving_average

    try:
        prices = await base_data_service.get_prices_history(
            symbol, lookback_days=120,
        )
    except Exception:
        return {"sma20": None, "sma50": None, "rsi14": None}

    if not prices:
        return {"sma20": None, "sma50": None, "rsi14": None}

    return {
        "sma20": simple_moving_average(prices, 20),
        "sma50": simple_moving_average(prices, 50),
        "rsi14": rsi(prices, 14),
    }


# =====================================================================
#  Utilities
# =====================================================================

def _to_float(val: Any) -> float | None:
    """Safely coerce to float. Returns None on failure — never fabricates."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def _to_int(val: Any) -> int | None:
    """Safely coerce to int. Returns None on failure."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None
