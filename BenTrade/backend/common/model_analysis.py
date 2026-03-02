from __future__ import annotations

import json
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
    model_url: str = "http://localhost:1234/v1/chat/completions",
    retries: int = 1,
    timeout: int = 60,
) -> dict[str, Any]:
    """Call the local LLM with raw regime inputs only (no derived scores/labels).

    The model independently infers risk-on/off, trend, and volatility assessments
    from the raw market data.  BenTrade's computed regime labels, scores, and
    playbook recommendations are deliberately excluded to prevent anchoring.
    """
    import logging
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
    }

    # ── 5. Call the model ───────────────────────────────────────────
    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            response = _requests.post(model_url, json=payload, timeout=timeout)
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

            parsed = _extract_json_payload(assistant_text)
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
    model_url: str = "http://localhost:1234/v1/chat/completions",
    retries: int = 2,
    timeout: int = 120,
) -> dict[str, Any] | None:
    # Keep the legacy JSON contract exactly the same by delegating to the legacy implementation.
    # TODO(architecture): migrate implementation from common.utils into this module and delete legacy shim.
    from common import utils as legacy_utils

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
    model_url: str = "http://localhost:1234/v1/chat/completions",
    retries: int = 1,
    timeout: int = 30,
) -> dict[str, Any]:
    from common import utils as legacy_utils

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
    }

    last_error: Exception | None = None
    attempt = 0
    while attempt <= int(max(retries, 0)):
        attempt += 1
        try:
            response = legacy_utils.requests.post(model_url, json=payload, timeout=timeout)
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

            parsed = _extract_json_payload(assistant_text)
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
