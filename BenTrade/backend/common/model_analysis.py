from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from requests.exceptions import RequestException

from app.models.trade_contract import TradeContract
from app.utils.validation import parse_expiration


class LocalModelUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TransportResult:
    """Lightweight return type from _model_transport().

    Attributes:
        content: Raw assistant text after think-tag stripping.
        transport_path: "routed" if distributed routing was used, "legacy" if
            direct HTTP POST was used.
        finish_reason: The LLM finish_reason (e.g. "stop", "length") when
            available from the legacy path.  None on the routed path because
            the routing layer does not propagate this field.
        provider: Provider identifier if routed (e.g. "local_llm"), None on
            legacy path.
    """
    content: str
    transport_path: str = "legacy"
    finish_reason: str | None = None
    provider: str | None = None


def _extract_json_payload(raw_text: str) -> Any:
    """Legacy JSON extractor — DEPRECATED.

    Superseded by ``common.json_repair.extract_and_repair_json()`` which
    covers all stages this function handles (direct parse + block extraction)
    plus fence stripping, smart-quote repair, trailing-comma fix, and more.
    Kept for backward compatibility only.
    """
    text = str(raw_text or "").strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        pass

    start_idx = None
    for char in ("{", "["):
        idx = text.find(char)
        if idx != -1:
            start_idx = idx
            break
    if start_idx is None:
        return None

    open_char = text[start_idx]
    close_char = "}" if open_char == "{" else "]"
    end_idx = text.rfind(close_char)
    if end_idx == -1:
        return None

    try:
        return json.loads(text[start_idx : end_idx + 1])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Shared model transport layer (Steps 11-12)
#
# All analyze_* functions share identical HTTP transport + retry + response
# extraction + think-tag stripping logic.  This function centralizes it:
#   • When routing is enabled → execute_routed_model() (distributed)
#   • When routing is disabled / routing fails → legacy requests.post()
#
# Returns a TransportResult dataclass with:
#   content — raw assistant_text (post-sanitization)
#   transport_path — "routed" or "legacy"
#   finish_reason — LLM finish_reason from legacy path (None on routed)
#   provider — provider name on routed path (None on legacy)
#
# Callers still own their own JSON repair, coercion, and trace attachment.
#
# Input: task_type — semantic identifier for routing policy
#        payload — OpenAI-compatible payload dict with messages etc.
#        log_prefix — domain tag for log messages (e.g. "MODEL_REGIME")
#        model_url — legacy endpoint URL (resolved lazily if None)
#        retries — retry count for legacy path (routing handles its own)
#        timeout — request timeout in seconds
# ---------------------------------------------------------------------------

def _model_transport(
    *,
    task_type: str,
    payload: dict[str, Any],
    log_prefix: str,
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> TransportResult:
    """Shared LLM transport: routed (if enabled) → legacy HTTP fallback.

    Returns a TransportResult with the assistant text after think-tag
    stripping, plus lightweight transport metadata (path, finish_reason,
    provider).

    Raises:
        LocalModelUnavailableError: model endpoint unreachable (legacy path).
        RuntimeError: model call failed for non-network reasons.
    """
    import logging
    _log = logging.getLogger("bentrade.model_analysis")

    # ── 1. Try distributed routing ──────────────────────────────
    try:
        from app.services.model_routing_integration import (
            RoutingDisabledError,
            execute_routed_model,
        )

        messages = payload.get("messages", [])
        system_prompt: str | None = None
        user_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content")
            else:
                user_messages.append(msg)

        legacy_result, trace = execute_routed_model(
            task_type=task_type,
            messages=user_messages,
            system_prompt=system_prompt,
            timeout=float(timeout),
            max_tokens=payload.get("max_tokens"),
            temperature=payload.get("temperature"),
            metadata={"source": log_prefix},
        )

        if legacy_result["status"] == "success":
            content = legacy_result.get("content") or ""
            _log.info(
                "[%s] routed OK: provider=%s timing_ms=%s request_id=%s",
                log_prefix,
                trace.selected_provider,
                trace.timing_ms,
                trace.request_id,
            )
            # Sanitize think-tags (provider adapters may not strip these)
            content = _strip_think_tags(content)
            return TransportResult(
                content=content,
                transport_path="routed",
                finish_reason=None,   # routing layer does not propagate finish_reason
                provider=trace.selected_provider,
            )

        # Routing returned an error — fall through to legacy
        _log.warning(
            "[%s] routed call failed: %s — falling back to legacy",
            log_prefix,
            legacy_result.get("error"),
        )
    except RoutingDisabledError:
        pass  # Expected — use legacy path
    except Exception as exc:
        _log.warning(
            "[%s] routing unavailable: %s — falling back to legacy",
            log_prefix,
            exc,
        )

    # ── 2. Legacy HTTP path ─────────────────────────────────────
    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    from requests.exceptions import RequestException as _ReqExc
    import requests as _requests

    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            _log.info("[%s] POST %s (attempt %d, timeout=%ds)", log_prefix, model_url, attempt, timeout)
            response = _requests.post(model_url, json=payload, timeout=timeout)
            _log.info(
                "[%s] response HTTP %d (%d bytes, %.1fs)",
                log_prefix,
                response.status_code,
                len(response.content),
                response.elapsed.total_seconds(),
            )
            response.raise_for_status()

            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            assistant_text = None
            finish_reason: str | None = None
            if isinstance(response_json, dict):
                choices = response_json.get("choices") or []
                if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                    first = choices[0]
                    finish_reason = first.get("finish_reason")
                    message = first.get("message")
                    if isinstance(message, dict) and "content" in message:
                        assistant_text = message.get("content")
                    elif "text" in first:
                        assistant_text = first.get("text")
            if assistant_text is None:
                assistant_text = getattr(response, "text", "")

            # Log finish_reason when present
            if finish_reason:
                _log.info("[%s] finish_reason=%s", log_prefix, finish_reason)
            if finish_reason == "length":
                _log.warning(
                    "[%s] response TRUNCATED (finish_reason=length) — token budget may be insufficient",
                    log_prefix,
                )

            # Sanitize: strip <think> tags
            from common.model_sanitize import had_think_tags
            if had_think_tags(assistant_text):
                _log.info("[%s] <think> content detected and stripped (attempt %d)", log_prefix, attempt)
            assistant_text = _strip_think_tags(assistant_text)

            return TransportResult(
                content=assistant_text,
                transport_path="legacy",
                finish_reason=finish_reason,
                provider=None,
            )
        except _ReqExc as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break

    if isinstance(last_error, _ReqExc):
        raise LocalModelUnavailableError(
            f"Local model endpoint unavailable at {model_url}: {last_error}"
        ) from last_error
    raise RuntimeError(f"{log_prefix} model transport failed: {last_error}")


def _coerce_stock_model_output(candidate: Any) -> dict[str, Any] | None:
    if isinstance(candidate, list) and candidate:
        first = candidate[0]
        if isinstance(first, dict):
            candidate = first
    if not isinstance(candidate, dict):
        return None

    recommendation = str(candidate.get("recommendation") or "WAIT").strip().upper()
    if recommendation not in {"BUY", "SELL", "WAIT"}:
        recommendation = "WAIT"

    confidence_raw = candidate.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.35
    confidence = max(0.0, min(confidence, 1.0))

    summary = str(candidate.get("summary") or "Model returned no summary.").strip()
    key_factors = candidate.get("key_factors") if isinstance(candidate.get("key_factors"), list) else []
    risks = candidate.get("risks") if isinstance(candidate.get("risks"), list) else []
    time_horizon = str(candidate.get("time_horizon") or "1W").strip().upper()
    if time_horizon not in {"1D", "1W", "1M"}:
        time_horizon = "1W"

    trade_ideas_raw = candidate.get("trade_ideas") if isinstance(candidate.get("trade_ideas"), list) else []
    trade_ideas: list[dict[str, Any]] = []
    for row in trade_ideas_raw:
        if isinstance(row, dict):
            idea = dict(row)
            expiration_raw = idea.get("expiration") or idea.get("expiration_date") or idea.get("expiry")
            if expiration_raw not in (None, ""):
                expiration, dte = parse_expiration(expiration_raw)
                if expiration is None or (dte is not None and dte < 0):
                    continue
            trade_ideas.append(idea)

    return {
        "recommendation": recommendation,
        "confidence": confidence,
        "summary": summary,
        "key_factors": [str(item) for item in key_factors if str(item or "").strip()],
        "risks": [str(item) for item in risks if str(item or "").strip()],
        "time_horizon": time_horizon,
        "trade_ideas": trade_ideas,
    }


def _coerce_regime_model_output(candidate: Any) -> dict[str, Any] | None:
    """Normalise the LLM response for regime analysis into a consistent dict.

    Resilient to common LLM response variations:
    - Alternate key names (e.g., risk_regime vs risk_regime_label)
    - Truncated JSON with partial fields
    - Nested sub-objects
    """
    import logging as _logging
    _log = _logging.getLogger("bentrade.model_analysis")

    if isinstance(candidate, list) and candidate:
        first = candidate[0]
        if isinstance(first, dict):
            candidate = first
    if not isinstance(candidate, dict):
        return None

    # ── Helper: find value from multiple possible key names ─────────
    def _get_first(d: dict, *keys: str) -> Any:
        for k in keys:
            v = d.get(k)
            if v is not None:
                return v
        return None

    sections = [
        "executive_summary",
        "regime_breakdown",
        "primary_fit",
        "avoid_rationale",
        "change_triggers",
        "confidence_caveats",
        "raw_inputs_used",
    ]
    out: dict[str, Any] = {}
    for key in sections:
        val = candidate.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
        elif isinstance(val, list):
            out[key] = [str(item) for item in val if str(item or "").strip()]
        elif isinstance(val, dict):
            out[key] = val
        else:
            out[key] = None

    confidence_raw = _get_first(candidate, "confidence", "overall_confidence")
    try:
        out["confidence"] = max(0.0, min(float(confidence_raw), 1.0))
    except (TypeError, ValueError):
        out["confidence"] = None

    # ── Model-inferred regime summary labels ────────────────────────
    # These are the structured labels the model infers independently from raw
    # inputs — used for the Engine-vs-Model comparison table.
    # Try multiple alternate key names LLMs commonly produce.
    _LABEL_ALTERNATES: dict[str, tuple[str, ...]] = {
        "risk_regime_label": ("risk_regime_label", "risk_regime", "risk_label", "risk"),
        "trend_label": ("trend_label", "trend", "market_trend"),
        "vol_regime_label": ("vol_regime_label", "vol_label", "volatility_label",
                             "volatility", "vol_regime", "volatility_regime"),
    }
    for label_key, alternates in _LABEL_ALTERNATES.items():
        val = _get_first(candidate, *alternates)
        out[label_key] = str(val).strip() if isinstance(val, str) and val.strip() else None

    # ── Three-block assessment labels ───────────────────────────────
    _ASSESS_ALTERNATES: dict[str, tuple[str, ...]] = {
        "structural_assessment": ("structural_assessment", "structural", "structural_label"),
        "tape_assessment": ("tape_assessment", "tape", "tape_label"),
        "tactical_assessment": ("tactical_assessment", "tactical", "tactical_label"),
    }
    for assess_key, alternates in _ASSESS_ALTERNATES.items():
        val = _get_first(candidate, *alternates)
        out[assess_key] = str(val).strip() if isinstance(val, str) and val.strip() else None

    # ── What-works / what-to-avoid ──────────────────────────────────
    for list_key in ("what_works", "what_to_avoid"):
        out[list_key] = _coerce_string_list(candidate.get(list_key), max_items=6)

    key_drivers = _get_first(candidate, "key_drivers", "drivers", "top_drivers")
    if isinstance(key_drivers, list):
        out["key_drivers"] = [str(item).strip() for item in key_drivers if str(item or "").strip()][:5]
    elif isinstance(key_drivers, str) and key_drivers.strip():
        out["key_drivers"] = [key_drivers.strip()]
    else:
        out["key_drivers"] = None

    # ── Diagnostic: count how many fields are populated vs None ─────
    populated = sum(1 for k, v in out.items() if v is not None)
    total = len(out)
    if populated == 0:
        _log.warning(
            "[MODEL_REGIME_COERCE] ALL fields None after coercion. "
            "candidate_keys=%s candidate_sample=%s",
            list(candidate.keys())[:20],
            str(candidate)[:500],
        )
    elif populated < total // 2:
        _log.warning(
            "[MODEL_REGIME_COERCE] low_fill=%d/%d. "
            "missing_keys=%s candidate_keys=%s",
            populated, total,
            [k for k, v in out.items() if v is None],
            list(candidate.keys())[:20],
        )

    return out


def _extract_regime_raw_inputs(regime_data: dict[str, Any]) -> dict[str, Any]:
    """Extract ONLY raw market inputs from regime data, excluding all derived scores/labels.

    Raw inputs = values directly from providers or computed from raw price series
    (e.g., moving averages, RSI from closes) PLUS MI engine pillar-level detail
    from the three-block architecture.

    Explicitly excluded:
    - regime_label (RISK_ON/NEUTRAL/RISK_OFF/etc.)
    - regime_score (0-100 composite)
    - confidence (0-1 composite)
    - interpretation (human-readable string)
    - component score values (normalized 0-100)
    - component raw_points
    - component signals (human-readable scoring descriptions)
    - suggested_playbook (primary/avoid/notes)
    - what_works, what_to_avoid, change_triggers, key_drivers
    """
    components = regime_data.get("components") or {}

    trend_inputs = (components.get("trend") or {}).get("inputs") or {}
    vol_inputs = (components.get("volatility") or {}).get("inputs") or {}
    breadth_inputs = (components.get("breadth") or {}).get("inputs") or {}
    rates_inputs = (components.get("rates") or {}).get("inputs") or {}
    momentum_inputs = (components.get("momentum") or {}).get("inputs") or {}

    _TREND_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA"]
    per_index_trend: dict[str, dict[str, Any]] = {}
    for sym in _TREND_SYMBOLS:
        sym_data = trend_inputs.get(sym)
        if isinstance(sym_data, dict):
            per_index_trend[sym] = sym_data

    spy_trend = per_index_trend.get("SPY", {})

    raw: dict[str, Any] = {
        # Trend: multi-index moving averages (per-symbol breakdown)
        "trend_indexes": per_index_trend if per_index_trend else None,
        "spy_price": spy_trend.get("close"),
        "spy_ema20": spy_trend.get("ema20"),
        "spy_ema50": spy_trend.get("ema50"),
        "spy_sma50": spy_trend.get("sma50"),
        "spy_sma200": spy_trend.get("sma200"),
        # Volatility: VIX spot level and recent change
        "vix_spot": vol_inputs.get("vix"),
        "vix_5d_change_pct": vol_inputs.get("vix_5d_change"),
        # Breadth: sector ETF breadth counts (raw counts, not scored)
        "sectors_above_ema20": breadth_inputs.get("sectors_above_ema20"),
        "sectors_total": breadth_inputs.get("sectors_total"),
        "pct_sectors_above_ema20": breadth_inputs.get("pct_above_ema20"),
        # Rates: 10Y Treasury yield
        "ten_year_yield": rates_inputs.get("ten_year_yield"),
        "ten_year_5d_change_bps": rates_inputs.get("ten_year_5d_change_bps"),
        # Momentum: multi-index average RSI
        "avg_rsi14": momentum_inputs.get("avg_rsi14"),
        "rsi14_per_index": {
            sym: momentum_inputs.get(sym)
            for sym in _TREND_SYMBOLS
            if momentum_inputs.get(sym) is not None
        } or None,
    }

    # ── Three-block pillar detail (MI engine normalized data) ─────
    # Include pillar-level detail from each block.  To avoid payload bloat,
    # each pillar is trimmed to a compact summary (label + score + key scalars)
    # and signals are capped at 6 items, each truncated to 120 chars.
    blocks = regime_data.get("blocks") or {}
    for block_key in ("structural", "tape", "tactical"):
        block = blocks.get(block_key)
        if not block or not isinstance(block, dict):
            continue
        pillar_detail = block.get("pillar_detail")
        if pillar_detail and isinstance(pillar_detail, dict):
            raw[f"block_{block_key}_pillars"] = _compact_pillar_detail(pillar_detail)
        key_signals = block.get("key_signals")
        if key_signals and isinstance(key_signals, list):
            raw[f"block_{block_key}_signals"] = [
                str(s)[:120] for s in key_signals[:6]
            ]

    return raw


def _compact_pillar_detail(pillar_detail: dict[str, Any]) -> dict[str, Any]:
    """Reduce a block's pillar_detail to model-relevant scalars only.

    Keeps: label, score, value, weight, tone, spread, level, direction
    Drops: deeply nested sub-objects, history arrays, raw component dicts.
    """
    _KEEP_KEYS = {"label", "score", "value", "weight", "tone", "spread",
                  "level", "direction", "status", "signal", "pct", "delta"}
    compact: dict[str, Any] = {}
    for name, detail in pillar_detail.items():
        if not isinstance(detail, dict):
            compact[name] = detail
            continue
        slim: dict[str, Any] = {}
        for k, v in detail.items():
            if k in _KEEP_KEYS and not isinstance(v, (dict, list)):
                slim[k] = v
        compact[name] = slim if slim else {"_empty": True}
    return compact


# Fields that are explicitly derived by BenTrade's regime engine and must NOT
# be sent to the model.  Used for trace logging verification.
_REGIME_DERIVED_FIELDS = [
    "regime_label",
    "regime_score",
    "confidence",
    "interpretation",
    "suggested_playbook",
    "what_works",
    "what_to_avoid",
    "change_triggers",
    "key_drivers",
    "agreement",
    "blocks.*.score",
    "blocks.*.label",
    "blocks.*.confidence",
    "components.*.score",
    "components.*.raw_points",
    "components.*.signals",
]


def extract_engine_regime_summary(regime_data: dict[str, Any]) -> dict[str, Any]:
    """Extract a structured summary of the ENGINE-derived regime outputs.

    This captures BenTrade's computed labels and scores so they can be shown
    in the Engine column of the comparison table.

    Input fields and derivation:
      - risk_regime_label: from regime_data["regime_label"] (RISK_ON → Risk-On, etc.)
      - trend_label: inferred from multi-index trend composite (majority vote)
      - vol_regime_label: inferred from VIX level buckets
      - confidence: from regime_data["confidence"] (direct, 0-1) or score/100 fallback
      - key_drivers: from regime_data["key_drivers"] or top component signals
      - structural/tape/tactical labels: from regime_data["blocks"]
    """
    # ── Risk regime label ───────────────────────────────────────────
    raw_label = str(regime_data.get("regime_label") or "NEUTRAL").upper()
    _LABEL_MAP = {
        "RISK_ON": "Risk-On",
        "RISK_ON_CAUTIOUS": "Risk-On (Cautious)",
        "RISK_OFF": "Risk-Off",
        "RISK_OFF_CAUTION": "Risk-Off (Caution)",
        "NEUTRAL": "Neutral",
    }
    risk_regime_label = _LABEL_MAP.get(raw_label, "Neutral")

    # ── Trend label ─────────────────────────────────────────────────
    components = regime_data.get("components") or {}
    trend_inputs = (components.get("trend") or {}).get("inputs") or {}
    _TREND_SYMS = ["SPY", "QQQ", "IWM", "DIA"]
    bull_count, bear_count, valid_count = 0, 0, 0
    for sym in _TREND_SYMS:
        sym_data = trend_inputs.get(sym) if isinstance(trend_inputs.get(sym), dict) else None
        if not sym_data:
            continue
        close = sym_data.get("close")
        ema20 = sym_data.get("ema20")
        sma200 = sym_data.get("sma200")
        if close is None or ema20 is None:
            continue
        valid_count += 1
        if close > ema20:
            bull_count += 1
        if sma200 is not None and close < sma200:
            bear_count += 1
    if valid_count == 0:
        trend_label = "Unknown"
    elif bull_count > valid_count / 2:
        trend_label = "Uptrend"
    elif bear_count > valid_count / 2:
        trend_label = "Downtrend"
    else:
        trend_label = "Sideways"

    # ── Volatility regime label ─────────────────────────────────────
    vol_inputs = (components.get("volatility") or {}).get("inputs") or {}
    vix = vol_inputs.get("vix")
    if vix is not None:
        if vix < 18:
            vol_regime_label = "Low"
        elif vix <= 25:
            vol_regime_label = "Moderate"
        else:
            vol_regime_label = "High"
    else:
        vol_regime_label = "Unknown"

    # ── Confidence ──────────────────────────────────────────────────
    # Prefer the direct confidence field from new regime; fallback to score/100
    confidence = regime_data.get("confidence")
    if confidence is None:
        regime_score = regime_data.get("regime_score")
        try:
            confidence = round(max(0.0, min(float(regime_score) / 100.0, 1.0)), 2)
        except (TypeError, ValueError):
            confidence = None
    else:
        try:
            confidence = round(max(0.0, min(float(confidence), 1.0)), 2)
        except (TypeError, ValueError):
            confidence = None

    # ── Key drivers ─────────────────────────────────────────────────
    # Prefer top-level key_drivers from new regime; fallback to component signals
    key_drivers: list[str] = []
    top_drivers = regime_data.get("key_drivers")
    if isinstance(top_drivers, list) and top_drivers:
        key_drivers = [str(d) for d in top_drivers[:5]]
    else:
        scored_components = []
        for cname in ("trend", "volatility", "breadth", "rates", "momentum"):
            comp = components.get(cname) or {}
            score = comp.get("score")
            signals = comp.get("signals") or []
            if score is not None and signals:
                scored_components.append((score, cname, signals))
        scored_components.sort(key=lambda x: x[0], reverse=True)
        for _, cname, signals in scored_components[:3]:
            if signals:
                key_drivers.append(f"{cname.capitalize()}: {signals[0]}")

    # ── Block-level labels (new three-block architecture) ───────────
    blocks = regime_data.get("blocks") or {}
    structural_label = None
    tape_label = None
    tactical_label = None
    for bkey, attr in [("structural", "structural_label"), ("tape", "tape_label"), ("tactical", "tactical_label")]:
        block = blocks.get(bkey)
        if block and isinstance(block, dict):
            if bkey == "structural":
                structural_label = block.get("label")
            elif bkey == "tape":
                tape_label = block.get("label")
            elif bkey == "tactical":
                tactical_label = block.get("label")

    result = {
        "risk_regime_label": risk_regime_label,
        "trend_label": trend_label,
        "vol_regime_label": vol_regime_label,
        "confidence": confidence,
        "key_drivers": key_drivers,
    }
    # Include block labels if available (new architecture)
    if structural_label is not None:
        result["structural_label"] = structural_label
    if tape_label is not None:
        result["tape_label"] = tape_label
    if tactical_label is not None:
        result["tactical_label"] = tactical_label

    return result


def compute_regime_deltas(
    engine_summary: dict[str, Any],
    model_summary: dict[str, Any],
) -> dict[str, Any]:
    """Compare engine and model regime summaries, returning per-row delta info.

    Delta logic:
      - String labels: case-insensitive match → {"match": True/False, "detail": ...}
      - Confidence: numeric tolerance ±0.10 → {"match": True/False, "detail": ...}
      - key_drivers: overlap count (informational, always "—")

    Returns:
      {
        "deltas": {"risk": {...}, "trend": {...}, "vol": {...}, "confidence": {...}},
        "disagreement_count": int (0-4),
      }
    """
    def _label_delta(a: str | None, b: str | None) -> dict[str, Any]:
        if a is None or b is None:
            return {"match": False, "detail": f"{a or '?'} vs {b or '?'}"}
        matched = a.strip().lower() == b.strip().lower()
        return {"match": matched, "detail": None if matched else f"{a} vs {b}"}

    def _confidence_delta(a: float | None, b: float | None) -> dict[str, Any]:
        if a is None or b is None:
            return {"match": False, "detail": f"{a} vs {b}"}
        try:
            diff = abs(float(a) - float(b))
            matched = diff <= 0.10
            return {"match": matched, "detail": None if matched else f"Δ={diff:.0%}"}
        except (TypeError, ValueError):
            return {"match": False, "detail": "invalid"}

    risk_delta = _label_delta(
        engine_summary.get("risk_regime_label"),
        model_summary.get("risk_regime_label"),
    )
    trend_delta = _label_delta(
        engine_summary.get("trend_label"),
        model_summary.get("trend_label"),
    )
    vol_delta = _label_delta(
        engine_summary.get("vol_regime_label"),
        model_summary.get("vol_regime_label"),
    )
    conf_delta = _confidence_delta(
        engine_summary.get("confidence"),
        model_summary.get("confidence"),
    )

    deltas = {
        "risk": risk_delta,
        "trend": trend_delta,
        "vol": vol_delta,
        "confidence": conf_delta,
    }

    # Block-level deltas (structural / tape / tactical)
    # Engine uses *_label, model uses *_assessment — compare cross-key
    for bkey, e_key, m_key in [
        ("structural", "structural_label", "structural_assessment"),
        ("tape", "tape_label", "tape_assessment"),
        ("tactical", "tactical_label", "tactical_assessment"),
    ]:
        e_val = engine_summary.get(e_key)
        m_val = model_summary.get(m_key)
        if e_val is not None or m_val is not None:
            deltas[bkey] = _label_delta(e_val, m_val)

    disagreement_count = sum(1 for v in deltas.values() if not v["match"])

    return {
        "deltas": deltas,
        "disagreement_count": disagreement_count,
    }


def analyze_regime(
    *,
    regime_data: dict[str, Any],
    playbook_data: dict[str, Any] | None = None,
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    """Call the local LLM with raw regime inputs only (no derived scores/labels).

    The model independently infers risk-on/off, trend, and volatility assessments
    from the raw market data.  BenTrade's computed regime labels, scores, and
    playbook recommendations are deliberately excluded to prevent anchoring.
    """
    import logging

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    _log = logging.getLogger("bentrade.model_analysis")

    # ── 1. Extract raw-only inputs ──────────────────────────────────
    regime_raw_inputs = _extract_regime_raw_inputs(regime_data)

    # Count included vs excluded fields for trace
    included_count = sum(1 for v in regime_raw_inputs.values() if v is not None)
    missing_fields = [k for k, v in regime_raw_inputs.items() if v is None]

    metadata = {
        "timestamp": regime_data.get("as_of"),
        "source_health": regime_data.get("source_health"),
    }

    # ── 2. Trace logging ────────────────────────────────────────────
    _log.info(
        "[MODEL_REGIME_TRACE] input_mode=raw_only "
        "included_fields=%d excluded_derived=%d excluded_names=%s missing_raw=%s",
        included_count,
        len(_REGIME_DERIVED_FIELDS),
        _REGIME_DERIVED_FIELDS,
        missing_fields,
    )

    # ── 3. Build prompt ─────────────────────────────────────────────
    prompt = (
        "You are an independent market regime analyst for an options trading platform.\n"
        "You will receive a JSON object with:\n"
        "  - regime_raw_inputs: raw market data values organized across three domains:\n"
        "    * Legacy factor data (index prices, moving averages, VIX, yields, breadth, RSI)\n"
        "    * Structural block pillars (liquidity conditions, cross-asset macro signals)\n"
        "    * Tape block pillars (breadth/participation engine detail, index trend/momentum)\n"
        "    * Tactical block pillars (volatility/options structure, flows/positioning, news/sentiment)\n"
        "  - metadata: timestamp and data-source health information\n\n"
        "IMPORTANT RULES:\n"
        "  1. Do NOT use any precomputed regime labels, scores, or playbook recommendations.\n"
        "     If a label is needed, infer it yourself from the raw inputs.\n"
        "  2. All assessments must be derived solely from the raw inputs provided.\n"
        "  3. If a raw input is null/missing, note it explicitly and reduce confidence.\n"
        "  4. Your analysis should cover THREE regime dimensions:\n"
        "     - Structural: Is the background environment supportive, restrictive, or unstable?\n"
        "     - Tape: Is the broad US market trending, broad, rotational, narrow, or weakening?\n"
        "     - Tactical: Is the short-term outlook expansionary, stable, compressing, or event-risk?\n\n"
        "Return valid JSON only (no markdown, no code fences) with exactly these keys:\n"
        "  risk_regime_label    – string, one of: 'Risk-On', 'Neutral', 'Risk-Off'\n"
        "  trend_label          – string, one of: 'Uptrend', 'Sideways', 'Downtrend'\n"
        "  vol_regime_label     – string, one of: 'Low', 'Moderate', 'High'\n"
        "  structural_assessment – string, one of: 'Supportive', 'Mixed', 'Restrictive', 'Unstable'\n"
        "     Your independent read of the macro/liquidity/rates environment.\n"
        "  tape_assessment       – string, one of: 'Trending', 'Broad', 'Rotational', 'Narrow', 'Weakening'\n"
        "     Your independent read of US market breadth and participation.\n"
        "  tactical_assessment   – string, one of: 'Expansionary', 'Stable', 'Compression', 'Event-Risk'\n"
        "     Your independent read of near-term forward pressure and tradability.\n"
        "  key_drivers          – string array of 3-5 short bullet points describing the top\n"
        "     factors driving your regime assessment\n"
        "  executive_summary    – string, 2-4 sentence overview of the current market regime.\n"
        "     Reference all three blocks (structural, tape, tactical) in your summary.\n"
        "  regime_breakdown     – object with keys: structural, tape, tactical, trend, volatility,\n"
        "     breadth, rates, momentum. Each value is a 2-3 sentence analysis.\n"
        "  what_works           – string array of 2-4 strategies/approaches that tend to work\n"
        "     in this regime environment\n"
        "  what_to_avoid        – string array of 2-4 strategies/approaches to avoid\n"
        "  primary_fit          – string explaining which options strategies fit this regime\n"
        "  avoid_rationale      – string explaining which strategies are riskier and why\n"
        "  change_triggers      – string array of 3-5 specific conditions that would shift\n"
        "     the regime\n"
        "  confidence_caveats   – string with confidence level and data-quality caveats\n"
        "  confidence           – float 0-1 representing your overall confidence\n"
        "  raw_inputs_used      – object listing each raw input name and value received,\n"
        "     plus a 'missing' array of input names that were null\n"
        "Do not include any keys beyond this schema."
    )

    # ── 4. Build user data (raw inputs only, no derived fields) ─────
    user_data: dict[str, Any] = {
        "regime_raw_inputs": regime_raw_inputs,
        "metadata": metadata,
    }

    # Verify exclusion: assert no derived fields leak into user_data
    _user_data_str = json.dumps(user_data, ensure_ascii=False, indent=None)
    for forbidden in ("regime_label", "regime_score", "suggested_playbook", "interpretation", "what_works", "what_to_avoid"):
        if f'"{forbidden}"' in _user_data_str:
            _log.error(
                "[MODEL_REGIME_TRACE] LEAK DETECTED: derived field '%s' found in user_data", forbidden
            )

    # ── Size budget: cap user_data at ~4000 chars to stay within safe token limits
    _MAX_USER_DATA_CHARS = 4000
    if len(_user_data_str) > _MAX_USER_DATA_CHARS:
        _log.warning(
            "[MODEL_REGIME_TRACE] user_data exceeds budget: %d chars (max %d). "
            "Trimming per-index trend data to SPY-only.",
            len(_user_data_str), _MAX_USER_DATA_CHARS,
        )
        # Progressive trim: drop non-SPY trend data first
        ti = regime_raw_inputs.get("trend_indexes")
        if isinstance(ti, dict) and len(ti) > 1:
            spy_only = {k: v for k, v in ti.items() if k == "SPY"}
            regime_raw_inputs["trend_indexes"] = spy_only if spy_only else None
        # Drop RSI per-index detail
        regime_raw_inputs.pop("rsi14_per_index", None)
        # Rebuild
        user_data["regime_raw_inputs"] = regime_raw_inputs
        _user_data_str = json.dumps(user_data, ensure_ascii=False, indent=None)
        _log.info(
            "[MODEL_REGIME_TRACE] after trim: %d chars", len(_user_data_str),
        )

    _log.debug(
        "[MODEL_REGIME_TRACE] user_data_snapshot=%s",
        _user_data_str[:2000],
    )

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": _user_data_str,
            },
        ],
        "max_tokens": 4096,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 5. Call the model (via shared transport layer) ─────────────
    _transport_result = _model_transport(
        task_type="regime_analysis",
        payload=payload,
        log_prefix="MODEL_REGIME",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    # ── 6. Parse + coerce ───────────────────────────────────────
    from common.json_repair import extract_and_repair_json
    parsed, method = extract_and_repair_json(assistant_text)

    if method:
        _log.info("[MODEL_REGIME] JSON extracted via method=%s", method)
    else:
        _log.warning(
            "[MODEL_REGIME] JSON extraction FAILED. assistant_text_len=%d "
            "first_200=%r last_200=%r",
            len(assistant_text or ""),
            (assistant_text or "")[:200],
            (assistant_text or "")[-200:],
        )

    # Diagnostic: log parsed keys before coercion
    if isinstance(parsed, dict):
        _log.info(
            "[MODEL_REGIME] parsed_keys=%s parsed_sample_values={%s}",
            list(parsed.keys()),
            ", ".join(
                f"{k}: {str(v)[:60]}"
                for k, v in list(parsed.items())[:5]
            ),
        )
    else:
        _log.warning("[MODEL_REGIME] parsed is not dict: type=%s val=%s",
                     type(parsed).__name__, str(parsed)[:300])

    normalized = _coerce_regime_model_output(parsed)
    if normalized is None:
        # Fallback: try to salvage useful prose from the raw response
        normalized = _build_plaintext_fallback(assistant_text, "regime")
        if normalized is None:
            raise ValueError("Model returned invalid regime analysis payload")
        _log.warning(
            "[MODEL_REGIME] JSON coerce failed; used plaintext_fallback"
        )

    # Diagnostic: log key fields after coercion
    _log.info(
        "[MODEL_REGIME] coerced: risk=%s trend=%s vol=%s conf=%s "
        "structural=%s tape=%s tactical=%s exec_summary=%s",
        normalized.get("risk_regime_label"),
        normalized.get("trend_label"),
        normalized.get("vol_regime_label"),
        normalized.get("confidence"),
        normalized.get("structural_assessment"),
        normalized.get("tape_assessment"),
        normalized.get("tactical_assessment"),
        "present" if normalized.get("executive_summary") else "MISSING",
    )

    # ── 7. Attach trace metadata ────────────────────────────────
    normalized["_trace"] = {
        "model_regime_input_mode": "raw_only",
        "included_fields_count": included_count,
        "excluded_fields_count": len(_REGIME_DERIVED_FIELDS),
        "excluded_derived_field_names": _REGIME_DERIVED_FIELDS,
        "missing_raw_fields": missing_fields,
        "regime_raw_inputs_snapshot": {
            k: v for k, v in regime_raw_inputs.items() if v is not None
        },
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }

    return normalized


def analyze_trade(
    trade: TradeContract,
    source: str,
    model_url: str | None = None,
    retries: int = 1,
    timeout: int = 180,
) -> dict[str, Any] | None:
    # Keep the legacy JSON contract exactly the same by delegating to the legacy implementation.
    # TODO(architecture): migrate implementation from common.utils into this module and delete legacy shim.
    from common import utils as legacy_utils

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    return legacy_utils._analyze_trade_with_model_legacy(
        trade.to_dict(),
        source,
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )


def analyze_stock_idea(
    *,
    symbol: str,
    idea: dict[str, Any],
    source: str,
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    import logging

    _log = logging.getLogger("bentrade.model_analysis")

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    prompt = (
        "You are a stock swing-trade analysis assistant.\n"
        "You will receive exactly one stock idea snapshot as JSON.\n"
        "Return valid JSON only (no markdown) with exactly these keys:\n"
        "recommendation (BUY|SELL|WAIT), confidence (0..1), summary (string),\n"
        "key_factors (string array), risks (string array), time_horizon (1D|1W|1M),\n"
        "trade_ideas (array of objects).\n"
        "trade_ideas may include stock action ideas and options strategy ideas.\n"
        "Do not include any keys beyond the required schema."
    )

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbol": str(symbol or "").upper(),
                        "source": str(source or "local_llm"),
                        "idea": idea,
                    },
                    ensure_ascii=False,
                    indent=None,
                ),
            },
        ],
        "max_tokens": 1800,
        "temperature": 0.0,
        "stream": False,
    }

    # ── Transport via shared seam (Step 12 migration) ──
    _transport_result = _model_transport(
        task_type="stock_idea",
        payload=payload,
        log_prefix="MODEL_STOCK_IDEA",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    # Try robust JSON extraction via repair pipeline first
    from common.json_repair import extract_and_repair_json
    parsed, method = extract_and_repair_json(assistant_text)

    if method:
        _log.info("[MODEL_STOCK_IDEA] JSON extracted via method=%s", method)

    normalized = _coerce_stock_model_output(parsed)
    if normalized is None:
        raise ValueError("Model returned invalid stock analysis payload")
    normalized["_trace"] = {
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }
    return normalized


# ── Stock Strategy Model Analysis (scanner TradeCard) ────────────────────

def _coerce_stock_strategy_output(candidate: Any) -> dict[str, Any] | None:
    """Normalize the LLM response for stock strategy analysis into the output contract.

    Output contract:
      recommendation: "BUY" | "PASS"
      score: int 0-100
      confidence: int 0-100
      summary: str
      key_drivers: [{ factor, impact, evidence }]
      risk_review: { primary_risks, volatility_risk, timing_risk, data_quality_flag }
      engine_vs_model: { engine_score, model_score, agreement, notes }
      data_quality: { provider, warnings }
    """
    if isinstance(candidate, list) and candidate:
        first = candidate[0]
        if isinstance(first, dict):
            candidate = first
    if not isinstance(candidate, dict):
        return None

    # ── Recommendation ──
    recommendation = str(candidate.get("recommendation") or "PASS").strip().upper()
    if recommendation not in {"BUY", "PASS"}:
        recommendation = "PASS"

    # ── Score ──
    score_raw = candidate.get("score")
    try:
        score = int(float(score_raw))
    except (TypeError, ValueError):
        score = 50
    score = max(0, min(score, 100))

    # ── Confidence ──
    confidence_raw = candidate.get("confidence")
    try:
        confidence = int(float(confidence_raw))
    except (TypeError, ValueError):
        confidence = 50
    # Handle 0-1 scale → 0-100
    if confidence <= 1:
        confidence = int(confidence * 100)
    confidence = max(0, min(confidence, 100))

    # ── Summary ──
    summary = str(candidate.get("summary") or "Model returned no summary.").strip()

    # ── Key Drivers ──
    raw_drivers = candidate.get("key_drivers") or []
    key_drivers: list[dict[str, str]] = []
    if isinstance(raw_drivers, list):
        for d in raw_drivers:
            if isinstance(d, dict):
                key_drivers.append({
                    "factor": str(d.get("factor") or d.get("name") or ""),
                    "impact": str(d.get("impact") or "neutral"),
                    "evidence": str(d.get("evidence") or d.get("detail") or ""),
                })
            elif isinstance(d, str) and d.strip():
                key_drivers.append({"factor": d.strip(), "impact": "neutral", "evidence": ""})

    # ── Risk Review ──
    rr_raw = candidate.get("risk_review") or {}
    if not isinstance(rr_raw, dict):
        rr_raw = {}
    risk_review = {
        "primary_risks": [str(r) for r in (rr_raw.get("primary_risks") or []) if isinstance(r, str) and r.strip()],
        "volatility_risk": str(rr_raw.get("volatility_risk") or "medium"),
        "timing_risk": str(rr_raw.get("timing_risk") or "medium"),
        "data_quality_flag": rr_raw.get("data_quality_flag"),
    }

    # ── Engine vs Model ──
    evm_raw = candidate.get("engine_vs_model") or {}
    if not isinstance(evm_raw, dict):
        evm_raw = {}
    engine_score_raw = evm_raw.get("engine_score")
    model_score_raw = evm_raw.get("model_score")
    try:
        engine_score_val = float(engine_score_raw) if engine_score_raw is not None else None
    except (TypeError, ValueError):
        engine_score_val = None
    try:
        model_score_val = float(model_score_raw) if model_score_raw is not None else None
    except (TypeError, ValueError):
        model_score_val = None

    agreement = str(evm_raw.get("agreement") or "mixed").lower()
    if agreement not in {"agree", "disagree", "mixed"}:
        agreement = "mixed"

    evm_notes = evm_raw.get("notes") or []
    if not isinstance(evm_notes, list):
        evm_notes = [str(evm_notes)] if evm_notes else []

    engine_vs_model = {
        "engine_score": engine_score_val,
        "model_score": model_score_val,
        "agreement": agreement,
        "notes": [str(n) for n in evm_notes if str(n or "").strip()],
    }

    # ── Data Quality ──
    dq_raw = candidate.get("data_quality") or {}
    if not isinstance(dq_raw, dict):
        dq_raw = {}
    data_quality = {
        "provider": str(dq_raw.get("provider") or "tradier"),
        "warnings": [str(w) for w in (dq_raw.get("warnings") or []) if isinstance(w, str) and w.strip()],
    }

    return {
        "recommendation": recommendation,
        "score": score,
        "confidence": confidence,
        "summary": summary,
        "key_drivers": key_drivers,
        "risk_review": risk_review,
        "engine_vs_model": engine_vs_model,
        "data_quality": data_quality,
    }


def _build_fallback_stock_analysis(
    candidate: dict[str, Any],
    strategy_id: str,
    reason: str,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """Build a valid PASS fallback when all JSON parsing/repair fails.

    This guarantees the endpoint NEVER returns a 500 for parse errors.
    The frontend receives a well-formed analysis with clear warnings.

    Derived fields:
      - score: candidate["composite_score"] (engine score) or 50
      - confidence: fixed 20 (low — indicates model failure, not model judgment)
      - recommendation: always "PASS" (cannot trust an unparsed model)
    """
    engine_score_raw = candidate.get("composite_score")
    try:
        engine_score = int(float(engine_score_raw))
    except (TypeError, ValueError):
        engine_score = 50

    return {
        "recommendation": "PASS",
        "score": max(0, min(engine_score, 100)),
        "confidence": 20,
        "summary": f"Model output could not be parsed. Defaulting to PASS. Reason: {reason}",
        "key_drivers": [],
        "risk_review": {
            "primary_risks": ["Model parse failure — review manually"],
            "volatility_risk": "unknown",
            "timing_risk": "unknown",
            "data_quality_flag": "MODEL_PARSE_FAILED",
        },
        "engine_vs_model": {
            "engine_score": engine_score,
            "model_score": None,
            "agreement": "mixed",
            "notes": [f"Model analysis unavailable: {reason}"],
        },
        "data_quality": {
            "provider": "tradier",
            "warnings": ["MODEL_PARSE_FAILED"],
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "_fallback": True,
        "_raw_text_preview": (raw_text or "")[:500] if raw_text else None,
    }


def analyze_stock_strategy(
    *,
    strategy_id: str,
    candidate: dict[str, Any],
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    """Run LLM model analysis on a stock strategy scanner candidate.

    Uses the stock_strategy_prompts library for strategy-specific prompt building.
    Returns the structured output matching the stock strategy analysis contract.

    Pipeline:
      1. Call LLM with strategy-specific prompt.
      2. Extract JSON via repair pipeline (handles fences, trailing commas, etc.).
      3. Normalize/coerce output to canonical schema.
      4. On parse failure: one retry asking LLM to fix its own JSON.
      5. On total failure: return a valid PASS fallback (never HTTP 500 for parse errors).

    Input fields:
      strategy_id: one of stock_pullback_swing, stock_momentum_breakout,
                   stock_mean_reversion, stock_volatility_expansion
      candidate: full candidate dict from the scanner (includes metrics, thesis, scores)

    Raises:
      LocalModelUnavailableError: if the local LLM endpoint is unreachable
      ValueError: if strategy_id is unknown
    """
    import logging

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    from common.json_repair import REPAIR_METRICS, extract_and_repair_json
    from common.stock_strategy_prompts import (
        STOCK_STRATEGY_SYSTEM_PROMPT,
        build_stock_strategy_user_prompt,
    )

    _log = logging.getLogger("bentrade.model_analysis")

    # Build prompts
    user_prompt = build_stock_strategy_user_prompt(strategy_id, candidate)
    symbol = candidate.get("symbol", "???")

    _log.info(
        "[MODEL_STOCK_STRATEGY_TRACE] strategy=%s symbol=%s engine_score=%s",
        strategy_id,
        symbol,
        candidate.get("composite_score"),
    )

    payload = {
        "messages": [
            {"role": "system", "content": STOCK_STRATEGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 2048,
        "temperature": 0.0,
    }

    # ── Primary transport via shared seam (Step 12 migration) ────
    _transport_result = _model_transport(
        task_type="stock_strategy",
        payload=payload,
        log_prefix="MODEL_STOCK_STRATEGY",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    _log.debug(
        "[MODEL_STOCK_STRATEGY_TRACE] raw_response_len=%d", len(assistant_text or ""),
    )

    # ── Parse + repair pipeline ──────────────────────────────────
    parsed, parse_method = extract_and_repair_json(assistant_text)

    if parse_method and parse_method != "direct":
        _log.info(
            "[MODEL_STOCK_STRATEGY_TRACE] JSON required repair: method=%s strategy=%s symbol=%s",
            parse_method, strategy_id, symbol,
        )

    # ── Normalize ────────────────────────────────────────────────
    normalized = _coerce_stock_strategy_output(parsed) if parsed is not None else None

    # ── Retry-with-fix on parse failure ──────────────────────────
    if normalized is None and assistant_text:
        _log.warning(
            "[MODEL_STOCK_STRATEGY_TRACE] parse failed, attempting retry-with-fix strategy=%s symbol=%s first_200=%r",
            strategy_id, symbol, (assistant_text or "")[:200],
        )

        fix_payload = {
            "messages": payload["messages"] + [
                {"role": "assistant", "content": assistant_text},
                {
                    "role": "user",
                    "content": (
                        "Your previous response was not valid JSON. "
                        "Return ONLY the corrected JSON object — no markdown fences, "
                        "no explanation, no trailing commas. Start with { and end with }."
                    ),
                },
            ],
            "max_tokens": 2048,
            "temperature": 0.0,
        }

        try:
            _fix_result = _model_transport(
                task_type="stock_strategy_fix",
                payload=fix_payload,
                log_prefix="MODEL_STOCK_STRATEGY",
                model_url=model_url,
                retries=0,
                timeout=timeout,
            )
            fix_text = _fix_result.content
            if fix_text:
                parsed2, parse_method2 = extract_and_repair_json(fix_text)
                normalized = _coerce_stock_strategy_output(parsed2) if parsed2 is not None else None
                if normalized is not None:
                    parse_method = f"retry_fix+{parse_method2 or 'unknown'}"
                    _log.info(
                        "[MODEL_STOCK_STRATEGY_TRACE] retry-with-fix SUCCEEDED strategy=%s symbol=%s method=%s",
                        strategy_id, symbol, parse_method,
                    )
        except (RequestException, LocalModelUnavailableError):
            _log.warning("[MODEL_STOCK_STRATEGY_TRACE] retry-fix network error, proceeding to fallback")

    # ── Fallback on total failure ────────────────────────────────
    if normalized is None:
        _log.error(
            "[MODEL_STOCK_STRATEGY_TRACE] ALL PARSE FAILED — returning fallback strategy=%s symbol=%s metrics=%s",
            strategy_id, symbol, dict(REPAIR_METRICS),
        )
        return _build_fallback_stock_analysis(
            candidate, strategy_id,
            reason="JSON extraction + repair + retry all failed",
            raw_text=assistant_text,
        )

    # ── Success path ─────────────────────────────────────────────
    normalized["timestamp"] = datetime.now(timezone.utc).isoformat()

    # Tag parse method in data_quality for diagnostics
    if parse_method and parse_method != "direct":
        dq = normalized.setdefault("data_quality", {})
        warnings = dq.setdefault("warnings", [])
        warnings.append(f"JSON_REPAIR:{parse_method}")

    _log.info(
        "[MODEL_STOCK_STRATEGY_TRACE] OK strategy=%s symbol=%s recommendation=%s score=%s parse=%s",
        strategy_id, symbol,
        normalized.get("recommendation"),
        normalized.get("score"),
        parse_method,
    )

    normalized["_trace"] = {
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }
    return normalized


# ═══════════════════════════════════════════════════════════════════════
# TMC FINAL TRADE DECISION
# ═══════════════════════════════════════════════════════════════════════


def _coerce_tmc_final_decision_output(raw: Any) -> dict[str, Any] | None:
    """Normalize the LLM response for a TMC final trade decision.

    Output contract:
      decision: "EXECUTE" | "PASS"
      conviction: int 0-100
      decision_summary: str
      technical_analysis: { setup_quality_assessment, key_metrics_cited,
        trend_context, momentum_read, volatility_read, volume_read } | None
      factors_considered: [{ category, factor, assessment, weight, detail }]
      market_alignment: { overall, detail }
      risk_assessment: { primary_risks, biggest_concern, risk_reward_verdict }
      what_would_change_my_mind: str
      engine_comparison: { engine_score, model_score, agreement, reasoning }
    """
    if isinstance(raw, list) and raw:
        raw = raw[0] if isinstance(raw[0], dict) else None
    if not isinstance(raw, dict):
        return None

    # ── Decision ──
    decision = str(raw.get("decision") or "PASS").strip().upper()
    if decision not in {"EXECUTE", "PASS"}:
        # Accept BUY as alias for EXECUTE
        decision = "EXECUTE" if decision == "BUY" else "PASS"

    # ── Conviction ──
    conv_raw = raw.get("conviction")
    try:
        conviction = int(float(conv_raw))
    except (TypeError, ValueError):
        conviction = 50
    if conviction <= 1:
        conviction = int(conviction * 100)
    conviction = max(0, min(conviction, 100))

    # ── Decision Summary ──
    decision_summary = str(
        raw.get("decision_summary") or raw.get("summary") or "No summary provided."
    ).strip()

    # ── Factors Considered ──
    raw_factors = raw.get("factors_considered") or []
    factors_considered: list[dict[str, str]] = []
    valid_categories = {"trade_setup", "market_environment", "risk_reward", "timing", "data_quality"}
    valid_assessments = {"favorable", "unfavorable", "neutral", "concerning"}
    valid_weights = {"high", "medium", "low"}
    if isinstance(raw_factors, list):
        for f in raw_factors:
            if isinstance(f, dict):
                cat = str(f.get("category") or "trade_setup").lower()
                if cat not in valid_categories:
                    cat = "trade_setup"
                assess = str(f.get("assessment") or "neutral").lower()
                if assess not in valid_assessments:
                    assess = "neutral"
                wt = str(f.get("weight") or "medium").lower()
                if wt not in valid_weights:
                    wt = "medium"
                factors_considered.append({
                    "category": cat,
                    "factor": str(f.get("factor") or f.get("name") or ""),
                    "assessment": assess,
                    "weight": wt,
                    "detail": str(f.get("detail") or f.get("evidence") or ""),
                })

    # ── Market Alignment ──
    ma_raw = raw.get("market_alignment") or {}
    if not isinstance(ma_raw, dict):
        ma_raw = {}
    overall = str(ma_raw.get("overall") or "neutral").lower()
    if overall not in {"aligned", "neutral", "conflicting"}:
        overall = "neutral"
    market_alignment = {
        "overall": overall,
        "detail": str(ma_raw.get("detail") or ""),
    }

    # ── Risk Assessment ──
    ra_raw = raw.get("risk_assessment") or raw.get("risk_review") or {}
    if not isinstance(ra_raw, dict):
        ra_raw = {}
    primary_risks = [
        str(r) for r in (ra_raw.get("primary_risks") or [])
        if isinstance(r, str) and r.strip()
    ]
    biggest_concern = str(ra_raw.get("biggest_concern") or "").strip()
    rrv = str(ra_raw.get("risk_reward_verdict") or "marginal").lower()
    if rrv not in {"favorable", "marginal", "unfavorable"}:
        rrv = "marginal"
    risk_assessment = {
        "primary_risks": primary_risks,
        "biggest_concern": biggest_concern,
        "risk_reward_verdict": rrv,
    }

    # ── What Would Change My Mind ──
    change_mind = str(
        raw.get("what_would_change_my_mind") or ""
    ).strip()

    # ── Engine Comparison ──
    ec_raw = raw.get("engine_comparison") or raw.get("engine_vs_model") or {}
    if not isinstance(ec_raw, dict):
        ec_raw = {}
    try:
        ec_engine = float(ec_raw["engine_score"]) if ec_raw.get("engine_score") is not None else None
    except (TypeError, ValueError):
        ec_engine = None
    try:
        ec_model = float(ec_raw["model_score"]) if ec_raw.get("model_score") is not None else None
    except (TypeError, ValueError):
        ec_model = None
    agreement = str(ec_raw.get("agreement") or "partial").lower()
    if agreement not in {"agree", "disagree", "partial"}:
        agreement = "partial"
    engine_comparison = {
        "engine_score": ec_engine,
        "model_score": ec_model,
        "agreement": agreement,
        "reasoning": str(ec_raw.get("reasoning") or ec_raw.get("notes") or ""),
    }

    # ── Technical Analysis (structured metrics breakdown) ──
    ta_raw = raw.get("technical_analysis") or {}
    technical_analysis = None
    if isinstance(ta_raw, dict) and ta_raw:
        kmc = ta_raw.get("key_metrics_cited")
        if isinstance(kmc, dict):
            # Coerce all values to numeric where possible
            cleaned_kmc: dict[str, Any] = {}
            for k, v in kmc.items():
                try:
                    cleaned_kmc[str(k)] = float(v) if v is not None else None
                except (TypeError, ValueError):
                    cleaned_kmc[str(k)] = v
            kmc = cleaned_kmc
        else:
            kmc = {}

        technical_analysis = {
            "setup_quality_assessment": str(ta_raw.get("setup_quality_assessment") or "").strip(),
            "key_metrics_cited": kmc,
            "trend_context": str(ta_raw.get("trend_context") or "").strip(),
            "momentum_read": str(ta_raw.get("momentum_read") or "").strip(),
            "volatility_read": str(ta_raw.get("volatility_read") or "").strip(),
            "volume_read": str(ta_raw.get("volume_read") or "").strip(),
        }

    return {
        "decision": decision,
        "conviction": conviction,
        "decision_summary": decision_summary,
        "technical_analysis": technical_analysis,
        "factors_considered": factors_considered,
        "market_alignment": market_alignment,
        "risk_assessment": risk_assessment,
        "what_would_change_my_mind": change_mind,
        "engine_comparison": engine_comparison,
    }


def _build_fallback_tmc_decision(
    candidate: dict[str, Any],
    reason: str,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """Build a valid PASS fallback when all JSON parsing/repair fails.

    Derived fields:
      - conviction: fixed 10 (very low — model produced no usable output)
      - engine_score: candidate["composite_score"] or candidate["setup_quality"]
    """
    engine_score_raw = candidate.get("composite_score") or candidate.get("setup_quality")
    try:
        engine_score = float(engine_score_raw)
    except (TypeError, ValueError):
        engine_score = None

    return {
        "decision": "PASS",
        "conviction": 10,
        "decision_summary": f"Model output could not be parsed. Defaulting to PASS. Reason: {reason}",
        "factors_considered": [],
        "market_alignment": {
            "overall": "neutral",
            "detail": "Unable to assess — model parse failure.",
        },
        "risk_assessment": {
            "primary_risks": ["Model parse failure — review manually"],
            "biggest_concern": "Model did not produce a usable analysis",
            "risk_reward_verdict": "unfavorable",
        },
        "what_would_change_my_mind": "",
        "engine_comparison": {
            "engine_score": engine_score,
            "model_score": None,
            "agreement": "partial",
            "reasoning": f"Model analysis unavailable: {reason}",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "_fallback": True,
        "_raw_text_preview": (raw_text or "")[:500] if raw_text else None,
    }


def analyze_tmc_final_decision(
    *,
    candidate: dict[str, Any],
    market_picture_context: dict[str, Any] | None = None,
    strategy_id: str | None = None,
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    """Run TMC final trade decision analysis via LLM.

    Uses the dedicated TMC final decision prompt which provides the model
    with full trade setup data AND market picture context, asking for a
    portfolio-manager-level decision.

    Pipeline:
      1. Build TMC final decision prompt with all available data.
      2. Call LLM with dedicated system + user prompt.
      3. Parse and coerce to TMC final decision output contract.
      4. On parse failure: one retry asking LLM to fix its JSON.
      5. On total failure: return a valid PASS fallback.

    Args:
        candidate: Full or compact candidate dict.
        market_picture_context: Full 6-engine market picture context.
        strategy_id: Strategy identifier.
        model_url: LLM endpoint URL.  Defaults to model_router.
        retries: Network retry count.
        timeout: Request timeout in seconds.

    Returns:
        TMC final decision dict matching the output contract.

    Raises:
        LocalModelUnavailableError: if the LLM endpoint is unreachable.
    """
    import logging

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    from common.json_repair import REPAIR_METRICS, extract_and_repair_json
    from common.tmc_final_decision_prompts import (
        TMC_FINAL_DECISION_SYSTEM_PROMPT,
        build_tmc_final_decision_prompt,
    )

    _log = logging.getLogger("bentrade.model_analysis")

    # Build prompts
    user_prompt = build_tmc_final_decision_prompt(
        candidate=candidate,
        market_picture_context=market_picture_context,
        strategy_id=strategy_id,
    )
    symbol = candidate.get("symbol", "???")

    _log.info(
        "[TMC_FINAL_DECISION_TRACE] symbol=%s strategy=%s engine_score=%s",
        symbol,
        strategy_id or "unknown",
        candidate.get("composite_score") or candidate.get("setup_quality"),
    )

    payload = {
        "messages": [
            {"role": "system", "content": TMC_FINAL_DECISION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 3000,
        "temperature": 0.0,
    }

    # ── Primary transport via shared seam (Step 12 migration) ────
    _transport_result = _model_transport(
        task_type="tmc_final_decision",
        payload=payload,
        log_prefix="TMC_FINAL_DECISION",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    _log.debug("[TMC_FINAL_DECISION_TRACE] raw_response_len=%d", len(assistant_text or ""))

    # ── Parse + repair ───────────────────────────────────────────
    parsed, parse_method = extract_and_repair_json(assistant_text)

    if parse_method and parse_method != "direct":
        _log.info(
            "[TMC_FINAL_DECISION_TRACE] JSON required repair: method=%s symbol=%s",
            parse_method, symbol,
        )

    normalized = _coerce_tmc_final_decision_output(parsed) if parsed is not None else None

    # ── Retry-with-fix on parse failure ──────────────────────────
    if normalized is None and assistant_text:
        _log.warning(
            "[TMC_FINAL_DECISION_TRACE] parse failed, attempting retry-with-fix symbol=%s",
            symbol,
        )
        fix_payload = {
            "messages": payload["messages"] + [
                {"role": "assistant", "content": assistant_text},
                {"role": "user", "content": (
                    "Your previous response was not valid JSON. "
                    "Please return ONLY the raw JSON object matching the schema "
                    "from the system prompt. No commentary, no fences. "
                    "Start with { and end with }."
                )},
            ],
            "max_tokens": 3000,
            "temperature": 0.0,
        }
        try:
            _fix_result = _model_transport(
                task_type="tmc_final_decision_fix",
                payload=fix_payload,
                log_prefix="TMC_FINAL_DECISION",
                model_url=model_url,
                retries=0,
                timeout=timeout,
            )
            fix_text = _fix_result.content
            if fix_text:
                parsed2, parse_method2 = extract_and_repair_json(fix_text)
                normalized = _coerce_tmc_final_decision_output(parsed2) if parsed2 is not None else None
                if normalized is not None:
                    parse_method = f"retry_fix+{parse_method2 or 'unknown'}"
                    _log.info(
                        "[TMC_FINAL_DECISION_TRACE] retry-with-fix SUCCEEDED symbol=%s method=%s",
                        symbol, parse_method,
                    )
        except (RequestException, LocalModelUnavailableError):
            _log.warning("[TMC_FINAL_DECISION_TRACE] retry-fix network error, proceeding to fallback")

    # ── Fallback on total failure ────────────────────────────────
    if normalized is None:
        _log.error(
            "[TMC_FINAL_DECISION_TRACE] ALL PARSE FAILED — returning fallback symbol=%s",
            symbol,
        )
        return _build_fallback_tmc_decision(
            candidate,
            reason="JSON extraction + repair + retry all failed",
            raw_text=assistant_text,
        )

    # ── Success path ─────────────────────────────────────────────
    normalized["timestamp"] = datetime.now(timezone.utc).isoformat()

    if parse_method and parse_method != "direct":
        normalized.setdefault("_parse_method", parse_method)

    _log.info(
        "[TMC_FINAL_DECISION_TRACE] OK symbol=%s decision=%s conviction=%s parse=%s",
        symbol,
        normalized.get("decision"),
        normalized.get("conviction"),
        parse_method,
    )

    normalized["_trace"] = {
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }
    return normalized


# ── News & Sentiment Model Analysis ─────────────────────────────────────


def _coerce_news_sentiment_model_output(candidate: Any) -> dict[str, Any] | None:
    """Normalise the LLM response for news/sentiment analysis into a consistent dict.

    Accepts the enhanced schema with headline_drivers, major_headlines,
    score_drivers, market_implications, uncertainty_flags, and trader_takeaway.
    Falls back gracefully for any missing optional sections.
    """
    if isinstance(candidate, list) and candidate:
        first = candidate[0]
        if isinstance(first, dict):
            candidate = first
    if not isinstance(candidate, dict):
        return None

    out: dict[str, Any] = {}

    # ── Label (expanded set) ────────────────────────────────────
    label = str(candidate.get("label") or candidate.get("regime_label") or "NEUTRAL").strip().upper()
    valid_labels = {"BULLISH", "BEARISH", "MIXED", "NEUTRAL", "RISK-OFF", "RISK-ON"}
    out["label"] = label if label in valid_labels else "NEUTRAL"

    # ── Score 0-100 ─────────────────────────────────────────────
    score_raw = candidate.get("score")
    try:
        out["score"] = max(0.0, min(float(score_raw), 100.0))
    except (TypeError, ValueError):
        out["score"] = None

    # ── Confidence 0-1 ──────────────────────────────────────────
    conf_raw = candidate.get("confidence")
    try:
        out["confidence"] = round(max(0.0, min(float(conf_raw), 1.0)), 2)
    except (TypeError, ValueError):
        out["confidence"] = None

    # ── Tone ────────────────────────────────────────────────────
    tone = candidate.get("tone") or candidate.get("headline_tone")
    out["tone"] = str(tone).strip() if isinstance(tone, str) and tone.strip() else None

    # ── Summary (required) ──────────────────────────────────────
    summary = candidate.get("summary") or candidate.get("executive_summary")
    out["summary"] = _safe_summary_text(summary)

    # ── Headline drivers (list of dicts) ────────────────────────
    hd_raw = candidate.get("headline_drivers")
    if isinstance(hd_raw, list):
        drivers = []
        for item in hd_raw[:8]:
            if isinstance(item, dict):
                drivers.append({
                    "theme": str(item.get("theme") or "").strip(),
                    "impact": str(item.get("impact") or "neutral").strip().lower(),
                    "strength": max(1, min(int(item.get("strength") or 1), 5)) if item.get("strength") is not None else 1,
                    "explanation": str(item.get("explanation") or "").strip(),
                })
        out["headline_drivers"] = drivers if drivers else None
    else:
        out["headline_drivers"] = None

    # ── Major headlines (list of dicts) ─────────────────────────
    mh_raw = candidate.get("major_headlines")
    if isinstance(mh_raw, list):
        headlines = []
        for item in mh_raw[:10]:
            if isinstance(item, dict):
                headlines.append({
                    "headline": str(item.get("headline") or "").strip(),
                    "category": str(item.get("category") or "macro").strip().lower(),
                    "market_impact": str(item.get("market_impact") or "neutral").strip().lower(),
                    "why_it_matters": str(item.get("why_it_matters") or "").strip(),
                })
        out["major_headlines"] = headlines if headlines else None
    else:
        out["major_headlines"] = None

    # ── Score drivers (bullish/bearish/offsetting factors) ──────
    sd_raw = candidate.get("score_drivers")
    if isinstance(sd_raw, dict):
        out["score_drivers"] = {
            "bullish_factors": _coerce_string_list(sd_raw.get("bullish_factors"), max_items=8),
            "bearish_factors": _coerce_string_list(sd_raw.get("bearish_factors"), max_items=8),
            "offsetting_factors": _coerce_string_list(sd_raw.get("offsetting_factors"), max_items=8),
        }
    else:
        out["score_drivers"] = None

    # ── Market implications ─────────────────────────────────────
    mi_raw = candidate.get("market_implications")
    if isinstance(mi_raw, dict):
        out["market_implications"] = {
            k: str(mi_raw.get(k) or "").strip() or None
            for k in ("equities", "volatility", "rates", "energy_or_commodities", "sector_rotation")
        }
    else:
        out["market_implications"] = None

    # ── Uncertainty flags ───────────────────────────────────────
    out["uncertainty_flags"] = _coerce_string_list(candidate.get("uncertainty_flags"), max_items=8)

    # ── Trader takeaway ─────────────────────────────────────────
    tt = candidate.get("trader_takeaway")
    out["trader_takeaway"] = str(tt).strip() if isinstance(tt, str) and tt.strip() else None

    # ── Legacy fields (kept for backward compat) ────────────────
    for key in ("sentiment_outlook", "risk_assessment"):
        val = candidate.get(key)
        out[key] = str(val).strip() if isinstance(val, str) and val.strip() else None

    for key in ("dominant_narratives", "underpriced_risks", "key_drivers", "change_triggers"):
        out[key] = _coerce_string_list(candidate.get(key), max_items=8)

    return out


def _safe_summary_text(val: Any, fallback: str | None = None) -> str | None:
    """Extract a clean text summary from a model output value.

    Handles str (pass-through), dict (extract nested .text/.summary/.content),
    list (join items). Returns *fallback* for None or unrecognised types.
    """
    if isinstance(val, str) and val.strip():
        return val.strip()
    if isinstance(val, dict):
        for key in ("text", "summary", "content", "executive_summary", "description"):
            nested = val.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        return fallback
    if isinstance(val, list):
        joined = " ".join(str(item).strip() for item in val if str(item or "").strip())
        return joined if joined else fallback
    return fallback


def _coerce_string_list(val: Any, *, max_items: int = 8) -> list[str] | None:
    """Coerce a value to a list of non-empty strings, or None."""
    if isinstance(val, list):
        items = [str(item).strip() for item in val if str(item or "").strip()][:max_items]
        return items if items else None
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return None


def _coerce_by_module(candidate: dict, module: str) -> dict[str, Any] | None:
    """Dispatch *candidate* to the correct module-specific coercer."""
    _dispatch = {
        "regime": _coerce_regime_model_output,
        "news_sentiment": _coerce_news_sentiment_model_output,
        "cross_asset": _coerce_cross_asset_model_output,
        "breadth": _coerce_breadth_model_output,
        "volatility": _coerce_vol_model_output,
        "flows_positioning": _coerce_flows_positioning_model_output,
        "liquidity_conditions": _coerce_liquidity_conditions_model_output,
    }
    coercer = _dispatch.get(module)
    if coercer is None:
        return None
    return coercer(candidate)


def _build_plaintext_fallback(raw_text: str, module: str) -> dict[str, Any] | None:
    """Build a minimal model result from raw text when JSON parsing fails.

    If the model returned useful prose but not valid JSON, we wrap it in the
    standard schema so the UI can still display something useful instead of
    showing a "malformed response" error.

    If the raw text looks like JSON, attempt to extract score/label/summary
    from within it before falling back to storing the text as-is.
    """
    text = (raw_text or "").strip()
    if not text or len(text) < 20:
        return None

    # Strip <think>/<scratchpad> blocks so embedded JSON is reachable
    import re as _re
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
    text = _re.sub(r"<scratchpad>.*?</scratchpad>", "", text, flags=_re.DOTALL).strip()
    if not text or len(text) < 20:
        return None

    # Attempt to recover structured fields from JSON-like raw text
    extracted_score = None
    extracted_label = None
    extracted_summary = None
    # Try direct parse first, then extract embedded JSON block
    json_text = text
    if not text.lstrip().startswith("{"):
        # Try to find an embedded JSON object in the text
        brace_match = _re.search(r"\{[\s\S]+\}", text)
        if brace_match:
            json_text = brace_match.group(0)
        else:
            json_text = None
    if json_text:
        try:
            candidate = json.loads(json_text)
            if isinstance(candidate, dict):
                # Try full module-specific coercion first — recovers ALL structured
                # fields (regime labels, assessments, headline_drivers, etc.)
                module_coerced = _coerce_by_module(candidate, module)
                if module_coerced is not None:
                    module_coerced["_plaintext_fallback"] = True
                    module_coerced["_module"] = module
                    module_coerced.setdefault("uncertainty_flags", [])
                    if isinstance(module_coerced["uncertainty_flags"], list):
                        module_coerced["uncertainty_flags"].append(
                            "Recovered from raw JSON fallback"
                        )
                    return module_coerced

                # Minimal extraction if module coercer didn't apply
                s = candidate.get("score")
                if s is not None:
                    try:
                        extracted_score = max(0.0, min(float(s), 100.0))
                    except (TypeError, ValueError):
                        pass
                l = candidate.get("label")
                if isinstance(l, str) and l.strip():
                    extracted_label = l.strip().upper()
                sm = _safe_summary_text(candidate.get("summary"))
                if sm:
                    extracted_summary = sm
                c = candidate.get("confidence")
                extracted_conf = None
                if c is not None:
                    try:
                        extracted_conf = round(max(0.0, min(float(c), 1.0)), 2)
                    except (TypeError, ValueError):
                        pass
                if extracted_score is not None or extracted_summary:
                    return {
                        "label": extracted_label or "ANALYSIS",
                        "score": extracted_score,
                        "confidence": extracted_conf,
                        "summary": extracted_summary,
                        "pillar_analysis": {},
                        "trader_takeaway": candidate.get("trader_takeaway") or "",
                        "uncertainty_flags": ["Recovered from raw JSON fallback"],
                        "_plaintext_fallback": True,
                        "_module": module,
                    }
        except (json.JSONDecodeError, TypeError):
            pass

    # Truncate overly long text
    summary = text[:1500].strip()
    if len(text) > 1500:
        summary += "…"
    return {
        "label": "ANALYSIS",
        "score": None,
        "confidence": None,
        "summary": summary,
        "pillar_analysis": {},
        "trader_takeaway": "",
        "uncertainty_flags": ["Model returned plain text instead of structured JSON"],
        "_plaintext_fallback": True,
        "_module": module,
    }


def _extract_news_raw_evidence(
    items: list[dict[str, Any]],
    macro_context: dict[str, Any],
) -> dict[str, Any]:
    """Build raw evidence packet for the model — NO pre-computed scores/labels.

    Includes:
      - headlines: source, headline, category, published_at, symbols (max 40)
      - macro_snapshot: VIX, yields, oil, fed funds, spread
    Explicitly excluded:
      - sentiment_score, sentiment_label (engine-computed)
      - regime_label, overall_score (engine-derived)
      - aggregation data (pressure counts, narratives)
    """
    headlines = []
    for item in (items or [])[:40]:
        headlines.append({
            "source": item.get("source"),
            "headline": item.get("headline"),
            "category": item.get("category"),
            "published_at": item.get("published_at"),
            "symbols": (item.get("symbols") or [])[:5],
        })

    macro_raw = {}
    for key in ("vix", "us_10y_yield", "us_2y_yield", "fed_funds_rate",
                "oil_wti", "usd_index", "yield_curve_spread"):
        macro_raw[key] = macro_context.get(key)

    return {
        "headlines": headlines,
        "headline_count": len(headlines),
        "macro_snapshot": macro_raw,
    }


# Fields explicitly excluded from model input to prevent anchoring
_NEWS_SENTIMENT_EXCLUDED_FIELDS = [
    "sentiment_score",
    "sentiment_label",
    "regime_label",
    "overall_score",
    "headline_pressure_24h",
    "headline_pressure_72h",
    "top_narratives",
    "divergence",
    "stress_level",
]


def analyze_news_sentiment(
    *,
    items: list[dict[str, Any]],
    macro_context: dict[str, Any],
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    """Call the local LLM with raw news/macro evidence only (no derived scores).

    The model independently assesses market sentiment from raw headlines and
    macro data.  Engine-computed sentiment scores, labels, and aggregation
    are deliberately excluded to prevent anchoring.
    """
    import logging
    import re as _re

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    _log = logging.getLogger("bentrade.model_analysis")

    # ── 1. Extract raw evidence ─────────────────────────────────
    raw_evidence = _extract_news_raw_evidence(items, macro_context)

    included_headlines = raw_evidence["headline_count"]
    macro_fields = raw_evidence["macro_snapshot"]
    missing_macro = [k for k, v in macro_fields.items() if v is None]

    # ── 2. Trace logging ────────────────────────────────────────
    _log.info(
        "[MODEL_NEWS_TRACE] input_mode=raw_only headlines=%d "
        "excluded_derived=%s missing_macro=%s",
        included_headlines,
        _NEWS_SENTIMENT_EXCLUDED_FIELDS,
        missing_macro,
    )

    # ── 3. Build prompt ─────────────────────────────────────────
    prompt = (
        "You are the BenTrade Market News Analyst. Analyze the supplied news, sentiment, "
        "macro, and market context and return ONLY valid JSON matching the required schema.\n\n"
        "Your task is to produce an institutional-style market news brief for traders. Focus on:\n"
        "- the dominant headline clusters\n"
        "- the narratives driving risk appetite\n"
        "- what is bullish, bearish, and conflicting\n"
        "- why the final score, label, and confidence are justified\n\n"
        "Rules:\n"
        "- Return JSON only\n"
        "- No markdown\n"
        "- No prose outside JSON\n"
        "- No chain-of-thought\n"
        "- No hidden reasoning\n"
        "- No <think> tags\n"
        "- No filler language\n"
        "- Summarize clusters of news, not random isolated stories\n"
        "- Keep score, label, confidence, and explanations internally consistent\n\n"
        "The summary MUST explicitly answer:\n"
        "- what happened\n"
        "- why markets care\n"
        "- what pushed risk up\n"
        "- what pushed risk down\n"
        "- what the trader should do with the information\n\n"
        "Scoring guide:\n"
        "- 0-20 = strongly bearish / risk-off\n"
        "- 21-40 = bearish\n"
        "- 41-59 = mixed / conflicted\n"
        "- 60-79 = constructive / mildly bullish\n"
        "- 80-100 = strongly bullish / risk-on\n\n"
        "If evidence conflicts:\n"
        "- use MIXED or NEUTRAL when appropriate\n"
        "- lower confidence\n"
        "- explicitly include offsetting factors and uncertainty flags\n\n"
        "Required JSON schema (return EXACTLY this shape):\n"
        "{\n"
        '  "label": "BULLISH | BEARISH | MIXED | NEUTRAL | RISK-OFF | RISK-ON",\n'
        '  "score": <float 0-100>,\n'
        '  "confidence": <float 0-1>,\n'
        '  "tone": "<string>",\n'
        '  "summary": "<2-4 sentence executive market brief>",\n'
        '  "headline_drivers": [\n'
        '    {"theme": "<short theme title>", "impact": "bullish|bearish|mixed|neutral", '
        '"strength": <1-5>, "explanation": "<why this theme matters>"}\n'
        "  ],\n"
        '  "major_headlines": [\n'
        '    {"headline": "<cleaned headline>", '
        '"category": "macro|geopolitics|rates|commodities|earnings|sector|policy|sentiment", '
        '"market_impact": "bullish|bearish|mixed|neutral", '
        '"why_it_matters": "<1-2 sentence explanation>"}\n'
        "  ],\n"
        '  "score_drivers": {\n'
        '    "bullish_factors": ["<specific factor>"],\n'
        '    "bearish_factors": ["<specific factor>"],\n'
        '    "offsetting_factors": ["<specific balancing/conflicting factor>"]\n'
        "  },\n"
        '  "market_implications": {\n'
        '    "equities": "<brief interpretation>",\n'
        '    "volatility": "<brief interpretation>",\n'
        '    "rates": "<brief interpretation>",\n'
        '    "energy_or_commodities": "<brief interpretation>",\n'
        '    "sector_rotation": "<brief interpretation>"\n'
        "  },\n"
        '  "uncertainty_flags": ["<uncertainty or conflict in the signal>"],\n'
        '  "trader_takeaway": "<2-4 sentence practical trader takeaway>"\n'
        "}\n\n"
        "Do not include any keys beyond this schema."
    )

    # ── 4. Build user data (raw evidence only) ──────────────────
    user_data_str = json.dumps(raw_evidence, ensure_ascii=False, indent=None)

    # Verify no derived fields leaked
    for forbidden in _NEWS_SENTIMENT_EXCLUDED_FIELDS:
        if f'"{forbidden}"' in user_data_str:
            _log.error(
                "[MODEL_NEWS_TRACE] LEAK DETECTED: derived field '%s' in user_data",
                forbidden,
            )

    _log.debug("[MODEL_NEWS_TRACE] user_data_snapshot=%s", user_data_str[:2000])

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_data_str},
        ],
        "max_tokens": 2500,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 5. Call the model (via shared transport layer) ─────────────
    _transport_result = _model_transport(
        task_type="news_sentiment",
        payload=payload,
        log_prefix="MODEL_NEWS",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    # ── 6. Parse + coerce ───────────────────────────────────────
    from common.json_repair import extract_and_repair_json
    parsed, method = extract_and_repair_json(assistant_text)

    if method:
        _log.info("[MODEL_NEWS] JSON extracted via method=%s", method)

    normalized = _coerce_news_sentiment_model_output(parsed)
    if normalized is None:
        normalized = _build_plaintext_fallback(assistant_text, "news_sentiment")
        method = "plaintext_fallback"
        if normalized is None:
            raise ValueError("Model returned invalid news sentiment payload")

    # ── 7. Attach trace metadata ────────────────────────────────
    normalized["_trace"] = {
        "input_mode": "raw_only",
        "headlines_provided": included_headlines,
        "excluded_derived_fields": _NEWS_SENTIMENT_EXCLUDED_FIELDS,
        "missing_macro_fields": missing_macro,
        "json_parse_method": method,
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }

    return normalized


# ────────────────────────────────────────────────────────────────────────────
# Breadth & Participation Model Analysis
# ────────────────────────────────────────────────────────────────────────────

# Fields excluded from breadth model input to prevent anchoring
_BREADTH_EXCLUDED_FIELDS = [
    "score",
    "label",
    "short_label",
    "summary",
    "trader_takeaway",
    "positive_contributors",
    "negative_contributors",
    "conflicting_signals",
    "confidence_score",
    "signal_quality",
]


def _extract_breadth_raw_evidence(engine_result: dict[str, Any]) -> dict[str, Any]:
    """Build raw evidence packet for the breadth model — NO derived scores/labels.

    Includes only:
      - raw_inputs: participation, trend, volume, leadership, stability sub-dicts
      - pillar_scores: the 5 numeric scores (the model may reinterpret these)
      - pillar_weights: how pillars are weighted
      - universe: coverage stats
      - warnings: data quality warnings
      - missing_inputs: what data was unavailable

    Explicitly excluded:
      - composite score, label, summary (engine-derived)
      - positive/negative contributors, conflicting signals (engine narratives)
      - confidence_score, signal_quality (engine assessments)
    """
    raw_inputs = engine_result.get("raw_inputs", {})
    evidence = {
        "raw_inputs": {
            "participation": raw_inputs.get("participation", {}),
            "trend": raw_inputs.get("trend", {}),
            "volume": raw_inputs.get("volume", {}),
            "leadership": raw_inputs.get("leadership", {}),
            "stability": raw_inputs.get("stability", {}),
        },
        "pillar_scores": engine_result.get("pillar_scores", {}),
        "pillar_weights": engine_result.get("pillar_weights", {}),
        "universe": engine_result.get("universe", {}),
        "warnings": engine_result.get("warnings", []),
        "missing_inputs": engine_result.get("missing_inputs", []),
    }
    return evidence


def _coerce_breadth_model_output(candidate: Any) -> dict[str, Any] | None:
    """Normalize LLM breadth analysis output into a consistent schema.

    Returns None only if candidate is not a dict at all.
    Provides safe defaults for missing fields so partial responses are usable.
    """
    if not isinstance(candidate, dict):
        return None

    label = candidate.get("label")
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    summary = candidate.get("summary")

    # Provide defaults for missing required fields instead of returning None
    if label is None:
        label = "ANALYSIS"
    if summary is None:
        summary = "Model did not provide a summary."

    # Clamp score 0-100
    try:
        score = float(score)
        score = max(0.0, min(100.0, score))
    except (TypeError, ValueError):
        score = None

    # Clamp confidence 0-1
    try:
        confidence = float(confidence) if confidence is not None else 0.5
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    result: dict[str, Any] = {
        "label": str(label).strip().upper(),
        "score": round(score, 1) if score is not None else None,
        "confidence": round(confidence, 2),
        "summary": str(summary).strip(),
    }

    # Pillar interpretations
    pa = candidate.get("pillar_analysis")
    if isinstance(pa, dict):
        result["pillar_analysis"] = {
            k: str(v).strip() if isinstance(v, str) else v
            for k, v in pa.items()
        }
    else:
        result["pillar_analysis"] = {}

    # Breadth drivers
    bd = candidate.get("breadth_drivers")
    if isinstance(bd, dict):
        result["breadth_drivers"] = {
            "constructive_factors": _coerce_string_list(bd.get("constructive_factors")) or [],
            "warning_factors": _coerce_string_list(bd.get("warning_factors")) or [],
            "conflicting_factors": _coerce_string_list(bd.get("conflicting_factors")) or [],
        }
    else:
        result["breadth_drivers"] = {
            "constructive_factors": [],
            "warning_factors": [],
            "conflicting_factors": [],
        }

    # Market implications
    mi = candidate.get("market_implications")
    if isinstance(mi, dict):
        result["market_implications"] = {
            "directional_bias": str(mi.get("directional_bias", "")).strip(),
            "position_sizing": str(mi.get("position_sizing", "")).strip(),
            "strategy_recommendation": str(mi.get("strategy_recommendation", "")).strip(),
            "risk_level": str(mi.get("risk_level", "")).strip(),
            "sector_tilt": str(mi.get("sector_tilt", "")).strip(),
        }
    else:
        result["market_implications"] = {}

    # Uncertainty flags
    result["uncertainty_flags"] = _coerce_string_list(candidate.get("uncertainty_flags")) or []

    # Trader takeaway
    ta = candidate.get("trader_takeaway")
    result["trader_takeaway"] = str(ta).strip() if ta else ""

    return result


def analyze_breadth_participation(
    *,
    engine_result: dict[str, Any],
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    """Call the local LLM with raw breadth evidence only (no derived labels/summary).

    The model independently assesses market breadth from raw pillar data.
    Engine-computed labels, summaries, and narrative contributors are
    deliberately excluded to prevent anchoring.
    """
    import logging
    import re as _re

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    _log = logging.getLogger("bentrade.model_analysis")

    # ── 1. Extract raw evidence ─────────────────────────────────
    raw_evidence = _extract_breadth_raw_evidence(engine_result)

    # ── 2. Trace logging ────────────────────────────────────────
    pillar_scores = raw_evidence.get("pillar_scores", {})
    universe = raw_evidence.get("universe", {})
    _log.info(
        "[MODEL_BREADTH_TRACE] input_mode=raw_only "
        "pillar_scores=%s universe_coverage=%.1f%% "
        "excluded_derived=%s warnings=%d missing=%d",
        pillar_scores,
        universe.get("coverage_pct", 0),
        _BREADTH_EXCLUDED_FIELDS,
        len(raw_evidence.get("warnings", [])),
        len(raw_evidence.get("missing_inputs", [])),
    )

    # ── 3. Build prompt ─────────────────────────────────────────
    prompt = (
        "You are the BenTrade Breadth & Participation Analyst. Analyze the supplied "
        "market breadth data and return ONLY valid JSON matching the required schema.\n\n"
        "Your task is to produce an institutional-style market breadth assessment for "
        "options traders. Focus on:\n"
        "- Whether the rally/sell-off is broad or narrow\n"
        "- Whether participation is expanding or contracting\n"
        "- What the advance/decline, volume, trend, and leadership data says about conviction\n"
        "- How breadth conditions affect risk for income-style options strategies\n"
        "- Whether breadth supports or undermines the current price trend\n\n"
        "Rules:\n"
        "- Return JSON only\n"
        "- No markdown\n"
        "- No prose outside JSON\n"
        "- No chain-of-thought or <think> tags\n"
        "- Keep score, label, confidence, and explanations internally consistent\n\n"
        "The summary MUST explicitly answer:\n"
        "- Is the market rally/decline broadly supported or driven by a few names?\n"
        "- Are trend and volume confirming or diverging?\n"
        "- What does leadership quality tell us about sustainability?\n"
        "- What should a risk-defined options trader do with this information?\n\n"
        "Scoring guide:\n"
        "- 0-20 = extremely narrow / deteriorating breadth\n"
        "- 21-40 = weak / selective participation\n"
        "- 41-59 = mixed / transitional breadth\n"
        "- 60-79 = constructive / broadening participation\n"
        "- 80-100 = strong / robust broad rally\n\n"
        "Label options: BROAD_RALLY | NARROW_RALLY | DETERIORATING | WEAK | "
        "RECOVERING | MIXED | STRONG\n\n"
        "Required JSON schema (return EXACTLY this shape):\n"
        "{\n"
        '  "label": "BROAD_RALLY | NARROW_RALLY | DETERIORATING | WEAK | RECOVERING | MIXED | STRONG",\n'
        '  "score": <float 0-100>,\n'
        '  "confidence": <float 0-1>,\n'
        '  "summary": "<2-4 sentence executive breadth brief>",\n'
        '  "pillar_analysis": {\n'
        '    "participation": "<interpretation of A/D data, pct advancing, new highs/lows>",\n'
        '    "trend": "<interpretation of MA breadth — pct above 200/50/20 DMA>",\n'
        '    "volume": "<interpretation of up/down volume balance>",\n'
        '    "leadership": "<interpretation of EW vs CW, outperformance, sector dispersion>",\n'
        '    "stability": "<interpretation of breadth consistency and mean reversion risk>"\n'
        "  },\n"
        '  "breadth_drivers": {\n'
        '    "constructive_factors": ["<factor supporting breadth>"],\n'
        '    "warning_factors": ["<factor weakening breadth>"],\n'
        '    "conflicting_factors": ["<signal conflict or divergence>"]\n'
        "  },\n"
        '  "market_implications": {\n'
        '    "directional_bias": "<bullish/bearish/neutral lean from breadth>",\n'
        '    "position_sizing": "<recommendation on sizing given breadth>",\n'
        '    "strategy_recommendation": "<which options strategies breadth supports>",\n'
        '    "risk_level": "<low/moderate/elevated/high>",\n'
        '    "sector_tilt": "<sectors breadth favors or warns against>"\n'
        "  },\n"
        '  "uncertainty_flags": ["<data gap, divergence, or low-confidence area>"],\n'
        '  "trader_takeaway": "<2-4 sentence practical trader takeaway for options income strategies>"\n'
        "}\n\n"
        "Do not include any keys beyond this schema."
    )

    # ── 4. Build user data (raw evidence only) ──────────────────
    user_data_str = json.dumps(raw_evidence, ensure_ascii=False, indent=None)

    # Verify no derived fields leaked
    for forbidden in _BREADTH_EXCLUDED_FIELDS:
        if f'"{forbidden}"' in user_data_str:
            _log.error(
                "[MODEL_BREADTH_TRACE] LEAK DETECTED: derived field '%s' in user_data",
                forbidden,
            )

    _log.debug("[MODEL_BREADTH_TRACE] user_data_snapshot=%s", user_data_str[:2000])

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_data_str},
        ],
        "max_tokens": 2500,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 5. Call the model (via shared transport layer) ─────────────
    _transport_result = _model_transport(
        task_type="breadth_participation",
        payload=payload,
        log_prefix="MODEL_BREADTH",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    # ── 6. Parse + coerce ───────────────────────────────────────
    from common.json_repair import extract_and_repair_json
    parsed, method = extract_and_repair_json(assistant_text)

    if method:
        _log.info("[MODEL_BREADTH] JSON extracted via method=%s", method)

    normalized = _coerce_breadth_model_output(parsed)
    if normalized is None:
        normalized = _build_plaintext_fallback(assistant_text, "breadth")
        method = "plaintext_fallback"
        if normalized is None:
            raise ValueError("Model returned invalid breadth analysis payload")

    # ── 7. Attach trace metadata ────────────────────────────────
    normalized["_trace"] = {
        "input_mode": "raw_only",
        "pillar_scores_provided": pillar_scores,
        "excluded_derived_fields": _BREADTH_EXCLUDED_FIELDS,
        "universe_coverage_pct": universe.get("coverage_pct", 0),
        "json_parse_method": method,
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }

    return normalized


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks, scratchpad, and hidden reasoning from LLM output.

    Handles nested tags, unclosed tags, and partial reasoning blocks.
    """
    import re as _re
    if not text:
        return text
    # Remove <think>...</think> blocks (greedy, handles nested)
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove unclosed <think> tags (everything from <think> to end)
    text = _re.sub(r"<think>.*$", "", text, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove <scratchpad>...</scratchpad> blocks
    text = _re.sub(r"<scratchpad>.*?</scratchpad>", "", text, flags=_re.DOTALL | _re.IGNORECASE)
    text = _re.sub(r"<scratchpad>.*$", "", text, flags=_re.DOTALL | _re.IGNORECASE)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════
# VOLATILITY & OPTIONS STRUCTURE MODEL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

# Fields excluded from volatility model input to prevent anchoring
_VOL_EXCLUDED_FIELDS = [
    "score",
    "label",
    "short_label",
    "summary",
    "trader_takeaway",
    "positive_contributors",
    "negative_contributors",
    "conflicting_signals",
    "confidence_score",
    "signal_quality",
]


def _extract_vol_raw_evidence(engine_result: dict[str, Any]) -> dict[str, Any]:
    """Build raw evidence for the volatility model — NO derived scores/labels.

    Includes only:
      - raw_inputs: regime, structure, skew, positioning, strategy sub-dicts
      - pillar_scores: the 5 numeric scores
      - pillar_weights: how pillars are weighted
      - strategy_scores: individual strategy suitability scores
      - warnings: data quality warnings
      - missing_inputs: what data was unavailable
    """
    raw_inputs = engine_result.get("raw_inputs", {})
    evidence = {
        "raw_inputs": {
            "regime": raw_inputs.get("regime", {}),
            "structure": raw_inputs.get("structure", {}),
            "skew": raw_inputs.get("skew", {}),
            "positioning": raw_inputs.get("positioning", {}),
            "strategy": raw_inputs.get("strategy", {}),
        },
        "pillar_scores": engine_result.get("pillar_scores", {}),
        "pillar_weights": engine_result.get("pillar_weights", {}),
        "strategy_scores": engine_result.get("strategy_scores", {}),
        "warnings": engine_result.get("warnings", []),
        "missing_inputs": engine_result.get("missing_inputs", []),
    }
    return evidence


def _coerce_vol_model_output(candidate: Any) -> dict[str, Any] | None:
    """Normalize LLM volatility analysis output into a consistent schema."""
    if not isinstance(candidate, dict):
        return None

    label = candidate.get("label") or "ANALYSIS"
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    summary = _safe_summary_text(
        candidate.get("summary"), fallback="Model did not provide a summary."
    )

    try:
        score = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        score = None

    try:
        confidence = max(0.0, min(1.0, float(confidence) if confidence is not None else 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    result: dict[str, Any] = {
        "label": str(label).strip().upper(),
        "score": round(score, 1) if score is not None else None,
        "confidence": round(confidence, 2),
        "summary": str(summary).strip(),
    }

    # Pillar interpretations
    pa = candidate.get("pillar_analysis")
    if isinstance(pa, dict):
        result["pillar_analysis"] = {
            k: str(v).strip() if isinstance(v, str) else v
            for k, v in pa.items()
        }
    else:
        result["pillar_analysis"] = {}

    # Vol drivers
    vd = candidate.get("vol_drivers")
    if isinstance(vd, dict):
        result["vol_drivers"] = {
            "favorable_factors": _coerce_string_list(vd.get("favorable_factors")) or [],
            "warning_factors": _coerce_string_list(vd.get("warning_factors")) or [],
            "conflicting_factors": _coerce_string_list(vd.get("conflicting_factors")) or [],
        }
    else:
        result["vol_drivers"] = {
            "favorable_factors": [],
            "warning_factors": [],
            "conflicting_factors": [],
        }

    # Strategy implications
    si = candidate.get("strategy_implications")
    if isinstance(si, dict):
        result["strategy_implications"] = {
            "premium_selling": str(si.get("premium_selling", "")).strip(),
            "directional": str(si.get("directional", "")).strip(),
            "vol_structure": str(si.get("vol_structure", "")).strip(),
            "hedging": str(si.get("hedging", "")).strip(),
            "position_sizing": str(si.get("position_sizing", "")).strip(),
            "risk_level": str(si.get("risk_level", "")).strip(),
        }
    else:
        result["strategy_implications"] = {}

    result["uncertainty_flags"] = _coerce_string_list(candidate.get("uncertainty_flags")) or []

    ta = candidate.get("trader_takeaway")
    result["trader_takeaway"] = str(ta).strip() if ta else ""

    return result


def analyze_volatility_options(
    *,
    engine_result: dict[str, Any],
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    """Call the local LLM with raw volatility evidence only.

    The model independently assesses volatility conditions from raw pillar data.
    Engine-computed labels, summaries, and narratives are deliberately excluded.
    """
    import logging
    import re as _re

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    _log = logging.getLogger("bentrade.model_analysis")

    # ── 1. Extract raw evidence ─────────────────────────────────
    raw_evidence = _extract_vol_raw_evidence(engine_result)

    pillar_scores = raw_evidence.get("pillar_scores", {})

    _log.info(
        "[MODEL_VOL_TRACE] input_mode=raw_only "
        "pillar_scores=%s excluded_derived=%s warnings=%d missing=%d",
        pillar_scores,
        _VOL_EXCLUDED_FIELDS,
        len(raw_evidence.get("warnings", [])),
        len(raw_evidence.get("missing_inputs", [])),
    )

    # ── 2. Build prompt ─────────────────────────────────────────
    prompt = (
        "You are the BenTrade Volatility & Options Structure Analyst. Analyze the "
        "supplied volatility data and return ONLY valid JSON matching the required schema.\n\n"
        "Your task is to produce an institutional-style volatility assessment for "
        "options income traders. Focus on:\n"
        "- What the VIX level, trend, and regime tell us about market fear\n"
        "- Whether term structure (contango/backwardation) supports premium selling\n"
        "- What IV vs realized vol says about option pricing (overpriced = sell, cheap = buy)\n"
        "- How skew and tail risk signals affect strategy selection\n"
        "- Which specific strategies are best suited to current conditions\n\n"
        "Rules:\n"
        "- Return JSON only\n"
        "- No markdown\n"
        "- No prose outside JSON\n"
        "- No chain-of-thought or <think> tags\n"
        "- Keep score, label, confidence, and explanations internally consistent\n\n"
        "Scoring guide (higher = more favorable for premium selling):\n"
        "- 0-20 = extreme vol stress / crisis\n"
        "- 21-40 = elevated risk / defensive\n"
        "- 41-59 = mixed / transitional\n"
        "- 60-79 = constructive for selling\n"
        "- 80-100 = strongly favorable for premium selling\n\n"
        "Label options: PREMIUM_SELLING_FAVORED | CONSTRUCTIVE | MIXED | FRAGILE | "
        "RISK_ELEVATED | VOL_STRESS | DEFENSIVE\n\n"
        "Required JSON schema (return EXACTLY this shape):\n"
        "{\n"
        '  "label": "PREMIUM_SELLING_FAVORED | CONSTRUCTIVE | MIXED | FRAGILE | '
        'RISK_ELEVATED | VOL_STRESS | DEFENSIVE",\n'
        '  "score": <float 0-100>,\n'
        '  "confidence": <float 0-1>,\n'
        '  "summary": "<2-4 sentence executive volatility brief>",\n'
        '  "pillar_analysis": {\n'
        '    "volatility_regime": "<VIX level, trend, IV rank interpretation>",\n'
        '    "volatility_structure": "<term structure shape, IV vs RV assessment>",\n'
        '    "tail_risk_skew": "<skew and tail risk assessment>",\n'
        '    "positioning_options_posture": "<put/call ratios, option richness>",\n'
        '    "strategy_suitability": "<which strategies current conditions favor>"\n'
        "  },\n"
        '  "vol_drivers": {\n'
        '    "favorable_factors": ["<factor supporting premium selling>"],\n'
        '    "warning_factors": ["<factor creating risk>"],\n'
        '    "conflicting_factors": ["<signal conflict or divergence>"]\n'
        "  },\n"
        '  "strategy_implications": {\n'
        '    "premium_selling": "<iron condors, credit spreads assessment>",\n'
        '    "directional": "<debit spreads, long straddles assessment>",\n'
        '    "vol_structure": "<calendars, diagonals assessment>",\n'
        '    "hedging": "<protective puts, collars assessment>",\n'
        '    "position_sizing": "<sizing recommendation given vol conditions>",\n'
        '    "risk_level": "<low/moderate/elevated/high>"\n'
        "  },\n"
        '  "uncertainty_flags": ["<data gap, divergence, or low-confidence area>"],\n'
        '  "trader_takeaway": "<2-4 sentence practical takeaway for options income traders>"\n'
        "}\n\n"
        "Do not include any keys beyond this schema."
    )

    # ── 3. Build user data ──────────────────────────────────────
    user_data_str = json.dumps(raw_evidence, ensure_ascii=False, indent=None)

    for forbidden in _VOL_EXCLUDED_FIELDS:
        if f'"{forbidden}"' in user_data_str:
            _log.error(
                "[MODEL_VOL_TRACE] LEAK DETECTED: derived field '%s' in user_data",
                forbidden,
            )

    _log.debug("[MODEL_VOL_TRACE] user_data_snapshot=%s", user_data_str[:2000])

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_data_str},
        ],
        "max_tokens": 2500,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 4. Call the model (via shared transport layer) ─────────────
    _transport_result = _model_transport(
        task_type="volatility_options",
        payload=payload,
        log_prefix="MODEL_VOL",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    # ── 5. Parse + coerce ───────────────────────────────────────
    from common.json_repair import extract_and_repair_json
    parsed, method = extract_and_repair_json(assistant_text)

    if method:
        _log.info("[MODEL_VOL] JSON extracted via method=%s", method)

    normalized = _coerce_vol_model_output(parsed)
    if normalized is None:
        normalized = _build_plaintext_fallback(assistant_text, "volatility")
        method = "plaintext_fallback"
        if normalized is None:
            raise ValueError("Model returned invalid volatility analysis payload")

    # ── 6. Attach trace metadata ────────────────────────────────
    normalized["_trace"] = {
        "input_mode": "raw_only",
        "pillar_scores_provided": pillar_scores,
        "excluded_derived_fields": _VOL_EXCLUDED_FIELDS,
        "json_parse_method": method,
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }

    return normalized


# ═══════════════════════════════════════════════════════════════════════════
# CROSS-ASSET / MACRO CONFIRMATION MODEL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

# Fields excluded from cross-asset model input to prevent anchoring
_CROSS_ASSET_EXCLUDED_FIELDS = [
    "score",
    "label",
    "short_label",
    "summary",
    "trader_takeaway",
    "confirming_signals",
    "contradicting_signals",
    "mixed_signals",
    "confidence_score",
    "signal_quality",
]


def _extract_cross_asset_raw_evidence(engine_result: dict[str, Any]) -> dict[str, Any]:
    """Build raw evidence for the cross-asset model — NO derived scores/labels.

    Includes only:
      - raw_inputs: rates, dollar_commodity, credit, defensive_growth, coherence sub-dicts
      - pillar_scores: the 5 numeric scores
      - pillar_weights: how pillars are weighted
      - warnings: data quality warnings
      - missing_inputs: what data was unavailable
    """
    raw_inputs = engine_result.get("raw_inputs", {})
    return {
        "raw_inputs": {
            "rates": raw_inputs.get("rates", {}),
            "dollar_commodity": raw_inputs.get("dollar_commodity", {}),
            "credit": raw_inputs.get("credit", {}),
            "defensive_growth": raw_inputs.get("defensive_growth", {}),
            "coherence": raw_inputs.get("coherence", {}),
        },
        "pillar_scores": engine_result.get("pillar_scores", {}),
        "pillar_weights": engine_result.get("pillar_weights", {}),
        "warnings": engine_result.get("warnings", []),
        "missing_inputs": engine_result.get("missing_inputs", []),
    }


def _coerce_cross_asset_model_output(candidate: Any) -> dict[str, Any] | None:
    """Normalize LLM cross-asset analysis output into a consistent schema."""
    if not isinstance(candidate, dict):
        return None

    label = candidate.get("label") or "ANALYSIS"
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    summary = _safe_summary_text(
        candidate.get("summary"), fallback="Model did not provide a summary."
    )

    try:
        score = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        score = None

    try:
        confidence = max(0.0, min(1.0, float(confidence) if confidence is not None else 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    result: dict[str, Any] = {
        "label": str(label).strip().upper(),
        "score": round(score, 1) if score is not None else None,
        "confidence": round(confidence, 2),
        "summary": str(summary).strip(),
    }

    pa = candidate.get("pillar_analysis")
    if isinstance(pa, dict):
        result["pillar_analysis"] = {
            k: str(v).strip() if isinstance(v, str) else v
            for k, v in pa.items()
        }
    else:
        result["pillar_analysis"] = {}

    md = candidate.get("macro_drivers")
    if isinstance(md, dict):
        result["macro_drivers"] = {
            "confirming_factors": _coerce_string_list(md.get("confirming_factors")) or [],
            "contradicting_factors": _coerce_string_list(md.get("contradicting_factors")) or [],
            "ambiguous_factors": _coerce_string_list(md.get("ambiguous_factors")) or [],
        }
    else:
        result["macro_drivers"] = {
            "confirming_factors": [],
            "contradicting_factors": [],
            "ambiguous_factors": [],
        }

    ti = candidate.get("trading_implications")
    if isinstance(ti, dict):
        result["trading_implications"] = {
            "directional_bias": str(ti.get("directional_bias", "")).strip(),
            "position_sizing": str(ti.get("position_sizing", "")).strip(),
            "strategy_recommendation": str(ti.get("strategy_recommendation", "")).strip(),
            "risk_level": str(ti.get("risk_level", "")).strip(),
            "hedging_guidance": str(ti.get("hedging_guidance", "")).strip(),
        }
    else:
        result["trading_implications"] = {}

    result["uncertainty_flags"] = _coerce_string_list(candidate.get("uncertainty_flags")) or []

    ta = candidate.get("trader_takeaway")
    result["trader_takeaway"] = str(ta).strip() if ta else ""

    return result


def analyze_cross_asset_macro(
    *,
    engine_result: dict[str, Any],
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    """Call the local LLM with raw cross-asset evidence only.

    The model independently assesses cross-asset macro conditions from raw
    pillar data. Engine-computed labels, summaries, and narratives are
    deliberately excluded to prevent anchoring.
    """
    import logging
    import re as _re

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    _log = logging.getLogger("bentrade.model_analysis")

    raw_evidence = _extract_cross_asset_raw_evidence(engine_result)

    pillar_scores = raw_evidence.get("pillar_scores", {})
    _log.info(
        "[MODEL_CROSS_ASSET_TRACE] input_mode=raw_only "
        "pillar_scores=%s excluded_derived=%s warnings=%d missing=%d",
        pillar_scores,
        _CROSS_ASSET_EXCLUDED_FIELDS,
        len(raw_evidence.get("warnings", [])),
        len(raw_evidence.get("missing_inputs", [])),
    )

    prompt = (
        "You are the BenTrade Cross-Asset & Macro Confirmation Analyst. Analyze the supplied "
        "cross-asset macro data and return ONLY valid JSON matching the required schema.\n\n"
        "Your job: Determine whether non-equity markets (rates, credit, commodities, currencies) "
        "are confirming or contradicting the equity story.\n\n"
        "REQUIRED JSON SCHEMA:\n"
        "{\n"
        '  "label": "STRONG CONFIRMATION|CONFIRMING|PARTIAL CONFIRMATION|MIXED SIGNALS|PARTIAL CONTRADICTION|STRONG CONTRADICTION",\n'
        '  "score": <number 0-100>,\n'
        '  "confidence": <number 0.0-1.0>,\n'
        '  "summary": "<2-3 sentence macro assessment>",\n'
        '  "pillar_analysis": {\n'
        '    "rates_yield_curve": "<interpretation>",\n'
        '    "dollar_commodity": "<interpretation>",\n'
        '    "credit_risk_appetite": "<interpretation>",\n'
        '    "defensive_vs_growth": "<interpretation>",\n'
        '    "macro_coherence": "<interpretation>"\n'
        "  },\n"
        '  "macro_drivers": {\n'
        '    "confirming_factors": ["<factor1>", ...],\n'
        '    "contradicting_factors": ["<factor1>", ...],\n'
        '    "ambiguous_factors": ["<factor1>", ...]\n'
        "  },\n"
        '  "trading_implications": {\n'
        '    "directional_bias": "<bullish|neutral|bearish>",\n'
        '    "position_sizing": "<full|reduced|minimal>",\n'
        '    "strategy_recommendation": "<specific guidance>",\n'
        '    "risk_level": "<low|moderate|elevated|high>",\n'
        '    "hedging_guidance": "<specific guidance>"\n'
        "  },\n"
        '  "uncertainty_flags": ["<flag1>", ...],\n'
        '  "trader_takeaway": "<one actionable paragraph>"\n'
        "}\n\n"
        "SCORING GUIDE:\n"
        "  85-100 = Strong Confirmation — most cross-asset signals confirm equities\n"
        "  70-84  = Confirming — clear majority confirms\n"
        "  55-69  = Partial Confirmation — more confirm than contradict\n"
        "  45-54  = Mixed Signals — roughly split\n"
        "  30-44  = Partial Contradiction — more contradict than confirm\n"
        "  0-29   = Strong Contradiction — most signals contradict equities\n\n"
        "IMPORTANT: Base your analysis on the RAW DATA provided. Do not invent data points.\n\n"
        "DATA SOURCE AWARENESS (proxy honesty):\n"
        "  - Copper price (FRED PCOPPUSDM) is a MONTHLY series. It may be up to 30 days stale.\n"
        "    Do not treat it as confirming/contradicting real-time moves.\n"
        "  - Gold (FRED GOLDAMGBD228NLBM) has a ~1 business day delay.\n"
        "  - Credit spreads (IG OAS, HY OAS) have a 1-2 business day delay.\n"
        "  - USD index is a trade-weighted broad index proxy, not DXY.\n"
        "  - Oil (WTI) is inherently ambiguous: declining oil can mean supply glut (neutral)\n"
        "    OR demand destruction (bearish). If oil is between $45–$85, treat it as ambiguous\n"
        "    rather than forcing a directional interpretation.\n"
        "  - If data is missing or stale, reflect that as LOWER confidence, not as a zero value."
    )

    user_data_str = json.dumps(raw_evidence, ensure_ascii=False, indent=None)

    for forbidden in _CROSS_ASSET_EXCLUDED_FIELDS:
        if f'"{forbidden}"' in user_data_str:
            _log.error(
                "[MODEL_CROSS_ASSET_TRACE] LEAK DETECTED: derived field '%s' in user_data",
                forbidden,
            )

    _log.debug("[MODEL_CROSS_ASSET_TRACE] user_data_snapshot=%s", user_data_str[:2000])

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_data_str},
        ],
        "max_tokens": 2500,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 5. Call the model (via shared transport layer) ─────────────
    _transport_result = _model_transport(
        task_type="cross_asset_macro",
        payload=payload,
        log_prefix="MODEL_CROSS_ASSET",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    # ── 6. Parse + coerce ───────────────────────────────────────
    from common.json_repair import extract_and_repair_json
    parsed, method = extract_and_repair_json(assistant_text)

    if method:
        _log.info("[MODEL_CROSS_ASSET] JSON extracted via method=%s", method)

    normalized = _coerce_cross_asset_model_output(parsed)
    if normalized is None:
        normalized = _build_plaintext_fallback(assistant_text, "cross_asset")
        method = "plaintext_fallback"
        if normalized is None:
            raise ValueError("Model returned invalid cross-asset analysis payload")

    # ── 7. Attach trace metadata ────────────────────────────────
    normalized["_trace"] = {
        "input_mode": "raw_only",
        "pillar_scores_provided": pillar_scores,
        "excluded_derived_fields": _CROSS_ASSET_EXCLUDED_FIELDS,
        "json_parse_method": method,
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }

    return normalized


# ═══════════════════════════════════════════════════════════════════════════
# FLOWS & POSITIONING MODEL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

# Fields excluded from flows positioning model input to prevent anchoring
_FLOWS_POSITIONING_EXCLUDED_FIELDS = [
    "score",
    "label",
    "short_label",
    "summary",
    "trader_takeaway",
    "positive_contributors",
    "negative_contributors",
    "conflicting_signals",
    "confidence_score",
    "signal_quality",
    "strategy_bias",
]


def _extract_flows_positioning_raw_evidence(engine_result: dict[str, Any]) -> dict[str, Any]:
    """Build raw evidence for the flows positioning model — NO derived scores/labels.

    Includes only:
      - raw_inputs: positioning, crowding, squeeze, flow, stability sub-dicts
      - pillar_scores: the 5 numeric scores
      - pillar_weights: how pillars are weighted
      - warnings: data quality warnings
      - missing_inputs: what data was unavailable
    """
    raw_inputs = engine_result.get("raw_inputs", {})
    return {
        "raw_inputs": {
            "positioning": raw_inputs.get("positioning", {}),
            "crowding": raw_inputs.get("crowding", {}),
            "squeeze": raw_inputs.get("squeeze", {}),
            "flow": raw_inputs.get("flow", {}),
            "stability": raw_inputs.get("stability", {}),
        },
        "pillar_scores": engine_result.get("pillar_scores", {}),
        "pillar_weights": engine_result.get("pillar_weights", {}),
        "warnings": engine_result.get("warnings", []),
        "missing_inputs": engine_result.get("missing_inputs", []),
    }


def _coerce_flows_positioning_model_output(candidate: Any) -> dict[str, Any] | None:
    """Normalize LLM flows & positioning analysis output into a consistent schema."""
    if not isinstance(candidate, dict):
        return None

    label = candidate.get("label") or "ANALYSIS"
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    summary = _safe_summary_text(
        candidate.get("summary"), fallback="Model did not provide a summary."
    )

    try:
        score = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        score = None

    try:
        confidence = max(0.0, min(1.0, float(confidence) if confidence is not None else 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    result: dict[str, Any] = {
        "label": str(label).strip().upper(),
        "score": round(score, 1) if score is not None else None,
        "confidence": round(confidence, 2),
        "summary": str(summary).strip(),
    }

    pa = candidate.get("pillar_analysis")
    if isinstance(pa, dict):
        result["pillar_analysis"] = {
            k: str(v).strip() if isinstance(v, str) else v
            for k, v in pa.items()
        }
    else:
        result["pillar_analysis"] = {}

    fd = candidate.get("flow_drivers")
    if isinstance(fd, dict):
        result["flow_drivers"] = {
            "supportive_factors": _coerce_string_list(fd.get("supportive_factors")) or [],
            "risk_factors": _coerce_string_list(fd.get("risk_factors")) or [],
            "ambiguous_factors": _coerce_string_list(fd.get("ambiguous_factors")) or [],
        }
    else:
        result["flow_drivers"] = {
            "supportive_factors": [],
            "risk_factors": [],
            "ambiguous_factors": [],
        }

    ti = candidate.get("trading_implications")
    if isinstance(ti, dict):
        result["trading_implications"] = {
            "continuation_support": str(ti.get("continuation_support", "")).strip(),
            "reversal_risk": str(ti.get("reversal_risk", "")).strip(),
            "position_sizing": str(ti.get("position_sizing", "")).strip(),
            "strategy_recommendation": str(ti.get("strategy_recommendation", "")).strip(),
            "squeeze_guidance": str(ti.get("squeeze_guidance", "")).strip(),
        }
    else:
        result["trading_implications"] = {}

    result["uncertainty_flags"] = _coerce_string_list(candidate.get("uncertainty_flags")) or []

    ta = candidate.get("trader_takeaway")
    result["trader_takeaway"] = str(ta).strip() if ta else ""

    return result


def analyze_flows_positioning(
    *,
    engine_result: dict[str, Any],
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    """Run LLM-based flows & positioning analysis using ONLY raw engine inputs.

    The model receives raw positioning data (put/call, VIX, futures proxies,
    sentiment, flow signals) and pillar scores. It does NOT receive the
    composite label, summary, or trader takeaway to prevent anchoring.

    Returns a dict matching the Flows & Positioning model schema.
    """
    import logging

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    _log = logging.getLogger("bentrade.model_analysis")

    raw_evidence = _extract_flows_positioning_raw_evidence(engine_result)

    pillar_scores = raw_evidence.get("pillar_scores", {})
    _log.info(
        "[MODEL_FLOWS_POS] input_mode=raw_only "
        "pillar_scores=%s excluded_derived=%s warnings=%d missing=%d",
        pillar_scores,
        _FLOWS_POSITIONING_EXCLUDED_FIELDS,
        len(raw_evidence.get("warnings", [])),
        len(raw_evidence.get("missing_inputs", [])),
    )

    prompt = (
        "You are the BenTrade Flows & Positioning Analyst. Analyze the supplied "
        "positioning and flow data and return ONLY valid JSON matching the required schema.\n\n"
        "Your job: Determine whether current positioning and flows support continuation, "
        "indicate crowding, create squeeze risk, or signal reversal potential.\n\n"
        "REQUIRED JSON SCHEMA:\n"
        "{\n"
        '  "label": "STRONGLY SUPPORTIVE|SUPPORTIVE|MIXED|FRAGILE|REVERSAL RISK|UNSTABLE",\n'
        '  "score": <number 0-100>,\n'
        '  "confidence": <number 0.0-1.0>,\n'
        '  "summary": "<2-3 sentence flows & positioning assessment>",\n'
        '  "pillar_analysis": {\n'
        '    "positioning_pressure": "<interpretation>",\n'
        '    "crowding_stretch": "<interpretation>",\n'
        '    "squeeze_unwind_risk": "<interpretation>",\n'
        '    "flow_direction_persistence": "<interpretation>",\n'
        '    "positioning_stability": "<interpretation>"\n'
        "  },\n"
        '  "flow_drivers": {\n'
        '    "supportive_factors": ["<factor1>", ...],\n'
        '    "risk_factors": ["<factor1>", ...],\n'
        '    "ambiguous_factors": ["<factor1>", ...]\n'
        "  },\n"
        '  "trading_implications": {\n'
        '    "continuation_support": "<strong|moderate|weak|none>",\n'
        '    "reversal_risk": "<low|moderate|elevated|high>",\n'
        '    "position_sizing": "<full|reduced|minimal>",\n'
        '    "strategy_recommendation": "<specific guidance>",\n'
        '    "squeeze_guidance": "<specific guidance>"\n'
        "  },\n"
        '  "uncertainty_flags": ["<flag1>", ...],\n'
        '  "trader_takeaway": "<one actionable paragraph>"\n'
        "}\n\n"
        "SCORING GUIDE:\n"
        "  85-100 = Strongly Supportive Flows — positioning/flows support continuation\n"
        "  70-84  = Supportive Positioning — healthy positioning with room to run\n"
        "  55-69  = Mixed but Tradable — some concerns but tradable\n"
        "  45-54  = Fragile / Crowded — elevated fragility, reduce exposure\n"
        "  30-44  = Reversal Risk Elevated — significant risk of positioning unwind\n"
        "  0-29   = Unstable / Unwind Risk — extreme positioning risk, defensive posture\n\n"
        "IMPORTANT: Base your analysis on the RAW DATA provided. Do not invent data points.\n\n"
        "DATA SOURCE AWARENESS (proxy honesty):\n"
        "  - Phase 1 uses PROXY data derived from VIX and market context, NOT direct\n"
        "    institutional flow feeds, CFTC COT data, or true dealer gamma reports.\n"
        "  - Put/call ratio is a VIX-derived PROXY, not exchange-reported.\n"
        "  - Futures positioning, short interest, systematic allocation, and retail\n"
        "    sentiment are ALL PROXY ESTIMATES from VIX regime heuristics.\n"
        "  - Flow direction, persistence, and follow-through are derived proxies.\n"
        "  - This significantly limits the precision of any positioning assessment.\n"
        "  - Reflect proxy limitations as LOWER confidence, not as confident assessments.\n"
        "  - If data is missing, reflect that as lower confidence and note it explicitly.\n"
        "  - NEVER claim precision that the proxy data cannot support."
    )

    user_data_str = json.dumps(raw_evidence, ensure_ascii=False, indent=None)

    for forbidden in _FLOWS_POSITIONING_EXCLUDED_FIELDS:
        if f'"{forbidden}"' in user_data_str:
            _log.error(
                "[MODEL_FLOWS_POS] LEAK DETECTED: derived field '%s' in user_data",
                forbidden,
            )

    _log.debug("[MODEL_FLOWS_POS] user_data_snapshot=%s", user_data_str[:2000])

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_data_str},
        ],
        "max_tokens": 2500,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 5. Call the model (via shared transport layer) ─────────────
    _transport_result = _model_transport(
        task_type="flows_positioning",
        payload=payload,
        log_prefix="MODEL_FLOWS_POS",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    # ── 6. Parse + coerce ───────────────────────────────────────
    from common.json_repair import extract_and_repair_json
    parsed, method = extract_and_repair_json(assistant_text)

    if method:
        _log.info("[MODEL_FLOWS_POS] JSON extracted via method=%s", method)

    normalized = _coerce_flows_positioning_model_output(parsed)
    if normalized is None:
        normalized = _build_plaintext_fallback(assistant_text, "flows_positioning")
        method = "plaintext_fallback"
        if normalized is None:
            raise ValueError("Model returned invalid flows & positioning analysis payload")

    # ── 7. Attach trace metadata ────────────────────────────────
    normalized["_trace"] = {
        "input_mode": "raw_only",
        "pillar_scores_provided": pillar_scores,
        "excluded_derived_fields": _FLOWS_POSITIONING_EXCLUDED_FIELDS,
        "json_parse_method": method,
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }

    return normalized


# ═══════════════════════════════════════════════════════════════════════
# LIQUIDITY & FINANCIAL CONDITIONS
# ═══════════════════════════════════════════════════════════════════════

_LIQUIDITY_CONDITIONS_EXCLUDED_FIELDS = [
    "score",
    "label",
    "short_label",
    "summary",
    "trader_takeaway",
    "positive_contributors",
    "negative_contributors",
    "conflicting_signals",
    "confidence_score",
    "signal_quality",
    "support_vs_stress",
]


def _extract_liquidity_conditions_raw_evidence(engine_result: dict[str, Any]) -> dict[str, Any]:
    """Build raw evidence for the liquidity conditions model — NO derived scores/labels.

    Includes only:
      - raw_inputs: rates, conditions, credit, dollar, stability sub-dicts
      - pillar_scores: the 5 numeric scores
      - pillar_weights: how pillars are weighted
      - warnings: data quality warnings
      - missing_inputs: what data was unavailable
    """
    raw_inputs = engine_result.get("raw_inputs", {})
    return {
        "raw_inputs": {
            "rates": raw_inputs.get("rates", {}),
            "conditions": raw_inputs.get("conditions", {}),
            "credit": raw_inputs.get("credit", {}),
            "dollar": raw_inputs.get("dollar", {}),
            "stability": raw_inputs.get("stability", {}),
        },
        "pillar_scores": engine_result.get("pillar_scores", {}),
        "pillar_weights": engine_result.get("pillar_weights", {}),
        "warnings": engine_result.get("warnings", []),
        "missing_inputs": engine_result.get("missing_inputs", []),
    }


def _coerce_liquidity_conditions_model_output(candidate: Any) -> dict[str, Any] | None:
    """Normalize LLM liquidity & conditions analysis output into a consistent schema."""
    if not isinstance(candidate, dict):
        return None

    label = candidate.get("label") or "ANALYSIS"
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    summary = _safe_summary_text(
        candidate.get("summary"), fallback="Model did not provide a summary."
    )

    try:
        score = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        score = None

    try:
        confidence = max(0.0, min(1.0, float(confidence) if confidence is not None else 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    result: dict[str, Any] = {
        "label": str(label).strip().upper(),
        "score": round(score, 1) if score is not None else None,
        "confidence": round(confidence, 2),
        "summary": str(summary).strip(),
    }

    # Tone
    tone = candidate.get("tone")
    result["tone"] = str(tone).strip().lower() if tone else "neutral"

    # Pillar interpretation
    pi = candidate.get("pillar_interpretation")
    if isinstance(pi, dict):
        result["pillar_interpretation"] = {
            k: str(v).strip() if isinstance(v, str) else v
            for k, v in pi.items()
        }
    else:
        result["pillar_interpretation"] = {}

    # Liquidity drivers
    ld = candidate.get("liquidity_drivers")
    if isinstance(ld, dict):
        result["liquidity_drivers"] = {
            "supportive_factors": _coerce_string_list(ld.get("supportive_factors")) or [],
            "restrictive_factors": _coerce_string_list(ld.get("restrictive_factors")) or [],
            "latent_stress_signals": _coerce_string_list(ld.get("latent_stress_signals")) or [],
        }
    else:
        result["liquidity_drivers"] = {
            "supportive_factors": [],
            "restrictive_factors": [],
            "latent_stress_signals": [],
        }

    # Score drivers
    sd = candidate.get("score_drivers")
    if isinstance(sd, dict):
        result["score_drivers"] = {
            "primary_driver": str(sd.get("primary_driver", "")).strip(),
            "secondary_drivers": _coerce_string_list(sd.get("secondary_drivers")) or [],
        }
    else:
        result["score_drivers"] = {"primary_driver": "", "secondary_drivers": []}

    # Market implications
    mi = candidate.get("market_implications")
    if isinstance(mi, dict):
        result["market_implications"] = {
            "risk_asset_outlook": str(mi.get("risk_asset_outlook", "")).strip(),
            "credit_conditions": str(mi.get("credit_conditions", "")).strip(),
            "funding_assessment": str(mi.get("funding_assessment", "")).strip(),
            "position_sizing": str(mi.get("position_sizing", "")).strip(),
            "strategy_recommendation": str(mi.get("strategy_recommendation", "")).strip(),
        }
    else:
        result["market_implications"] = {}

    result["uncertainty_flags"] = _coerce_string_list(candidate.get("uncertainty_flags")) or []

    ta = candidate.get("trader_takeaway")
    result["trader_takeaway"] = str(ta).strip() if ta else ""

    return result


def analyze_liquidity_conditions(
    *,
    engine_result: dict[str, Any],
    model_url: str | None = None,
    retries: int = 0,
    timeout: int = 180,
) -> dict[str, Any]:
    """Run LLM-based liquidity & conditions analysis using ONLY raw engine inputs.

    The model receives raw rates/credit/dollar/conditions data and pillar scores.
    It does NOT receive the composite label, summary, or trader takeaway
    to prevent anchoring.

    Returns a dict matching the Liquidity Conditions model schema.
    """
    import logging

    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()

    _log = logging.getLogger("bentrade.model_analysis")

    raw_evidence = _extract_liquidity_conditions_raw_evidence(engine_result)

    pillar_scores = raw_evidence.get("pillar_scores", {})
    _log.info(
        "[MODEL_LIQ_COND] input_mode=raw_only "
        "pillar_scores=%s excluded_derived=%s warnings=%d missing=%d",
        pillar_scores,
        _LIQUIDITY_CONDITIONS_EXCLUDED_FIELDS,
        len(raw_evidence.get("warnings", [])),
        len(raw_evidence.get("missing_inputs", [])),
    )

    prompt = (
        "You are the BenTrade Liquidity & Financial Conditions Analyst. Analyze the supplied "
        "rates, credit, dollar, and conditions data and return ONLY valid JSON matching the "
        "required schema.\n\n"
        "Your job: Determine whether current liquidity conditions and financial market "
        "plumbing are supportive, neutral, or restrictive for risk assets.\n\n"
        "REQUIRED JSON SCHEMA:\n"
        "{\n"
        '  "label": "STRONGLY SUPPORTIVE|SUPPORTIVE|MIXED|TIGHTENING|RESTRICTIVE|STRESS",\n'
        '  "score": <number 0-100>,\n'
        '  "confidence": <number 0.0-1.0>,\n'
        '  "tone": "<supportive|cautious|neutral|concerned|alarmed>",\n'
        '  "summary": "<2-3 sentence liquidity conditions assessment>",\n'
        '  "pillar_interpretation": {\n'
        '    "rates_policy_pressure": "<interpretation>",\n'
        '    "financial_conditions_tightness": "<interpretation>",\n'
        '    "credit_funding_stress": "<interpretation>",\n'
        '    "dollar_global_liquidity": "<interpretation>",\n'
        '    "liquidity_stability_fragility": "<interpretation>"\n'
        "  },\n"
        '  "liquidity_drivers": {\n'
        '    "supportive_factors": ["<factor1>", ...],\n'
        '    "restrictive_factors": ["<factor1>", ...],\n'
        '    "latent_stress_signals": ["<factor1>", ...]\n'
        "  },\n"
        '  "score_drivers": {\n'
        '    "primary_driver": "<what is driving the score most>",\n'
        '    "secondary_drivers": ["<driver1>", ...]\n'
        "  },\n"
        '  "market_implications": {\n'
        '    "risk_asset_outlook": "<supportive|neutral|headwind|hostile>",\n'
        '    "credit_conditions": "<easy|neutral|tightening|stressed>",\n'
        '    "funding_assessment": "<stable|manageable|strained|stressed>",\n'
        '    "position_sizing": "<full|reduced|minimal|defensive>",\n'
        '    "strategy_recommendation": "<specific guidance>"\n'
        "  },\n"
        '  "uncertainty_flags": ["<flag1>", ...],\n'
        '  "trader_takeaway": "<one actionable paragraph>"\n'
        "}\n\n"
        "SCORING GUIDE:\n"
        "  85-100 = Liquidity Strongly Supportive — conditions ideal for risk\n"
        "  70-84  = Supportive Conditions — favorable rates/credit/funding\n"
        "  55-69  = Mixed but Manageable — some tightening but tradable\n"
        "  45-54  = Neutral / Tightening — headwinds emerging, caution warranted\n"
        "  30-44  = Restrictive Conditions — active tightening, reduce exposure\n"
        "  0-29   = Liquidity Stress — hostile conditions, defensive posture\n\n"
        "IMPORTANT: Base your analysis on the RAW DATA provided. Do not invent data points.\n\n"
        "DATA SOURCE AWARENESS:\n"
        "  - Rate data (2Y, 10Y, Fed Funds, yield curve) is DIRECT from FRED.\n"
        "  - Credit spreads (IG, HY OAS) are DIRECT from FRED when available.\n"
        "  - USD index is DIRECT from FRED.\n"
        "  - VIX is from Tradier/Finnhub/FRED waterfall.\n"
        "  - Financial conditions index is a PROXY composite, NOT a true FCI.\n"
        "  - Funding stress is a PROXY from VIX + fed funds heuristic.\n"
        "  - If data is missing, reflect that as lower confidence.\n"
        "  - NEVER claim precision that the data cannot support."
    )

    user_data_str = json.dumps(raw_evidence, ensure_ascii=False, indent=None)

    for forbidden in _LIQUIDITY_CONDITIONS_EXCLUDED_FIELDS:
        if f'"{forbidden}"' in user_data_str:
            _log.error(
                "[MODEL_LIQ_COND] LEAK DETECTED: derived field '%s' in user_data",
                forbidden,
            )

    _log.debug("[MODEL_LIQ_COND] user_data_snapshot=%s", user_data_str[:2000])

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_data_str},
        ],
        "max_tokens": 2500,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 5. Call the model (via shared transport layer) ─────────────
    _transport_result = _model_transport(
        task_type="liquidity_conditions",
        payload=payload,
        log_prefix="MODEL_LIQ_COND",
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
    assistant_text = _transport_result.content

    # ── 6. Parse + coerce ───────────────────────────────────────
    from common.json_repair import extract_and_repair_json
    parsed, method = extract_and_repair_json(assistant_text)

    if method:
        _log.info("[MODEL_LIQ_COND] JSON extracted via method=%s", method)

    normalized = _coerce_liquidity_conditions_model_output(parsed)
    if normalized is None:
        normalized = _build_plaintext_fallback(assistant_text, "liquidity_conditions")
        method = "plaintext_fallback"
        if normalized is None:
            raise ValueError("Model returned invalid liquidity conditions analysis payload")

    # ── 7. Attach trace metadata ────────────────────────────────
    normalized["_trace"] = {
        "input_mode": "raw_only",
        "pillar_scores_provided": pillar_scores,
        "excluded_derived_fields": _LIQUIDITY_CONDITIONS_EXCLUDED_FIELDS,
        "json_parse_method": method,
        "transport_path": _transport_result.transport_path,
        "finish_reason": _transport_result.finish_reason,
    }

    return normalized
