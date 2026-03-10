from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from requests.exceptions import RequestException

from app.models.trade_contract import TradeContract
from app.utils.validation import parse_expiration


class LocalModelUnavailableError(RuntimeError):
    pass


def _extract_json_payload(raw_text: str) -> Any:
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
    """Normalise the LLM response for regime analysis into a consistent dict."""
    if isinstance(candidate, list) and candidate:
        first = candidate[0]
        if isinstance(first, dict):
            candidate = first
    if not isinstance(candidate, dict):
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

    confidence_raw = candidate.get("confidence")
    try:
        out["confidence"] = max(0.0, min(float(confidence_raw), 1.0))
    except (TypeError, ValueError):
        out["confidence"] = None

    # ── Model-inferred regime summary labels ────────────────────────
    # These are the structured labels the model infers independently from raw
    # inputs — used for the Engine-vs-Model comparison table.
    for label_key in ("risk_regime_label", "trend_label", "vol_regime_label"):
        val = candidate.get(label_key)
        out[label_key] = str(val).strip() if isinstance(val, str) and val.strip() else None

    key_drivers = candidate.get("key_drivers")
    if isinstance(key_drivers, list):
        out["key_drivers"] = [str(item).strip() for item in key_drivers if str(item or "").strip()][:5]
    elif isinstance(key_drivers, str) and key_drivers.strip():
        out["key_drivers"] = [key_drivers.strip()]
    else:
        out["key_drivers"] = None

    return out


def _extract_regime_raw_inputs(regime_data: dict[str, Any]) -> dict[str, Any]:
    """Extract ONLY raw market inputs from regime data, excluding all derived scores/labels.

    Raw inputs = values directly from providers or computed from raw price series
    (e.g., moving averages, RSI from closes).

    Explicitly excluded:
    - regime_label (RISK_ON/NEUTRAL/RISK_OFF)
    - regime_score (0-100 composite)
    - component score values (normalized 0-100)
    - component raw_points
    - component signals (human-readable scoring descriptions)
    - suggested_playbook (primary/avoid/notes)
    - boolean comparisons (close_gt_ema20, close_gt_ema50, sma50_gt_sma200)
    """
    components = regime_data.get("components") or {}

    trend_inputs = (components.get("trend") or {}).get("inputs") or {}
    vol_inputs = (components.get("volatility") or {}).get("inputs") or {}
    breadth_inputs = (components.get("breadth") or {}).get("inputs") or {}
    rates_inputs = (components.get("rates") or {}).get("inputs") or {}
    momentum_inputs = (components.get("momentum") or {}).get("inputs") or {}

    # Derived boolean fields to exclude from trend inputs
    _TREND_EXCLUDED = {"close_gt_ema20", "close_gt_ema50", "sma50_gt_sma200"}

    raw: dict[str, Any] = {
        # Trend: SPY price and moving averages (computed from raw price series)
        "spy_price": trend_inputs.get("close"),
        "spy_ema20": trend_inputs.get("ema20"),
        "spy_ema50": trend_inputs.get("ema50"),
        "spy_sma50": trend_inputs.get("sma50"),
        "spy_sma200": trend_inputs.get("sma200"),
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
        # Momentum: RSI
        "rsi14": momentum_inputs.get("rsi14"),
    }

    return raw


# Fields that are explicitly derived by BenTrade's regime engine and must NOT
# be sent to the model.  Used for trace logging verification.
_REGIME_DERIVED_FIELDS = [
    "regime_label",
    "regime_score",
    "suggested_playbook",
    "components.*.score",
    "components.*.raw_points",
    "components.*.signals",
    "components.trend.inputs.close_gt_ema20",
    "components.trend.inputs.close_gt_ema50",
    "components.trend.inputs.sma50_gt_sma200",
]


def extract_engine_regime_summary(regime_data: dict[str, Any]) -> dict[str, Any]:
    """Extract a structured summary of the ENGINE-derived regime outputs.

    This captures BenTrade's computed labels and scores so they can be shown
    in the Engine column of the comparison table.

    Input fields and derivation:
      - risk_regime_label: from regime_data["regime_label"] (RISK_ON → Risk-On, etc.)
      - trend_label: inferred from trend component inputs (close vs EMA20/EMA50/SMA200)
      - vol_regime_label: inferred from VIX level buckets
      - confidence: regime_score / 100 (normalized to 0-1)
      - key_drivers: top signals from components with highest scores
    """
    # ── Risk regime label ───────────────────────────────────────────
    raw_label = str(regime_data.get("regime_label") or "NEUTRAL").upper()
    _LABEL_MAP = {"RISK_ON": "Risk-On", "RISK_OFF": "Risk-Off", "NEUTRAL": "Neutral"}
    risk_regime_label = _LABEL_MAP.get(raw_label, "Neutral")

    # ── Trend label ─────────────────────────────────────────────────
    # Derived from: close vs EMA20, EMA50, SMA50 vs SMA200
    components = regime_data.get("components") or {}
    trend_inputs = (components.get("trend") or {}).get("inputs") or {}
    close = trend_inputs.get("close")
    ema20 = trend_inputs.get("ema20")
    sma50 = trend_inputs.get("sma50")
    sma200 = trend_inputs.get("sma200")

    if close is not None and ema20 is not None and sma200 is not None:
        if close > ema20 and (sma50 is None or sma50 > sma200):
            trend_label = "Uptrend"
        elif close < sma200:
            trend_label = "Downtrend"
        else:
            trend_label = "Sideways"
    elif close is not None and ema20 is not None:
        trend_label = "Uptrend" if close > ema20 else "Downtrend"
    else:
        trend_label = "Unknown"

    # ── Volatility regime label ─────────────────────────────────────
    # Derived from: VIX level buckets
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
    # regime_score is 0-100; normalize to 0-1 for comparison
    regime_score = regime_data.get("regime_score")
    try:
        confidence = round(max(0.0, min(float(regime_score) / 100.0, 1.0)), 2)
    except (TypeError, ValueError):
        confidence = None

    # ── Key drivers ─────────────────────────────────────────────────
    # Pick top signals from the components with highest scores
    key_drivers: list[str] = []
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

    return {
        "risk_regime_label": risk_regime_label,
        "trend_label": trend_label,
        "vol_regime_label": vol_regime_label,
        "confidence": confidence,
        "key_drivers": key_drivers,
    }


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
    import requests as _requests

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
        "You are an independent market analyst producing an options-trading regime assessment.\n"
        "You will receive a JSON object with:\n"
        "  - regime_raw_inputs: raw market data values (prices, moving averages, VIX, yields, breadth, RSI)\n"
        "  - metadata: timestamp and data-source health information\n\n"
        "IMPORTANT RULES:\n"
        "  1. Do NOT use any precomputed regime labels, scores, or playbook recommendations.\n"
        "     If a label is needed, infer it yourself from the raw inputs.\n"
        "  2. All assessments must be derived solely from the raw inputs provided.\n"
        "  3. If a raw input is null/missing, note it explicitly and reduce confidence accordingly.\n\n"
        "Return valid JSON only (no markdown, no code fences) with exactly these keys:\n"
        "  risk_regime_label   – string, one of: 'Risk-On', 'Neutral', 'Risk-Off'\n"
        "     (your independent assessment of the overall risk regime)\n"
        "  trend_label         – string, one of: 'Uptrend', 'Sideways', 'Downtrend'\n"
        "     (your independent assessment of the market trend)\n"
        "  vol_regime_label    – string, one of: 'Low', 'Moderate', 'High'\n"
        "     (your independent assessment of the volatility regime)\n"
        "  key_drivers         – string array of 3 short bullet points describing the top\n"
        "     factors driving your regime assessment, derived from the raw inputs\n"
        "  executive_summary   – string, 2-4 sentence overview of the current market regime\n"
        "     as you infer it from the raw data. Include your inferred regime stance\n"
        "     (risk-on / neutral / risk-off) and overall trend direction.\n"
        "  regime_breakdown    – object with keys trend, volatility, breadth, rates, momentum;\n"
        "     each value is a 2-3 sentence analysis of that component based on raw inputs.\n"
        "     Explicitly state which raw values you are interpreting.\n"
        "  primary_fit         – string explaining which options strategies fit the regime\n"
        "     you inferred, and why, based on the raw data\n"
        "  avoid_rationale     – string explaining which strategies are riskier given\n"
        "     the raw data, and why\n"
        "  change_triggers     – string array of 3-5 specific conditions that would shift\n"
        "     the regime (e.g., 'VIX rises above 25', 'SPY breaks below SMA200')\n"
        "  confidence_caveats  – string with confidence level and any data-quality caveats;\n"
        "     call out any missing inputs that reduce confidence\n"
        "  confidence          – float 0-1 representing your overall confidence\n"
        "  raw_inputs_used     – object listing each raw input name and the value you received,\n"
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
    for forbidden in ("regime_label", "regime_score", "suggested_playbook"):
        if f'"{forbidden}"' in _user_data_str:
            _log.error(
                "[MODEL_REGIME_TRACE] LEAK DETECTED: derived field '%s' found in user_data", forbidden
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
        "max_tokens": 2400,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 5. Call the model ───────────────────────────────────────────
    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            _log.info("[MODEL_REGIME] POST %s (attempt %d, timeout=%ds)", model_url, attempt, timeout)
            response = _requests.post(model_url, json=payload, timeout=timeout)
            _log.info(
                "[MODEL_REGIME] response HTTP %d (%d bytes, %.1fs)",
                response.status_code, len(response.content), response.elapsed.total_seconds(),
            )
            response.raise_for_status()

            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            assistant_text = None
            if isinstance(response_json, dict):
                choices = response_json.get("choices") or []
                if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                    first = choices[0]
                    message = first.get("message")
                    if isinstance(message, dict) and "content" in message:
                        assistant_text = message.get("content")
                    elif "text" in first:
                        assistant_text = first.get("text")
            if assistant_text is None:
                assistant_text = getattr(response, "text", "")

            # ── Sanitize: strip <think> tags and any hidden reasoning ──
            from common.model_sanitize import had_think_tags
            if had_think_tags(assistant_text):
                _log.info("[MODEL_REGIME] <think> content detected and stripped (attempt %d)", attempt)
            assistant_text = _strip_think_tags(assistant_text)

            # Try robust JSON extraction via repair pipeline first
            from common.json_repair import extract_and_repair_json
            parsed, method = extract_and_repair_json(assistant_text)
            if parsed is None:
                parsed = _extract_json_payload(assistant_text)
                method = "legacy_fallback"

            if method:
                _log.info("[MODEL_REGIME] JSON extracted via method=%s", method)

            normalized = _coerce_regime_model_output(parsed)
            if normalized is None:
                raise ValueError("Model returned invalid regime analysis payload")

            # ── 6. Attach trace metadata to response ────────────────
            normalized["_trace"] = {
                "model_regime_input_mode": "raw_only",
                "included_fields_count": included_count,
                "excluded_fields_count": len(_REGIME_DERIVED_FIELDS),
                "excluded_derived_field_names": _REGIME_DERIVED_FIELDS,
                "missing_raw_fields": missing_fields,
                "regime_raw_inputs_snapshot": {
                    k: v for k, v in regime_raw_inputs.items() if v is not None
                },
            }

            return normalized
        except RequestException as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break

    if isinstance(last_error, RequestException):
        raise LocalModelUnavailableError(
            f"Local model endpoint unavailable at {model_url}: {last_error}"
        ) from last_error
    raise RuntimeError(f"Regime model analysis failed: {last_error}")


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
    from common import utils as legacy_utils

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

    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            response = legacy_utils.requests.post(model_url, json=payload, timeout=timeout)
            _log.info(
                "[MODEL_STOCK_IDEA] response HTTP %d (%d bytes, %.1fs)",
                response.status_code, len(response.content), response.elapsed.total_seconds(),
            )
            response.raise_for_status()

            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            assistant_text = None
            if isinstance(response_json, dict):
                choices = response_json.get("choices") or []
                if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                    first = choices[0]
                    message = first.get("message")
                    if isinstance(message, dict) and "content" in message:
                        assistant_text = message.get("content")
                    elif "text" in first:
                        assistant_text = first.get("text")
            if assistant_text is None:
                assistant_text = getattr(response, "text", "")

            # ── Sanitize: strip <think> tags and any hidden reasoning ──
            from common.model_sanitize import had_think_tags
            if had_think_tags(assistant_text):
                _log.info("[MODEL_STOCK_IDEA] <think> content detected and stripped (attempt %d)", attempt)
            assistant_text = _strip_think_tags(assistant_text)

            # Try robust JSON extraction via repair pipeline first
            from common.json_repair import extract_and_repair_json
            parsed, method = extract_and_repair_json(assistant_text)
            if parsed is None:
                parsed = _extract_json_payload(assistant_text)
                method = "legacy_fallback"

            if method:
                _log.info("[MODEL_STOCK_IDEA] JSON extracted via method=%s", method)

            normalized = _coerce_stock_model_output(parsed)
            if normalized is None:
                raise ValueError("Model returned invalid stock analysis payload")
            return normalized
        except RequestException as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break

    if isinstance(last_error, RequestException):
        raise LocalModelUnavailableError(f"Local model endpoint unavailable at {model_url}: {last_error}") from last_error
    raise RuntimeError(f"Stock model analysis failed: {last_error}")


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
    import requests as _requests

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

    def _call_llm(messages: list[dict], label: str) -> str | None:
        """POST to LLM and extract assistant text. Returns None on network error."""
        try:
            _log.info("[MODEL_STOCK_STRATEGY] POST %s (%s, timeout=%ds)", model_url, label, timeout)
            resp = _requests.post(
                model_url,
                json={"messages": messages, "max_tokens": 2048, "temperature": 0.0, "stream": False},
                timeout=timeout,
            )
            _log.info(
                "[MODEL_STOCK_STRATEGY] response HTTP %d (%d bytes, %.1fs) [%s]",
                resp.status_code, len(resp.content), resp.elapsed.total_seconds(), label,
            )
            resp.raise_for_status()
        except RequestException as exc:
            _log.warning("[MODEL_STOCK_STRATEGY_TRACE] %s network error: %s", label, exc)
            raise

        response_json = None
        try:
            response_json = resp.json()
        except Exception:
            pass

        assistant_text = None
        if isinstance(response_json, dict):
            choices = response_json.get("choices") or []
            if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                first = choices[0]
                message = first.get("message")
                if isinstance(message, dict) and "content" in message:
                    assistant_text = message.get("content")
                elif "text" in first:
                    assistant_text = first.get("text")
        if assistant_text is None:
            assistant_text = getattr(resp, "text", "")

        return assistant_text

    # ── Main attempt loop (network retries) ──────────────────────
    last_error: Exception | None = None
    assistant_text: str | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            assistant_text = _call_llm(payload["messages"], f"attempt-{attempt}")
            break  # network OK — proceed to parse
        except RequestException as exc:
            last_error = exc
            _log.warning(
                "[MODEL_STOCK_STRATEGY_TRACE] attempt %d/%d network fail: %s",
                attempt, retries + 1, exc,
            )

    # All network retries exhausted
    if assistant_text is None:
        if isinstance(last_error, RequestException):
            raise LocalModelUnavailableError(
                f"Local model endpoint unavailable at {model_url}: {last_error}"
            ) from last_error
        raise RuntimeError(f"Stock strategy model analysis failed: {last_error}")

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

        fix_messages = payload["messages"] + [
            {"role": "assistant", "content": assistant_text},
            {
                "role": "user",
                "content": (
                    "Your previous response was not valid JSON. "
                    "Return ONLY the corrected JSON object — no markdown fences, "
                    "no explanation, no trailing commas. Start with { and end with }."
                ),
            },
        ]

        try:
            fix_text = _call_llm(fix_messages, "retry-fix")
            if fix_text:
                parsed2, parse_method2 = extract_and_repair_json(fix_text)
                normalized = _coerce_stock_strategy_output(parsed2) if parsed2 is not None else None
                if normalized is not None:
                    parse_method = f"retry_fix+{parse_method2 or 'unknown'}"
                    _log.info(
                        "[MODEL_STOCK_STRATEGY_TRACE] retry-with-fix SUCCEEDED strategy=%s symbol=%s method=%s",
                        strategy_id, symbol, parse_method,
                    )
        except RequestException:
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
    out["summary"] = str(summary).strip() if isinstance(summary, str) and summary.strip() else None

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


def _coerce_string_list(val: Any, *, max_items: int = 8) -> list[str] | None:
    """Coerce a value to a list of non-empty strings, or None."""
    if isinstance(val, list):
        items = [str(item).strip() for item in val if str(item or "").strip()][:max_items]
        return items if items else None
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return None


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
    import requests as _requests

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
        "max_tokens": 4000,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 5. Call the model ───────────────────────────────────────
    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            _log.info("[MODEL_NEWS] POST %s (attempt %d, timeout=%ds)", model_url, attempt, timeout)
            response = _requests.post(model_url, json=payload, timeout=timeout)
            _log.info(
                "[MODEL_NEWS] response HTTP %d (%d bytes, %.1fs)",
                response.status_code, len(response.content), response.elapsed.total_seconds(),
            )
            response.raise_for_status()

            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            assistant_text = None
            if isinstance(response_json, dict):
                choices = response_json.get("choices") or []
                if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                    first = choices[0]
                    message = first.get("message")
                    if isinstance(message, dict) and "content" in message:
                        assistant_text = message.get("content")
                    elif "text" in first:
                        assistant_text = first.get("text")
            if assistant_text is None:
                assistant_text = getattr(response, "text", "")

            # ── Sanitize: strip <think> tags and any hidden reasoning ──
            from common.model_sanitize import had_think_tags
            if had_think_tags(assistant_text):
                _log.info("[MODEL_NEWS] <think> content detected and stripped (attempt %d)", attempt)
            assistant_text = _strip_think_tags(assistant_text)

            # Try robust JSON extraction via repair pipeline first
            from common.json_repair import extract_and_repair_json
            parsed, method = extract_and_repair_json(assistant_text)
            if parsed is None:
                # Fallback to legacy extractor
                parsed = _extract_json_payload(assistant_text)
                method = "legacy_fallback"

            if method:
                _log.info("[MODEL_NEWS] JSON extracted via method=%s", method)

            normalized = _coerce_news_sentiment_model_output(parsed)
            if normalized is None:
                raise ValueError("Model returned invalid news sentiment payload")

            # ── 6. Attach trace metadata ────────────────────────
            normalized["_trace"] = {
                "input_mode": "raw_only",
                "headlines_provided": included_headlines,
                "excluded_derived_fields": _NEWS_SENTIMENT_EXCLUDED_FIELDS,
                "missing_macro_fields": missing_macro,
                "json_parse_method": method,
            }

            return normalized
        except RequestException as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break

    if isinstance(last_error, RequestException):
        raise LocalModelUnavailableError(
            f"Local model endpoint unavailable at {model_url}: {last_error}"
        ) from last_error
    raise RuntimeError(f"News sentiment model analysis failed: {last_error}")


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

    Returns None if candidate is not a valid dict with required fields.
    """
    if not isinstance(candidate, dict):
        return None

    label = candidate.get("label")
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    summary = candidate.get("summary")

    if label is None or score is None or summary is None:
        return None

    # Clamp score 0-100
    try:
        score = float(score)
        score = max(0.0, min(100.0, score))
    except (TypeError, ValueError):
        return None

    # Clamp confidence 0-1
    try:
        confidence = float(confidence) if confidence is not None else 0.5
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    result: dict[str, Any] = {
        "label": str(label).strip().upper(),
        "score": round(score, 1),
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
    import requests as _requests

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
        "max_tokens": 4000,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 5. Call the model ───────────────────────────────────────
    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            _log.info("[MODEL_BREADTH] POST %s (attempt %d, timeout=%ds)", model_url, attempt, timeout)
            response = _requests.post(model_url, json=payload, timeout=timeout)
            _log.info(
                "[MODEL_BREADTH] response HTTP %d (%d bytes, %.1fs)",
                response.status_code, len(response.content), response.elapsed.total_seconds(),
            )
            response.raise_for_status()

            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            assistant_text = None
            if isinstance(response_json, dict):
                choices = response_json.get("choices") or []
                if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                    first = choices[0]
                    message = first.get("message")
                    if isinstance(message, dict) and "content" in message:
                        assistant_text = message.get("content")
                    elif "text" in first:
                        assistant_text = first.get("text")
            if assistant_text is None:
                assistant_text = getattr(response, "text", "")

            # Sanitize: strip <think> tags
            from common.model_sanitize import had_think_tags
            if had_think_tags(assistant_text):
                _log.info("[MODEL_BREADTH] <think> content detected and stripped (attempt %d)", attempt)
            assistant_text = _strip_think_tags(assistant_text)

            # Try robust JSON extraction
            from common.json_repair import extract_and_repair_json
            parsed, method = extract_and_repair_json(assistant_text)
            if parsed is None:
                parsed = _extract_json_payload(assistant_text)
                method = "legacy_fallback"

            if method:
                _log.info("[MODEL_BREADTH] JSON extracted via method=%s", method)

            normalized = _coerce_breadth_model_output(parsed)
            if normalized is None:
                raise ValueError("Model returned invalid breadth analysis payload")

            # ── 6. Attach trace metadata ────────────────────────
            normalized["_trace"] = {
                "input_mode": "raw_only",
                "pillar_scores_provided": pillar_scores,
                "excluded_derived_fields": _BREADTH_EXCLUDED_FIELDS,
                "universe_coverage_pct": universe.get("coverage_pct", 0),
                "json_parse_method": method,
            }

            return normalized
        except RequestException as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break

    if isinstance(last_error, RequestException):
        raise LocalModelUnavailableError(
            f"Local model endpoint unavailable at {model_url}: {last_error}"
        ) from last_error
    raise RuntimeError(f"Breadth model analysis failed: {last_error}")


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

    label = candidate.get("label")
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    summary = candidate.get("summary")

    if label is None or score is None or summary is None:
        return None

    try:
        score = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        return None

    try:
        confidence = max(0.0, min(1.0, float(confidence) if confidence is not None else 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    result: dict[str, Any] = {
        "label": str(label).strip().upper(),
        "score": round(score, 1),
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
    import requests as _requests

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
        "max_tokens": 4000,
        "temperature": 0.0,
        "stream": False,
    }

    # ── 4. Call the model ───────────────────────────────────────
    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            _log.info("[MODEL_VOL] POST %s (attempt %d, timeout=%ds)", model_url, attempt, timeout)
            response = _requests.post(model_url, json=payload, timeout=timeout)
            _log.info(
                "[MODEL_VOL] response HTTP %d (%d bytes, %.1fs)",
                response.status_code, len(response.content), response.elapsed.total_seconds(),
            )
            response.raise_for_status()

            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            assistant_text = None
            if isinstance(response_json, dict):
                choices = response_json.get("choices") or []
                if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                    first = choices[0]
                    message = first.get("message")
                    if isinstance(message, dict) and "content" in message:
                        assistant_text = message.get("content")
                    elif "text" in first:
                        assistant_text = first.get("text")
            if assistant_text is None:
                assistant_text = getattr(response, "text", "")

            from common.model_sanitize import had_think_tags
            if had_think_tags(assistant_text):
                _log.info("[MODEL_VOL] <think> content detected and stripped (attempt %d)", attempt)
            assistant_text = _strip_think_tags(assistant_text)

            from common.json_repair import extract_and_repair_json
            parsed, method = extract_and_repair_json(assistant_text)
            if parsed is None:
                parsed = _extract_json_payload(assistant_text)
                method = "legacy_fallback"

            if method:
                _log.info("[MODEL_VOL] JSON extracted via method=%s", method)

            normalized = _coerce_vol_model_output(parsed)
            if normalized is None:
                raise ValueError("Model returned invalid volatility analysis payload")

            normalized["_trace"] = {
                "input_mode": "raw_only",
                "pillar_scores_provided": pillar_scores,
                "excluded_derived_fields": _VOL_EXCLUDED_FIELDS,
                "json_parse_method": method,
            }

            return normalized
        except RequestException as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break

    if isinstance(last_error, RequestException):
        raise LocalModelUnavailableError(
            f"Local model endpoint unavailable at {model_url}: {last_error}"
        ) from last_error
    raise RuntimeError(f"Volatility model analysis failed: {last_error}")


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

    label = candidate.get("label")
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    summary = candidate.get("summary")

    if label is None or score is None or summary is None:
        return None

    try:
        score = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        return None

    try:
        confidence = max(0.0, min(1.0, float(confidence) if confidence is not None else 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    result: dict[str, Any] = {
        "label": str(label).strip().upper(),
        "score": round(score, 1),
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
    import requests as _requests

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
        "max_tokens": 4000,
        "temperature": 0.0,
        "stream": False,
    }

    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            _log.info("[MODEL_CROSS_ASSET] POST %s (attempt %d, timeout=%ds)", model_url, attempt, timeout)
            response = _requests.post(model_url, json=payload, timeout=timeout)
            _log.info(
                "[MODEL_CROSS_ASSET] response HTTP %d (%d bytes, %.1fs)",
                response.status_code, len(response.content), response.elapsed.total_seconds(),
            )
            response.raise_for_status()

            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            assistant_text = None
            if isinstance(response_json, dict):
                choices = response_json.get("choices") or []
                if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                    first = choices[0]
                    message = first.get("message")
                    if isinstance(message, dict) and "content" in message:
                        assistant_text = message.get("content")
                    elif "text" in first:
                        assistant_text = first.get("text")
            if assistant_text is None:
                assistant_text = getattr(response, "text", "")

            from common.model_sanitize import had_think_tags
            if had_think_tags(assistant_text):
                _log.info("[MODEL_CROSS_ASSET] <think> content detected and stripped (attempt %d)", attempt)
            assistant_text = _strip_think_tags(assistant_text)

            from common.json_repair import extract_and_repair_json
            parsed, method = extract_and_repair_json(assistant_text)
            if parsed is None:
                parsed = _extract_json_payload(assistant_text)
                method = "legacy_fallback"

            if method:
                _log.info("[MODEL_CROSS_ASSET] JSON extracted via method=%s", method)

            normalized = _coerce_cross_asset_model_output(parsed)
            if normalized is None:
                raise ValueError("Model returned invalid cross-asset analysis payload")

            normalized["_trace"] = {
                "input_mode": "raw_only",
                "pillar_scores_provided": pillar_scores,
                "excluded_derived_fields": _CROSS_ASSET_EXCLUDED_FIELDS,
                "json_parse_method": method,
            }

            return normalized
        except RequestException as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break

    if isinstance(last_error, RequestException):
        raise LocalModelUnavailableError(
            f"Local model endpoint unavailable at {model_url}: {last_error}"
        ) from last_error
    raise RuntimeError(f"Cross-asset model analysis failed: {last_error}")


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

    label = candidate.get("label")
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    summary = candidate.get("summary")

    if label is None or score is None or summary is None:
        return None

    try:
        score = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        return None

    try:
        confidence = max(0.0, min(1.0, float(confidence) if confidence is not None else 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    result: dict[str, Any] = {
        "label": str(label).strip().upper(),
        "score": round(score, 1),
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
    if model_url is None:
        from app.services.model_router import get_model_endpoint
        model_url = get_model_endpoint()
    import requests as _requests

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
        "max_tokens": 4000,
        "temperature": 0.0,
        "stream": False,
    }

    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            _log.info("[MODEL_FLOWS_POS] POST %s (attempt %d, timeout=%ds)", model_url, attempt, timeout)
            response = _requests.post(model_url, json=payload, timeout=timeout)
            _log.info(
                "[MODEL_FLOWS_POS] response HTTP %d (%d bytes, %.1fs)",
                response.status_code, len(response.content), response.elapsed.total_seconds(),
            )
            response.raise_for_status()

            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            assistant_text = None
            if isinstance(response_json, dict):
                choices = response_json.get("choices") or []
                if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                    first = choices[0]
                    message = first.get("message")
                    if isinstance(message, dict) and "content" in message:
                        assistant_text = message.get("content")
                    elif "text" in first:
                        assistant_text = first.get("text")
            if assistant_text is None:
                assistant_text = getattr(response, "text", "")

            from common.model_sanitize import had_think_tags
            if had_think_tags(assistant_text):
                _log.info("[MODEL_FLOWS_POS] <think> content detected and stripped (attempt %d)", attempt)
            assistant_text = _strip_think_tags(assistant_text)

            from common.json_repair import extract_and_repair_json
            parsed, method = extract_and_repair_json(assistant_text)
            if parsed is None:
                parsed = _extract_json_payload(assistant_text)
                method = "legacy_fallback"

            if method:
                _log.info("[MODEL_FLOWS_POS] JSON extracted via method=%s", method)

            normalized = _coerce_flows_positioning_model_output(parsed)
            if normalized is None:
                raise ValueError("Model returned invalid flows & positioning analysis payload")

            normalized["_trace"] = {
                "input_mode": "raw_only",
                "pillar_scores_provided": pillar_scores,
                "excluded_derived_fields": _FLOWS_POSITIONING_EXCLUDED_FIELDS,
                "json_parse_method": method,
            }

            return normalized
        except RequestException as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break

    if isinstance(last_error, RequestException):
        raise LocalModelUnavailableError(
            f"Local model endpoint unavailable at {model_url}: {last_error}"
        ) from last_error
    raise RuntimeError(f"Flows positioning model analysis failed: {last_error}")
