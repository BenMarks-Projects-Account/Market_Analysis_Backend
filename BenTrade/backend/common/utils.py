import json
import re
import time
import uuid
from pathlib import Path
from datetime import datetime
import sys
import os
# Temporarily remove the repo `backend` directory from sys.path so a local
# `backend/requests` folder (leftover shim) does not shadow the real `requests`
# package installed in the venv. After importing, restore the original path.
# When utils lives in `common/`, the backend root is two levels up.
_repo_backend_dir = str(Path(__file__).parent.parent)
_removed_repo_backend = False
if _repo_backend_dir in sys.path:
    try:
        sys.path.remove(_repo_backend_dir)
        _removed_repo_backend = True
    except ValueError:
        _removed_repo_backend = False
try:
    import requests
    from requests.exceptions import RequestException
finally:
    if _removed_repo_backend and _repo_backend_dir not in sys.path:
        sys.path.insert(0, _repo_backend_dir)

RESULTS_DIR = Path(__file__).parent.parent / 'results'

# ---------------------------------------------------------------------------
# Robust LLM JSON extraction
# ---------------------------------------------------------------------------
_CODE_FENCE_RE = re.compile(r'```(?:json)?\s*\n?(.*?)\n?\s*```', re.DOTALL)

_MODEL_EVAL_KEYS = frozenset({'recommendation', 'confidence', 'risk_level', 'key_factors', 'summary',
                              'score_0_100', 'confidence_0_1', 'thesis', 'key_drivers',
                              'risk_review', 'execution_notes', 'execution_assessment',
                              'missing_data', 'model_calculations', 'edge_assessment',
                              'data_quality_flags', 'cross_check_deltas'})


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences, returning the inner content."""
    m = _CODE_FENCE_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def _find_json_block(text: str):
    """Attempt to parse the first balanced JSON object or array from *text*.

    Returns the parsed Python object on success, ``None`` on failure.
    Tries:
      1) Full text as JSON
      2) First ``{``…last ``}``  or first ``[``…last ``]`` substring
    """
    cleaned = _strip_code_fences(text)

    # Attempt 1 — full cleaned text
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Attempt 2 — locate outermost JSON block
    for open_ch, close_ch in (('{', '}'), ('[', ']')):
        start = cleaned.find(open_ch)
        if start == -1:
            continue
        end = cleaned.rfind(close_ch)
        if end == -1 or end <= start:
            continue
        try:
            return json.loads(cleaned[start:end + 1])
        except Exception:
            continue

    return None


def _coerce_model_evaluation(parsed, original_trade: dict | None = None) -> dict | None:
    """Extract a ``model_evaluation`` dict from the LLM's parsed JSON output.

    Tolerates multiple common response shapes:
      - List of 1 trade containing ``model_evaluation`` key
      - Dict with ``trades`` list containing 1 trade
      - Dict with ``model_evaluation`` key directly
      - Bare ``model_evaluation`` object (has recommendation/confidence/…)
      - Dict wrapping an inner-most ``model_evaluation`` at any nesting level

    Returns ``None`` only if the parsed data truly contains nothing usable.
    """
    if parsed is None:
        return None

    # --- Shape: list of trades (expected by the prompt) ---
    if isinstance(parsed, list):
        if len(parsed) >= 1 and isinstance(parsed[0], dict):
            first = parsed[0]
            if 'model_evaluation' in first and isinstance(first['model_evaluation'], dict):
                return _normalize_eval(first['model_evaluation'])
            # Maybe the list item *is* the evaluation object
            if _looks_like_eval(first):
                return _normalize_eval(first)
        return None

    if not isinstance(parsed, dict):
        return None

    # --- Shape: dict with 'trades' key ---
    trades_list = parsed.get('trades')
    if isinstance(trades_list, list) and trades_list:
        first = trades_list[0] if isinstance(trades_list[0], dict) else None
        if first and 'model_evaluation' in first:
            return _normalize_eval(first['model_evaluation'])

    # --- Shape: dict with direct 'model_evaluation' ---
    if 'model_evaluation' in parsed and isinstance(parsed['model_evaluation'], dict):
        return _normalize_eval(parsed['model_evaluation'])

    # --- Shape: bare evaluation object ---
    if _looks_like_eval(parsed):
        return _normalize_eval(parsed)

    # --- Shape: single-trade dict containing the original trade keys + model_evaluation ---
    # (model echoed the trade back as a dict instead of a single-element list)
    for key in ('model_evaluation',):
        for v in parsed.values():
            if isinstance(v, dict) and _looks_like_eval(v):
                return _normalize_eval(v)

    return None


def _looks_like_eval(d: dict) -> bool:
    """Check whether *d* looks like a model_evaluation object."""
    return bool(d and isinstance(d, dict) and d.keys() & _MODEL_EVAL_KEYS)


def _normalize_eval(raw: dict) -> dict:
    """Ensure the evaluation dict has the expected keys with safe types.

    Handles both legacy (ACCEPT/NEUTRAL/REJECT) and new (TAKE/PASS/WATCH)
    recommendation vocabularies.  New-schema fields are passed through when
    present; legacy fields are always populated for backward compatibility.
    """
    # --- Recommendation: new vocab → legacy mapping for backward compat ---
    _NEW_TO_LEGACY = {'TAKE': 'ACCEPT', 'PASS': 'REJECT', 'WATCH': 'NEUTRAL'}
    rec_raw = str(raw.get('recommendation') or 'NEUTRAL').strip().upper()
    # Accept either vocabulary
    rec_legacy = _NEW_TO_LEGACY.get(rec_raw, rec_raw)
    if rec_legacy not in ('ACCEPT', 'NEUTRAL', 'REJECT'):
        rec_legacy = 'NEUTRAL'
    # Keep the model's original word for the new field
    rec_model = rec_raw if rec_raw in ('TAKE', 'PASS', 'WATCH') else _NEW_TO_LEGACY.get(rec_legacy, rec_legacy)

    # --- Confidence (0-1 float) ---
    conf = raw.get('confidence') or raw.get('confidence_0_1')
    try:
        conf = max(0.0, min(float(conf), 1.0))
    except (TypeError, ValueError):
        conf = 0.5

    risk = str(raw.get('risk_level') or 'Moderate').strip()
    if risk not in ('Low', 'Moderate', 'High'):
        risk = 'Moderate'

    kf = raw.get('key_factors')
    if not isinstance(kf, list):
        kf = [str(kf)] if kf else []
    kf = [str(f) for f in kf if str(f or '').strip()][:6]

    summary = str(raw.get('summary') or raw.get('thesis') or '').strip()

    # --- New-schema fields (passthrough with safe defaults) ---
    score = raw.get('score_0_100')
    try:
        score = max(0, min(int(score), 100))
    except (TypeError, ValueError):
        score = None

    thesis = str(raw.get('thesis') or '').strip() or None

    key_drivers = raw.get('key_drivers')
    if not isinstance(key_drivers, list):
        key_drivers = [str(key_drivers)] if key_drivers else []
    # key_drivers can be structured objects {factor, impact, evidence}
    # or plain strings (legacy). Preserve objects, coerce bare values to strings.
    _clean_drivers = []
    for d in key_drivers[:8]:
        if isinstance(d, dict) and d.get('factor'):
            _clean_drivers.append({
                'factor': str(d['factor']),
                'impact': str(d.get('impact', 'neutral')),
                'evidence': str(d.get('evidence', '')),
            })
        elif d and str(d or '').strip():
            _clean_drivers.append(str(d))
    key_drivers = _clean_drivers

    risk_review = raw.get('risk_review')
    if not isinstance(risk_review, dict):
        risk_review = {}

    # Normalize risk_review to expanded schema (support both old and new shapes)
    if 'primary_risks' not in risk_review:
        # Old shape: { primary_risk, tail_scenario, data_quality_flag }
        legacy_risks = []
        if risk_review.get('primary_risk'):
            legacy_risks.append(str(risk_review['primary_risk']))
        if risk_review.get('tail_scenario'):
            legacy_risks.append(str(risk_review['tail_scenario']))
        risk_review = {
            'primary_risks': legacy_risks,
            'liquidity_risks': [],
            'assignment_risk': 'low',
            'volatility_risk': 'low',
            'event_risk': [],
        }
    else:
        # Ensure all keys present with safe types
        risk_review.setdefault('primary_risks', [])
        risk_review.setdefault('liquidity_risks', [])
        risk_review.setdefault('assignment_risk', 'low')
        risk_review.setdefault('volatility_risk', 'low')
        risk_review.setdefault('event_risk', [])
        for list_key in ('primary_risks', 'liquidity_risks', 'event_risk'):
            if not isinstance(risk_review[list_key], list):
                risk_review[list_key] = []
            risk_review[list_key] = [str(r) for r in risk_review[list_key] if str(r or '').strip()]

    execution_notes = raw.get('execution_notes')
    if not isinstance(execution_notes, dict):
        execution_notes = {}

    # Expanded execution_assessment (new schema)
    execution_assessment = raw.get('execution_assessment')
    if not isinstance(execution_assessment, dict):
        # Fall back to execution_notes if present
        fill_raw = (execution_notes.get('fill_probability', 'average') if execution_notes else 'average') or 'average'
        execution_assessment = {
            'fill_quality': str(fill_raw).lower() if str(fill_raw).lower() in ('good', 'average', 'poor') else 'average',
            'slippage_risk': 'medium',
            'recommended_limit': None,
            'entry_notes': str(execution_notes.get('spread_concern', '') if execution_notes else ''),
        }
    else:
        execution_assessment.setdefault('fill_quality', 'average')
        execution_assessment.setdefault('slippage_risk', 'medium')
        execution_assessment.setdefault('recommended_limit', None)
        execution_assessment.setdefault('entry_notes', '')

    # Model calculations (independently computed by LLM)
    model_calculations = raw.get('model_calculations')
    if not isinstance(model_calculations, dict):
        model_calculations = {
            'max_profit_per_share': None,
            'max_loss_per_share': None,
            'expected_value_est': None,
            'return_on_risk_est': None,
            'probability_est': None,
            'breakeven_est': None,
            'assumptions': [],
        }
    else:
        model_calculations.setdefault('max_profit_per_share', None)
        model_calculations.setdefault('max_loss_per_share', None)
        model_calculations.setdefault('expected_value_est', None)
        model_calculations.setdefault('return_on_risk_est', None)
        model_calculations.setdefault('probability_est', None)
        model_calculations.setdefault('breakeven_est', None)
        # Migrate legacy 'notes' string → 'assumptions' list
        assumptions = model_calculations.get('assumptions')
        if not isinstance(assumptions, list):
            notes = model_calculations.get('notes') or ''
            assumptions = [str(notes)] if notes else []
        model_calculations['assumptions'] = [str(a) for a in assumptions if str(a or '').strip()]
        model_calculations.pop('notes', None)

    # Edge assessment (passthrough — kept for backward compat, no longer required by prompt)
    edge_assessment = raw.get('edge_assessment')
    if not isinstance(edge_assessment, dict):
        edge_assessment = {
            'premium_vs_risk': 'neutral',
            'volatility_context': 'neutral',
            'liquidity_quality': 'medium',
            'tail_risk_profile': 'moderate',
        }
    else:
        edge_assessment.setdefault('premium_vs_risk', 'neutral')
        edge_assessment.setdefault('volatility_context', 'neutral')
        edge_assessment.setdefault('liquidity_quality', 'medium')
        edge_assessment.setdefault('tail_risk_profile', 'moderate')

    # Cross-check deltas (new — model vs engine comparison)
    cross_check_deltas = raw.get('cross_check_deltas')
    if not isinstance(cross_check_deltas, dict):
        cross_check_deltas = {}
    # Ensure each delta entry has the expected shape
    _clean_deltas = {}
    for metric_name, delta_info in cross_check_deltas.items():
        if isinstance(delta_info, dict):
            _clean_deltas[str(metric_name)] = {
                'model': delta_info.get('model'),
                'engine': delta_info.get('engine'),
                'delta_pct': delta_info.get('delta_pct'),
                'note': str(delta_info.get('note', '')),
            }
    cross_check_deltas = _clean_deltas

    # Data quality flags (new)
    data_quality_flags = raw.get('data_quality_flags')
    if not isinstance(data_quality_flags, list):
        data_quality_flags = []
    else:
        data_quality_flags = [str(f) for f in data_quality_flags if str(f or '').strip()]

    missing_data = raw.get('missing_data')
    if not isinstance(missing_data, list):
        missing_data = []
    missing_data = [str(m) for m in missing_data if str(m or '').strip()]

    # --- Content validation guards (§5) ---
    _content_warnings = []
    if not key_drivers or len(key_drivers) < 3:
        _content_warnings.append(f'key_drivers count={len(key_drivers)} (minimum 3 expected)')
    if thesis and len(thesis.split('.')) < 2:
        _content_warnings.append(f'thesis has fewer than 2 sentences')
    if model_calculations.get('expected_value_est') is None:
        _content_warnings.append('model_calculations.expected_value_est is missing')
    if model_calculations.get('return_on_risk_est') is None:
        _content_warnings.append('model_calculations.return_on_risk_est is missing')
    if _content_warnings:
        import sys as _sys
        for w in _content_warnings:
            print(f'[MODEL_CONTENT_GUARD] WARNING: {w}', file=_sys.stderr)

    # Legacy key_factors: always strings for backward compat
    _kf_legacy = kf[:]
    if not _kf_legacy and key_drivers:
        for d in key_drivers:
            if isinstance(d, dict):
                _kf_legacy.append(str(d.get('factor', '')))
            else:
                _kf_legacy.append(str(d))

    return {
        # Legacy fields (always set for backward compat)
        'recommendation': rec_legacy,
        'confidence': conf,
        'risk_level': risk,
        'key_factors': _kf_legacy,
        'summary': summary,
        # New-schema fields
        'model_recommendation': rec_model,
        'score_0_100': score,
        'confidence_0_1': conf,
        'thesis': thesis,
        'key_drivers': key_drivers,
        'risk_review': risk_review,
        'execution_notes': execution_notes,
        'execution_assessment': execution_assessment,
        'model_calculations': model_calculations,
        'edge_assessment': edge_assessment,
        'cross_check_deltas': cross_check_deltas,
        'data_quality_flags': data_quality_flags,
        'missing_data': missing_data,
    }


# ---------------------------------------------------------------------------
# Anchoring-field regression guard  (requirement §6)
# ---------------------------------------------------------------------------
_ANCHORING_FORBIDDEN_TOKENS = [
    "Risk-On", "Risk-Off",
    "composite_score", "provisional_evaluation",
    "regime_label", "regime_score", "regime_bias",
    "suggested_strategy",
]


def _check_anchoring_regression(payload_str: str, tag: str = '[GUARD]') -> None:
    """Scan serialised LLM payload for anchoring fields that must not be sent.

    In development (``DEV_GUARD_ANCHORING=1`` env var) this will raise
    ``ValueError``.  In production it logs a warning.
    """
    found = [tok for tok in _ANCHORING_FORBIDDEN_TOKENS if tok in payload_str]
    if not found:
        return

    message = f"{tag} ANCHORING GUARD: forbidden tokens found in LLM payload: {found}"
    dev_mode = os.environ.get("DEV_GUARD_ANCHORING", "1") == "1"
    if dev_mode:
        raise ValueError(message)
    else:
        print(f"WARNING: {message}")


# ---------------------------------------------------------------------------
# Facts-only payload builder  (requirement §1–§2)
# ---------------------------------------------------------------------------
def _build_facts_only_payload(trade: dict, *, include_crosscheck: bool = True) -> dict:
    """Build the LLM input payload using the new ``AnalysisFacts`` contract.

    Uses ``build_analysis_facts()`` to normalize the raw trade dict, then
    optionally attaches deterministic engine metrics under a separate
    ``engine_crosscheck`` key (controlled by *include_crosscheck* or the
    ``MODEL_CROSSCHECK_MODE`` env-var, which defaults to ``"on"``).

    Input fields → output mapping is documented in
    ``trade_analysis_engine.build_analysis_facts`` and
    ``trade_analysis_engine.compute_trade_metrics``.
    """
    from common.trade_analysis_engine import build_analysis_facts, compute_trade_metrics

    facts = build_analysis_facts(trade)
    engine_metrics = compute_trade_metrics(facts)

    # Build the payload from the structured facts
    payload = {
        "trade": {
            "symbol": (facts.get("underlying") or {}).get("symbol"),
            "spread_type": (facts.get("structure") or {}).get("spread_type"),
            "short_strike": (facts.get("structure") or {}).get("short_strike"),
            "long_strike": (facts.get("structure") or {}).get("long_strike"),
            "underlying_price": (facts.get("underlying") or {}).get("price"),
            "expiration": (facts.get("structure") or {}).get("expiration"),
            "dte": (facts.get("structure") or {}).get("dte"),
            "net_credit": (facts.get("pricing") or {}).get("net_credit"),
            "width": (facts.get("structure") or {}).get("width"),
        },
        "volatility": facts.get("volatility") or {},
        "liquidity": facts.get("liquidity") or {},
        "market_context": facts.get("market_context") or {},
        "probability": facts.get("probability") or {},
        "data_quality_flags": facts.get("data_quality_flags") or [],
    }

    # Optionally include deterministic engine calculations for cross-check
    crosscheck_mode = os.environ.get("MODEL_CROSSCHECK_MODE", "on")
    if include_crosscheck and crosscheck_mode != "off":
        payload["engine_crosscheck"] = engine_metrics

    return payload


# ---------------------------------------------------------------------------
# Independent analysis system prompt  (requirement §3–§5)
# ---------------------------------------------------------------------------
_INDEPENDENT_ANALYSIS_PROMPT = """\
You are an independent options risk analyst.
Write like a professional options risk analyst.
Be concise but analytically specific.
Avoid vague phrases like "looks good" or "appears strong."
Always tie conclusions to provided data.

CRITICAL INSTRUCTIONS:
- Base your evaluation ONLY on the factual data provided below.
- Do NOT assume any prior scoring, recommendation, or regime classification.
- Do NOT assume the trade is good because metrics appear favourable.
- If risk/reward is unattractive, you MUST say so clearly.
- If key data is missing or weak, explicitly call it out.
- Prefer cautious, risk-aware reasoning.
- You are NOT confirming anyone else's analysis — you are forming your own.
- Do NOT copy or reference any external scoring system — compute your own metrics.

DATA QUALITY FLAGS:
- The input includes a "data_quality_flags" array listing any fields that were \
missing or invalid in the original trade data.  Be conservative when these flags \
indicate important data is missing.

CROSS-CHECK PROTOCOL:
- The input MAY include an "engine_crosscheck" object with deterministic engine \
calculations (max_profit, max_loss, breakeven, return_on_risk, EV, Kelly, POP proxy).
- FIRST compute your own values independently (model_calculations).
- THEN compare to engine_crosscheck values — if your values differ by more than \
10%, explain WHY in model_calculations.notes and in cross_check_deltas.
- The engine values are NOT authoritative — they can be wrong if inputs are bad.  \
Your independent calculation takes precedence when you can justify it.

YOU MUST INDEPENDENTLY COMPUTE (in model_calculations):
- max_profit_per_share: net_credit for credit spreads.
- max_loss_per_share: width - net_credit for credit spreads.
- expected_value_est: Consider probability vs payoff asymmetry. \
Use POP or short-strike delta as probability proxy. EV ~ (POP * max_profit) - ((1-POP) * max_loss).
- return_on_risk_est: credit / (width - credit) or max_profit / max_loss.
- probability_est: Use short-strike delta as a probability proxy if available, \
or estimate from distance-to-strike / sigma.
- breakeven_est: For credit spreads, short_strike +/- net_credit (direction depends on put/call).
- assumptions: Array of strings explaining the assumptions behind your calculations.
- Be conservative when liquidity is weak or data is sparse.
- If insufficient data exists, estimate conservatively and note assumptions.

EVALUATION DIMENSIONS (consider all holistically):
1. Reward vs risk profile (EV, max profit/loss ratio, return on risk)
2. Distance to strikes / probability profile (POP, strike distance in sigma/%)
3. Volatility environment (IV vs RV, IV rank, VIX level)
4. Liquidity (open interest, volume, bid-ask spread)
5. Tail risk exposure (width, max loss, extreme move scenarios)
6. Execution realism (bid-ask spread, fill probability at limit price)
7. Data quality (are critical fields present and plausible?)

SCORING DEFINITION:
- score_0_100: A holistic attractiveness score (0 = terrible, 100 = exceptional) \
based ONLY on the provided facts. Most decent trades should fall in 40-70 range. \
Reserve 80+ for truly exceptional setups. Below 30 = material concerns.

RULES FOR DEPTH:
- thesis: MUST be 2-3 sentences minimum. State the core trade thesis and the single \
biggest factor driving the recommendation.
- key_drivers: Return AT LEAST 3 drivers. Each must be a structured object with \
factor, impact (positive|negative|neutral), \
and evidence (specific data that led to this assessment). \
Prefer specific, data-linked reasoning over generic statements.
- risk_review: Must include at least 2 primary_risks. Explicitly state the single \
largest concern.
- Mention the largest concern explicitly in risk_review or thesis.

OUTPUT FORMAT — return valid JSON only (no markdown, no code fences):
{
  "recommendation": "TAKE" | "PASS" | "WATCH",
  "score_0_100": <integer 0-100>,
  "confidence_0_1": <float 0.0-1.0>,

  "thesis": "<2-3 sentence high-level summary>",

  "model_calculations": {
    "max_profit_per_share": <number or null>,
    "max_loss_per_share": <number or null>,
    "expected_value_est": <number or null>,
    "return_on_risk_est": <number or null>,
    "probability_est": <number or null>,
    "breakeven_est": <number or null>,
    "assumptions": ["<string>", ...]
  },

  "cross_check_deltas": {
    "<metric_name>": {"model": <number>, "engine": <number>, "delta_pct": <number>, "note": "<why>"}
  },

  "key_drivers": [
    {
      "factor": "<string>",
      "impact": "positive" | "negative" | "neutral",
      "evidence": "<what data led to this>"
    }
  ],

  "risk_review": {
    "primary_risks": ["<string>", ...],
    "liquidity_risks": ["<string>", ...],
    "assignment_risk": "low" | "medium" | "high",
    "volatility_risk": "low" | "medium" | "high",
    "event_risk": ["<string>", ...]
  },

  "execution_assessment": {
    "fill_quality": "good" | "average" | "poor",
    "slippage_risk": "low" | "medium" | "high",
    "recommended_limit": <number or null>,
    "entry_notes": "<string>"
  },

  "data_quality_flags": ["<string>", ...],
  "missing_data": ["<field_name>", ...]
}

Return ONE JSON object with exactly these keys. No additional keys.
"""


def generate_mock_report() -> str:
    """Create a mocked analysis JSON file in `results/` and return the filename.

    This uses `quant_analysis.enrich_trades_batch` to produce the enriched metrics
    so the frontend receives the expanded metric set.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = f"analysis_{ts}.json"

    # Base trade templates with minimal fields — the enrichment function will add metrics
    # Synthetic-but-plausible SPY spreads calibrated around SPY ~681 and IV ~0.17–0.22
    # NOTE: These are not live quotes; they’re designed to be realistic inputs for testing your pipeline.

    base_trades = [
        # --- Put credit spreads (bull put) ---
        {'underlying':'SPY','spread_type':'put_credit','short_strike':665,'long_strike':660,'underlying_price':681.3,'net_credit':0.92,'dte':7,'iv':0.19,'bid':0.90,'ask':0.94,'open_interest':4200,'volume':1800},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':660,'long_strike':650,'underlying_price':681.3,'net_credit':1.45,'dte':14,'iv':0.20,'bid':1.42,'ask':1.48,'open_interest':3800,'volume':1500},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':655,'long_strike':650,'underlying_price':681.3,'net_credit':0.78,'dte':10,'iv':0.18,'bid':0.75,'ask':0.81,'open_interest':5100,'volume':2200},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':650,'long_strike':640,'underlying_price':681.3,'net_credit':1.18,'dte':21,'iv':0.19,'bid':1.14,'ask':1.22,'open_interest':2900,'volume':900},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':645,'long_strike':640,'underlying_price':681.3,'net_credit':0.64,'dte':28,'iv':0.17,'bid':0.61,'ask':0.67,'open_interest':2600,'volume':700},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':640,'long_strike':630,'underlying_price':681.3,'net_credit':0.95,'dte':35,'iv':0.16,'bid':0.91,'ask':0.99,'open_interest':2100,'volume':520},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':670,'long_strike':665,'underlying_price':681.3,'net_credit':1.25,'dte':5,'iv':0.22,'bid':1.21,'ask':1.29,'open_interest':6100,'volume':3400},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':635,'long_strike':625,'underlying_price':681.3,'net_credit':0.72,'dte':42,'iv':0.15,'bid':0.69,'ask':0.75,'open_interest':1800,'volume':380},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':675,'long_strike':670,'underlying_price':681.3,'net_credit':1.62,'dte':3,'iv':0.24,'bid':1.58,'ask':1.66,'open_interest':7400,'volume':4100},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':625,'long_strike':615,'underlying_price':681.3,'net_credit':0.48,'dte':45,'iv':0.14,'bid':0.45,'ask':0.51,'open_interest':1500,'volume':260},

        # --- Call credit spreads (bear call) ---
        {'underlying':'SPY','spread_type':'call_credit','short_strike':700,'long_strike':705,'underlying_price':681.3,'net_credit':0.82,'dte':7,'iv':0.19,'bid':0.79,'ask':0.85,'open_interest':3600,'volume':1400},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':705,'long_strike':715,'underlying_price':681.3,'net_credit':1.12,'dte':14,'iv':0.20,'bid':1.08,'ask':1.16,'open_interest':2400,'volume':880},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':710,'long_strike':715,'underlying_price':681.3,'net_credit':0.63,'dte':10,'iv':0.18,'bid':0.60,'ask':0.66,'open_interest':4100,'volume':1700},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':715,'long_strike':725,'underlying_price':681.3,'net_credit':0.88,'dte':21,'iv':0.17,'bid':0.84,'ask':0.92,'open_interest':1900,'volume':540},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':720,'long_strike':725,'underlying_price':681.3,'net_credit':0.52,'dte':28,'iv':0.16,'bid':0.49,'ask':0.55,'open_interest':1700,'volume':430},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':690,'long_strike':695,'underlying_price':681.3,'net_credit':1.05,'dte':5,'iv':0.22,'bid':1.01,'ask':1.09,'open_interest':5200,'volume':2600},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':695,'long_strike':705,'underlying_price':681.3,'net_credit':1.34,'dte':9,'iv':0.21,'bid':1.30,'ask':1.38,'open_interest':3100,'volume':1200},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':730,'long_strike':735,'underlying_price':681.3,'net_credit':0.41,'dte':35,'iv':0.15,'bid':0.39,'ask':0.43,'open_interest':1300,'volume':260},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':725,'long_strike':735,'underlying_price':681.3,'net_credit':0.69,'dte':42,'iv':0.14,'bid':0.66,'ask':0.72,'open_interest':1200,'volume':210},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':685,'long_strike':690,'underlying_price':681.3,'net_credit':0.74,'dte':3,'iv':0.24,'bid':0.71,'ask':0.77,'open_interest':6800,'volume':3900},
    ]

    # small synthetic price history to compute trend/rv/rsi
    prices_history = [90 + i * 0.5 for i in range(60)]

    # Import enrichment and CreditSpread locally to avoid circular imports at module load
    enriched = base_trades
    try:
        from .quant_analysis import enrich_trades_batch, CreditSpread, _norm_cdf
    except Exception:
        enrich_trades_batch = None
        CreditSpread = None
        _norm_cdf = None

    # If enrichment is available, run it and merge CreditSpread.summary() for frontend compatibility
    # Provide a VIX value to the enrichment to allow classify_market_regime to use it
    vix_value = 17.44
    if enrich_trades_batch:
        try:
            enriched = enrich_trades_batch(
                base_trades,
                prices_history=prices_history,
                vix=vix_value,
                iv_low=0.10,
                iv_high=0.30,
            )
        except Exception:
            print('[utils] Warning: enrich_trades_batch raised an exception; falling back to base trades')
            enriched = base_trades

    if enriched and CreditSpread:
        final = []
        print('[utils] Generating enriched report — merging CreditSpread summaries')
        for tr in enriched:
            try:
                cs_kwargs = {
                    'spread_type': tr.get('spread_type'),
                    'underlying_price': tr.get('underlying_price') or tr.get('price'),
                    'short_strike': tr.get('short_strike'),
                    'long_strike': tr.get('long_strike'),
                    'net_credit': tr.get('net_credit') or tr.get('max_profit_per_share') or 0.0,
                    'dte': tr.get('dte'),
                    'short_delta_abs': tr.get('short_delta_abs'),
                    'implied_vol': tr.get('iv') or tr.get('implied_vol'),
                    'realized_vol': tr.get('realized_vol'),
                }
                cs = CreditSpread(**cs_kwargs)
                summary = cs.summary(iv_rank_value=tr.get('iv_rank'))
                # Merge enrichment fields (they may include market_regime, rsi, etc.)
                merged = {**summary, **tr}
                # ensure VIX is present in the merged output (take from trade or our vix_value)
                if merged.get('vix') is None:
                    merged['vix'] = vix_value

                # If p_win_used wasn't available from the CreditSpread summary, try a fallback
                # using the short_strike_z (distance in sigma). POP ≈ NormalCDF(short_strike_z).
                if merged.get('p_win_used') is None and _norm_cdf is not None:
                    sz = merged.get('short_strike_z')
                    try:
                        if sz is not None:
                            est_pop = float(_norm_cdf(sz))
                            merged['p_win_used'] = est_pop
                            # recompute EV/Kelly using the same CreditSpread instance
                            try:
                                merged['ev_per_share'] = cs.expected_value_per_share(p_win=est_pop)
                                merged['ev_to_risk'] = cs.ev_to_risk(p_win=est_pop)
                                merged['kelly_fraction'] = cs.kelly_fraction(p_win=est_pop)
                                merged['trade_quality_score'] = cs.trade_quality_score(p_win=est_pop, iv_rank_value=tr.get('iv_rank'))
                            except Exception:
                                pass
                    except Exception:
                        pass
                # ensure VIX is present in the merged output (take from trade or our vix_value)
                if merged.get('vix') is None:
                    merged['vix'] = vix_value

                # Ensure some frontend-expected fields exist (fallbacks)
                if merged.get('max_profit_per_share') is None:
                    merged['max_profit_per_share'] = merged.get('net_credit')
                if merged.get('max_loss_per_share') is None:
                    width = merged.get('width') if merged.get('width') is not None else (abs(merged.get('long_strike',0) - merged.get('short_strike',0)))
                    merged['max_loss_per_share'] = width - (merged.get('net_credit') or 0)
                if merged.get('break_even') is None:
                    if merged.get('spread_type') == 'put_credit':
                        merged['break_even'] = merged.get('short_strike') - (merged.get('net_credit') or 0)
                    else:
                        merged['break_even'] = merged.get('short_strike') + (merged.get('net_credit') or 0)

                final.append(merged)
            except Exception:
                print('[utils] Warning: failed to merge CreditSpread summary for trade:', tr)
                final.append(tr)

        enriched = final

    file_path = RESULTS_DIR / filename
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(enriched, f, indent=2, default=str)

    # After writing the base enriched report, optionally call the local LM Studio model
    # to append a `model_evaluation` object to each trade. By default this is disabled
    # to avoid automatic model calls on report generation. Set environment variable
    # AUTO_CALL_MODEL=1 to enable automatic calling (useful for batch runs).
    def _call_model_and_append(trades, target_path, model_url='http://localhost:1234/v1/chat/completions', retries=2, timeout=30, batch_size=1):
        """
        Send trades to the model in batches of `batch_size`, merge returned
        `model_evaluation` into each trade, and write combined output to a new
        file prefixed with 'mode_'. Returns True on success.
        """
        # Prompt/instructions for the model: return only the JSON array of trades
        PROMPT = (
            "You are an options trading risk advisor.\n\n"
            "You will receive a JSON array of a single credit spread with many metrics.\n"
            "Return ONLY a JSON array of the SAME trades in the SAME order.\n"
            "For each trade object, append EXACTLY one new key: \"model_evaluation\".\n"
            "Do NOT add any filenames, metadata, or wrapper objects.\n"
            "Do not modify, remove, rename, or reorder existing keys/values.\n\n"

            "EVALUATION GOAL:\n"
            "Provide a recommendation based on TRADE QUALITY, emphasizing risk-adjusted returns.\n"
            "You MUST explicitly consider max_profit vs max_loss, expected_value, return_on_risk, and liquidity.\n"
            "Market regime and probability can SUPPORT a decision but MUST NOT override negative expectancy.\n\n"

            "HARD DECISION RULES (apply in this exact priority order):\n"
            "1) If \"ev_per_share\" or \"expected_value\" exists and is < 0 -> recommendation MUST be \"REJECT\".\n"
            "2) If \"kelly_fraction\" exists and is < 0 -> recommendation MUST be \"REJECT\".\n"
            "3) If max_loss is missing, or max_loss <= 0, or max_profit is missing -> recommendation MUST be \"NEUTRAL\" with low confidence.\n"
            "4) If return_on_risk exists and is < 0.10 (10%) -> default to \"REJECT\" unless EV is strongly positive and liquidity is excellent.\n"
            "5) If bid_ask_spread_pct exists and is > 0.12 (12%) -> at most \"NEUTRAL\" (execution risk too high).\n\n"

            "SECONDARY GUIDELINES (only if no hard-rule forced a decision):\n"
            "- Prefer ACCEPT when EV > 0, Kelly > 0, return_on_risk >= 0.10, and short_strike_z >= 1.0.\n"
            "- Prefer NEUTRAL when EV is small/near zero, or when there is meaningful event/volatility risk.\n"
            "- Prefer REJECT when risk:reward is extreme (max_loss / max_profit > 8), even if probability is high.\n"
            "- Treat probability/pop as supportive evidence only; do not recommend a trade solely because POP is high.\n\n"

            "DATA QUALITY GUARDS:\n"
            "- If a metric is clearly implausible (e.g., iv_rv_ratio > 6, rsi14 == 100 with no trend context, "
            "or realized_vol appears near zero), mention possible data issue in key_factors and reduce confidence.\n"
            "- Never invent missing metrics.\n\n"

            "The \"model_evaluation\" object MUST contain EXACTLY these keys:\n"
            "- recommendation: one of [\"ACCEPT\", \"NEUTRAL\", \"REJECT\"]\n"
            "- confidence: number between 0 and 1\n"
            "- risk_level: one of [\"Low\", \"Moderate\", \"High\"]\n"
            "- key_factors: array of 2 to 6 short strings (include at least one factor about risk/reward or EV)\n"
            "- summary: one paragraph string\n\n"

            "CONFIDENCE RULES:\n"
            "- If a hard rule forces REJECT (negative EV or negative Kelly), confidence should be >= 0.80.\n"
            "- If required inputs are missing or data quality is questionable, confidence should be <= 0.40.\n\n"

            "OUTPUT RULES:\n"
            "- Output must be valid JSON only (a single JSON array).\n"
            "- Do not include any commentary or surrounding text.\n"
            "- Preserve all numeric values exactly as received.\n"
            "- If you cannot evaluate a trade, still return the trade but set \"model_evaluation\" with "
            "\"recommendation\"=\"NEUTRAL\" and low confidence.\n"
        )


        out_path = Path(target_path).parent / ("mode_" + Path(target_path).name)
        all_updated = []
        model_out_path = Path(target_path).parent / ("model_" + Path(target_path).name)

        def _send_batch(batch):
            payload = {
                'messages': [
                    {'role': 'system', 'content': PROMPT},
                    {'role': 'user', 'content': json.dumps(batch, ensure_ascii=False, indent=None)}
                ],
                'max_tokens': 2048,
                'temperature': 0.0,
            }

            attempt = 0
            last_err = None
            while attempt <= retries:
                try:
                    attempt += 1
                    print(f"[utils] Calling model at {model_url} (attempt {attempt}) for batch size {len(batch)}")
                    resp = requests.post(model_url, json=payload, timeout=timeout)
                    resp.raise_for_status()

                    # extract assistant text from chat response
                    try:
                        resp_json = resp.json()
                    except ValueError:
                        resp_json = None

                    assistant_text = None
                    if isinstance(resp_json, dict):
                        chs = resp_json.get('choices') or []
                        if chs and isinstance(chs, list):
                            first = chs[0]
                            if isinstance(first, dict):
                                msg = first.get('message')
                                if isinstance(msg, dict) and 'content' in msg:
                                    assistant_text = msg.get('content')
                                elif 'text' in first:
                                    assistant_text = first.get('text')
                    if assistant_text is None:
                        assistant_text = resp.text.strip()

                    # parse JSON from assistant_text
                    parsed = None
                    try:
                        parsed = json.loads(assistant_text)
                    except Exception:
                        # heuristic extraction
                        txt = assistant_text
                        start_idx = None
                        for ch in ('[', '{'):
                            i = txt.find(ch)
                            if i != -1:
                                start_idx = i
                                break
                        if start_idx is not None:
                            open_ch = txt[start_idx]
                            close_ch = ']' if open_ch == '[' else '}'
                            end_idx = txt.rfind(close_ch)
                            if end_idx != -1:
                                try:
                                    parsed = json.loads(txt[start_idx:end_idx + 1])
                                except Exception:
                                    parsed = None

                    if parsed is None:
                        print('[utils] Could not parse model response for batch; raw response:')
                        print(resp.text)
                        return None

                    # parsed should be a list or dict with 'trades'
                    if isinstance(parsed, dict) and 'trades' in parsed:
                        model_trades = parsed['trades']
                    elif isinstance(parsed, list):
                        model_trades = parsed
                    else:
                        print('[utils] Unexpected parsed model shape for batch; raw parsed:')
                        print(parsed)
                        return None

                    if len(model_trades) != len(batch):
                        print('[utils] Model returned different number of trades for batch; skipping')
                        return None

                    # merge model_evaluation
                    updated = []
                    for orig, m in zip(batch, model_trades):
                        new = dict(orig)
                        if isinstance(m, dict) and 'model_evaluation' in m:
                            new['model_evaluation'] = m['model_evaluation']
                        else:
                            # maybe the model returned the evaluation nested differently
                            # try to find keys matching recommendation/confidence
                            if isinstance(m, dict) and any(k in m for k in ('recommendation','confidence','risk_level','key_factors','summary')):
                                # assume m itself is the evaluation object
                                new['model_evaluation'] = {k: m.get(k) for k in ('recommendation','confidence','risk_level','key_factors','summary') if k in m}
                        updated.append(new)

                    return updated

                except RequestException as e:
                    last_err = e
                    print(f"[utils] Model call failed (attempt {attempt}): {e}")
                    time.sleep(1)

            print('[utils] All model call attempts failed for this batch; last error:', last_err)
            return None

        # Iterate batches and append each returned evaluated trade to model_<filename>
        for i in range(0, len(trades), batch_size):
            batch = trades[i:i+batch_size]
            res = _send_batch(batch)
            if res is None:
                print(f"[utils] Aborting model batching at batch starting index {i}")
                return False

            # res is a list of evaluated trades for this batch (same order)
            for evaluated_trade in res:
                # read existing file if present
                existing = []
                try:
                    if model_out_path.exists():
                        with open(model_out_path, 'r', encoding='utf-8') as rf:
                            existing = json.load(rf)
                except Exception:
                    print(f"[utils] Warning: failed to read existing model output {model_out_path}; starting fresh")
                    existing = []

                existing.append(evaluated_trade)

                try:
                    with open(model_out_path, 'w', encoding='utf-8') as wf:
                        json.dump(existing, wf, indent=2, default=str)
                    print(f"[utils] Appended evaluated trade to {model_out_path} (total={len(existing)})")
                except Exception as e:
                    print('[utils] Failed to write model output file:', e)
                    return False

        return True

    # Try calling model and append evaluations if explicitly enabled by env var.
    # This prevents automatic network calls during report generation unless desired.
    try:
        import os
        if os.environ.get('AUTO_CALL_MODEL', '0') == '1':
            _call_model_and_append(enriched, file_path)
        else:
            print('[utils] AUTO_CALL_MODEL not set; skipping automatic model calls')
    except Exception:
        print('[utils] Unexpected error while calling model; continuing without model evaluations')

    return filename


def analyze_trade_with_model(trade: dict, source_filename: str, model_url='http://localhost:1234/v1/chat/completions', retries=2, timeout=120):
    """Send a single trade to the local model and append the evaluated trade
    to ``results/model_<source_filename>``.  Returns the evaluated trade dict
    (including ``model_evaluation`` and ``engine_calculations``) on success.

    **Pipeline:**
    1. Hard-gate override (deterministic REJECT for clearly bad trades)
    2. Build ``AnalysisFacts`` → compute deterministic engine metrics
    3. Build facts-only LLM payload (with optional cross-check data)
    4. Call LLM → extract JSON → coerce shape → normalize
    5. Schema validate → repair retry if violations found
    6. Attach both ``engine_calculations`` and ``model_evaluation`` to result


    On model-call failure a provisional NEUTRAL evaluation is persisted and
    returned so the UI receives a deterministic response (no 500).
    """
    from common.trade_analysis_engine import (
        build_analysis_facts,
        compute_trade_metrics,
        validate_model_schema,
    )

    request_id = uuid.uuid4().hex[:10]
    TAG = f'[MODEL_TRACE:{request_id}]'

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    model_out_path = RESULTS_DIR / ("model_" + Path(source_filename).name)

    PROMPT = _INDEPENDENT_ANALYSIS_PROMPT

    # ── Step 1: Pre-call deterministic override ──────────────────
    base_me = trade.get('model_evaluation') or {}
    me_override = hard_gate_override(trade, base_me)

    # ── Step 2: Build AnalysisFacts + compute engine metrics ─────
    analysis_facts = build_analysis_facts(trade)
    engine_metrics = compute_trade_metrics(analysis_facts)

    # If hard gate forced a decision, persist with engine metrics and return
    if me_override and me_override.get('_hard_gate_forced'):
        forced_eval = dict(me_override)
        forced_eval.pop('_hard_gate_forced', None)
        new = dict(trade)
        new['model_evaluation'] = forced_eval
        new['engine_calculations'] = engine_metrics
        _persist_model_result(new, model_out_path, TAG)
        print(f"{TAG} Hard-gate forced evaluation: {forced_eval.get('recommendation')}")
        return new

    # ── Step 3: Build facts-only payload ─────────────────────────
    facts_payload = _build_facts_only_payload(trade)
    facts_json = json.dumps(facts_payload, ensure_ascii=False, indent=None)

    # Regression guard: verify no anchoring tokens leaked into the payload
    _check_anchoring_regression(facts_json, TAG)

    payload = {
        'messages': [
            {'role': 'system', 'content': PROMPT},
            {'role': 'user', 'content': facts_json}
        ],
        'max_tokens': 2048,
        'temperature': 0.0,
        'stream': False,
    }

    symbol = trade.get('symbol') or trade.get('underlying') or '?'
    strategy = trade.get('strategy_id') or trade.get('spread_type') or '?'
    print(f"{TAG} start — symbol={symbol} strategy={strategy} source={source_filename} url={model_url} timeout={timeout}")

    # ── Step 4: Call LLM ─────────────────────────────────────────
    attempt = 0
    last_err = None
    raw_snippet = None
    model_eval = None
    while attempt <= retries:
        try:
            attempt += 1
            print(f"{TAG} attempt {attempt}/{retries + 1}")
            resp = requests.post(model_url, json=payload, timeout=timeout)
            print(f"{TAG} HTTP {resp.status_code} ({len(resp.content)} bytes, {resp.elapsed.total_seconds():.1f}s)")
            resp.raise_for_status()

            # --- Extract assistant text from OpenAI-compatible response ---
            resp_json = None
            try:
                resp_json = resp.json()
            except Exception:
                pass

            assistant_text = None
            if isinstance(resp_json, dict):
                chs = resp_json.get('choices') or []
                if chs and isinstance(chs, list):
                    first = chs[0]
                    if isinstance(first, dict):
                        msg = first.get('message')
                        if isinstance(msg, dict) and 'content' in msg:
                            assistant_text = msg.get('content')
                        elif 'text' in first:
                            assistant_text = first.get('text')
            if assistant_text is None:
                assistant_text = getattr(resp, 'text', '') or ''

            raw_snippet = (assistant_text[:500] + '…') if len(assistant_text) > 500 else assistant_text
            print(f"{TAG} assistant_text length={len(assistant_text)} first500={raw_snippet!r}")

            # --- Robust JSON extraction (code fences, bare objects, arrays) ---
            parsed = _find_json_block(assistant_text)
            if parsed is None:
                print(f"{TAG} FAIL: could not extract any JSON from LLM output")
                last_err = f'unparsable_response (requestId={request_id})'
                break  # don't retry — the model answered, just not parseable

            # --- Tolerant shape coercion ---
            model_eval = _coerce_model_evaluation(parsed, original_trade=trade)

            if model_eval is None:
                # Legacy fallback: try the old list-of-trades shape
                model_eval = _try_legacy_list_shape(parsed, TAG)

            if model_eval is None:
                print(f"{TAG} FAIL: parsed JSON but could not extract model_evaluation; parsed type={type(parsed).__name__}")
                print(f"{TAG} parsed keys={list(parsed.keys()) if isinstance(parsed, dict) else 'N/A'}")
                last_err = f'unexpected_shape (requestId={request_id})'
                break  # don't retry — shape issue won't change

            # ── Step 5: Schema validation + repair retry ─────────
            violations = validate_model_schema(model_eval)
            if violations:
                print(f"{TAG} SCHEMA_VIOLATIONS: {violations}")
                repair_eval = _attempt_repair(
                    model_eval, violations, facts_json, PROMPT,
                    model_url, timeout, TAG,
                )
                if repair_eval is not None:
                    model_eval = repair_eval
                else:
                    # Accept partial output — violations are logged but not fatal
                    print(f"{TAG} REPAIR_FAILED — accepting partial model output with {len(violations)} violation(s)")

            # --- Success! ---
            new = dict(trade)
            new['model_evaluation'] = model_eval
            new['engine_calculations'] = engine_metrics
            _persist_model_result(new, model_out_path, TAG)
            print(f"{TAG} SUCCESS — recommendation={model_eval.get('recommendation')} model_rec={model_eval.get('model_recommendation')} score={model_eval.get('score_0_100')} confidence={model_eval.get('confidence')}")
            return new

        except RequestException as e:
            last_err = e
            print(f"{TAG} RequestException on attempt {attempt}: {e}")
            if attempt <= retries:
                time.sleep(2)

    print(f"{TAG} FALLBACK — all attempts failed; last_err={last_err}")

    # Fallback: persist a provisional NEUTRAL evaluation so caller receives a response
    fallback_eval = {
        'recommendation': 'NEUTRAL',
        'confidence': 0.35,
        'risk_level': 'Moderate',
        'key_factors': [
            f'Model returned unparsable response (requestId={request_id})',
            'Saved provisional NEUTRAL evaluation',
        ],
        'summary': f'Model call failed (requestId={request_id}); saved provisional NEUTRAL evaluation so UI reflects a decision.',
    }
    if raw_snippet:
        fallback_eval['_raw_snippet'] = str(raw_snippet)[:300]

    try:
        if me_override and isinstance(me_override, dict) and not me_override.get('_hard_gate_forced'):
            if me_override.get('recommendation'):
                fallback_eval['recommendation'] = me_override.get('recommendation')
            if me_override.get('confidence') is not None:
                fallback_eval['confidence'] = me_override.get('confidence')
            if me_override.get('risk_level'):
                fallback_eval['risk_level'] = me_override.get('risk_level')
            if me_override.get('key_factors'):
                fallback_eval['key_factors'] = ([f'Model response unparsable (requestId={request_id})'] + me_override.get('key_factors'))[:6]
    except Exception:
        pass

    new = dict(trade)
    new['model_evaluation'] = fallback_eval
    new['engine_calculations'] = engine_metrics
    _persist_model_result(new, model_out_path, TAG)
    return new


def _attempt_repair(
    partial_eval: dict,
    violations: list[str],
    facts_json: str,
    system_prompt: str,
    model_url: str,
    timeout: int,
    tag: str,
) -> dict | None:
    """Attempt a single repair LLM call to fix schema violations.

    Sends the partial output + violation list back to the model and asks it
    to return a corrected JSON object.  Returns the repaired ``model_eval``
    on success, ``None`` on failure.
    """
    repair_prompt = (
        "Your previous response had schema violations.  Fix them and return "
        "a COMPLETE, corrected JSON object matching the required schema.\n\n"
        f"VIOLATIONS:\n{json.dumps(violations, indent=2)}\n\n"
        f"YOUR PARTIAL OUTPUT:\n{json.dumps(partial_eval, indent=2, default=str)}\n\n"
        "Return ONLY valid JSON matching the schema in the system prompt. "
        "Do not include markdown, code fences, or commentary."
    )

    repair_payload = {
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': facts_json},
            {'role': 'assistant', 'content': json.dumps(partial_eval, default=str)},
            {'role': 'user', 'content': repair_prompt},
        ],
        'max_tokens': 2048,
        'temperature': 0.0,
        'stream': False,
    }

    try:
        print(f"{tag} REPAIR attempt — {len(violations)} violation(s)")
        resp = requests.post(model_url, json=repair_payload, timeout=timeout)
        resp.raise_for_status()

        resp_json = None
        try:
            resp_json = resp.json()
        except Exception:
            pass

        assistant_text = None
        if isinstance(resp_json, dict):
            chs = resp_json.get('choices') or []
            if chs and isinstance(chs, list):
                first = chs[0]
                if isinstance(first, dict):
                    msg = first.get('message')
                    if isinstance(msg, dict) and 'content' in msg:
                        assistant_text = msg.get('content')
                    elif 'text' in first:
                        assistant_text = first.get('text')
        if assistant_text is None:
            assistant_text = getattr(resp, 'text', '') or ''

        parsed = _find_json_block(assistant_text)
        if parsed is None:
            print(f"{tag} REPAIR_FAIL: could not parse repair response")
            return None

        repaired_eval = _coerce_model_evaluation(parsed)
        if repaired_eval is None:
            print(f"{tag} REPAIR_FAIL: coercion failed on repair response")
            return None

        # Re-validate
        from common.trade_analysis_engine import validate_model_schema
        new_violations = validate_model_schema(repaired_eval)
        if new_violations:
            print(f"{tag} REPAIR_PARTIAL: repair still has {len(new_violations)} violation(s): {new_violations}")
            # Accept if fewer violations than before
            if len(new_violations) < len(violations):
                print(f"{tag} REPAIR_IMPROVED: accepting repaired output ({len(violations)} → {len(new_violations)} violations)")
                return repaired_eval
            return None

        print(f"{tag} REPAIR_SUCCESS: all violations resolved")
        return repaired_eval

    except Exception as e:
        print(f"{tag} REPAIR_ERROR: {e}")
        return None


def _try_legacy_list_shape(parsed, tag: str) -> dict | None:
    """Handle the old prompt's expected shape: a JSON array of 1 trade with model_evaluation."""
    model_trades = None
    if isinstance(parsed, dict) and 'trades' in parsed:
        model_trades = parsed['trades']
    elif isinstance(parsed, list):
        model_trades = parsed
    else:
        return None

    if not isinstance(model_trades, list) or not model_trades:
        return None

    m = model_trades[0]
    if isinstance(m, dict) and 'model_evaluation' in m:
        print(f"{tag} legacy list-of-trades shape detected — extracting model_evaluation")
        return _normalize_eval(m['model_evaluation'])
    if isinstance(m, dict) and _looks_like_eval(m):
        print(f"{tag} legacy list shape — first element IS the evaluation")
        return _normalize_eval(m)
    return None


def _persist_model_result(evaluated_trade: dict, model_out_path: Path, tag: str = '[utils]'):
    """Append an evaluated trade to the model output file."""
    existing = []
    try:
        if model_out_path.exists():
            with open(model_out_path, 'r', encoding='utf-8') as rf:
                existing = json.load(rf)
    except Exception:
        existing = []

    existing.append(evaluated_trade)
    try:
        with open(model_out_path, 'w', encoding='utf-8') as wf:
            json.dump(existing, wf, indent=2, default=str)
        print(f"{tag} Persisted to {model_out_path} (total={len(existing)})")
    except Exception as e:
        print(f"{tag} Failed to write {model_out_path}: {e}")


def hard_gate_override(trade: dict, me: dict) -> dict:
    """Apply deterministic rules to force REJECT decisions before calling the model.

    Returns an updated `me` dict when a hard gate forces a decision. The returned
    dict will include `_hard_gate_forced=True` when a forced decision is applied.
    """
    # Read and normalize candidate fields
    def _to_float(x):
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        if isinstance(x, str):
            s = x.strip().replace('%', '')
            try:
                return float(s)
            except Exception:
                return None
        return None

    ev = _to_float(trade.get('ev_per_share', trade.get('expected_value')))
    kelly = _to_float(trade.get('kelly_fraction'))
    ror = _to_float(trade.get('return_on_risk'))
    max_profit = _to_float(trade.get('max_profit_per_share', trade.get('max_profit')))
    max_loss = _to_float(trade.get('max_loss_per_share', trade.get('max_loss')))

    forced = None
    reasons = []
    if ev is not None and ev < 0:
        forced = 'REJECT'
        reasons.append('Negative expected value')
    if kelly is not None and kelly < 0:
        forced = 'REJECT'
        reasons.append('Negative Kelly fraction')
    if ror is not None and ror < 0.10:
        forced = 'REJECT'
        reasons.append('Return on risk below threshold')
    if max_profit and max_loss and max_profit > 0 and (max_loss / max_profit) > 8:
        forced = 'REJECT'
        reasons.append('Risk/reward too extreme')

    if forced:
        out = dict(me) if me else {}
        out['recommendation'] = forced
        try:
            out['confidence'] = max(float(out.get('confidence', 0.5)), 0.85)
        except Exception:
            out['confidence'] = 0.85
        out['risk_level'] = 'High'
        kf = out.get('key_factors') or []
        out['key_factors'] = (reasons + kf)[:6]
        out['_hard_gate_forced'] = True
        return out

    return me


_analyze_trade_with_model_legacy = analyze_trade_with_model


def analyze_trade_with_model(trade: dict, source_filename: str, model_url='http://localhost:1234/v1/chat/completions', retries=2, timeout=30):
    # TODO(architecture): remove this compatibility shim once all imports use common.model_analysis directly.
    from app.models.trade_contract import TradeContract
    from common.model_analysis import analyze_trade

    return analyze_trade(
        TradeContract.from_dict(trade),
        source_filename,
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
