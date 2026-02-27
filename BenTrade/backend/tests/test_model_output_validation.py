from common.model_analysis import _coerce_stock_model_output
from common.utils import _normalize_eval
from common.trade_analysis_engine import (
    build_analysis_facts,
    compute_trade_metrics,
    validate_model_schema,
)


def test_stock_model_output_filters_past_or_invalid_expiration_trade_ideas() -> None:
    candidate = {
        "recommendation": "BUY",
        "confidence": 0.8,
        "summary": "ok",
        "time_horizon": "1W",
        "trade_ideas": [
            {"action": "buy", "quantity": 1},
            {"strategy": "iron_condor", "expiration_date": "2000-01-01"},
            {"strategy": "covered_call", "expiration_date": "not-a-date"},
            {"strategy": "debit_call_spread", "expiration_date": "2099-12-31"},
        ],
    }

    normalized = _coerce_stock_model_output(candidate)
    assert normalized is not None
    ideas = normalized["trade_ideas"]
    assert len(ideas) == 2
    assert ideas[0].get("action") == "buy"
    assert ideas[1].get("strategy") == "debit_call_spread"


def test_normalize_eval_expanded_schema() -> None:
    """Verify _normalize_eval handles the expanded model output schema."""
    raw = {
        "recommendation": "TAKE",
        "score_0_100": 65,
        "confidence_0_1": 0.78,
        "thesis": "This is a solid trade. The premium compensates for the risk.",
        "model_calculations": {
            "max_profit_per_share": 0.92,
            "max_loss_per_share": 4.08,
            "expected_value_est": 0.42,
            "return_on_risk_est": 0.18,
            "probability_est": 0.82,
            "breakeven_est": 663.08,
            "assumptions": ["Used short-strike delta as probability proxy.", "Conservative fill assumption."],
        },
        "edge_assessment": {
            "premium_vs_risk": "positive",
            "volatility_context": "favorable",
            "liquidity_quality": "high",
            "tail_risk_profile": "low",
        },
        "key_drivers": [
            {"factor": "Strong premium", "impact": "positive", "evidence": "Credit of $0.92 vs $5 width"},
            {"factor": "Distant strike", "impact": "positive", "evidence": "2.4% OTM"},
            {"factor": "Short DTE", "impact": "negative", "evidence": "7 DTE limits adjustment time"},
        ],
        "cross_check_deltas": {
            "return_on_risk": {"model": 0.18, "engine": 0.225, "delta_pct": -0.20, "note": "Model used conservative fill assumption"},
        },
        "risk_review": {
            "primary_risks": ["Gap risk on earnings", "Short DTE limits recovery"],
            "liquidity_risks": ["Slight widening in afternoon sessions"],
            "assignment_risk": "low",
            "volatility_risk": "medium",
            "event_risk": ["FOMC meeting next week"],
        },
        "execution_assessment": {
            "fill_quality": "good",
            "slippage_risk": "low",
            "recommended_limit": 0.91,
            "entry_notes": "Place limit at mid or slightly below.",
        },
        "data_quality_flags": ["IV rank not available"],
        "missing_data": ["realized_vol_20d"],
    }

    result = _normalize_eval(raw)

    # Top-level fields
    assert result["recommendation"] == "ACCEPT"  # legacy mapping
    assert result["model_recommendation"] == "TAKE"
    assert result["score_0_100"] == 65
    assert result["confidence_0_1"] == 0.78
    assert result["thesis"] is not None

    # Model calculations
    mc = result["model_calculations"]
    assert mc["max_profit_per_share"] == 0.92
    assert mc["max_loss_per_share"] == 4.08
    assert mc["expected_value_est"] == 0.42
    assert mc["return_on_risk_est"] == 0.18
    assert mc["probability_est"] == 0.82
    assert mc["breakeven_est"] == 663.08
    assert len(mc["assumptions"]) == 2
    assert "notes" not in mc  # migrated to assumptions

    # Edge assessment
    ea = result["edge_assessment"]
    assert ea["premium_vs_risk"] == "positive"
    assert ea["volatility_context"] == "favorable"

    # Key drivers (structured objects preserved — no 'confidence' field)
    kd = result["key_drivers"]
    assert len(kd) == 3
    assert isinstance(kd[0], dict)
    assert kd[0]["factor"] == "Strong premium"
    assert kd[0]["impact"] == "positive"
    assert "confidence" not in kd[0]  # dropped from schema

    # Legacy key_factors (always strings)
    kf = result["key_factors"]
    assert all(isinstance(f, str) for f in kf)
    assert len(kf) == 3

    # Risk review (expanded)
    rr = result["risk_review"]
    assert len(rr["primary_risks"]) == 2
    assert rr["assignment_risk"] == "low"
    assert rr["volatility_risk"] == "medium"
    assert len(rr["event_risk"]) == 1

    # Execution assessment
    ex = result["execution_assessment"]
    assert ex["fill_quality"] == "good"
    assert ex["slippage_risk"] == "low"
    assert ex["recommended_limit"] == 0.91

    # Cross-check deltas
    ccd = result["cross_check_deltas"]
    assert "return_on_risk" in ccd
    assert ccd["return_on_risk"]["model"] == 0.18
    assert ccd["return_on_risk"]["engine"] == 0.225
    assert ccd["return_on_risk"]["note"] != ""

    # Data quality
    assert len(result["data_quality_flags"]) == 1
    assert len(result["missing_data"]) == 1


def test_normalize_eval_legacy_fallback() -> None:
    """Verify _normalize_eval still works with old-style model output."""
    raw = {
        "recommendation": "ACCEPT",
        "confidence": 0.65,
        "risk_level": "Moderate",
        "key_factors": ["Good premium", "Distant strike"],
        "summary": "Decent setup.",
        "risk_review": {
            "primary_risk": "Gap risk",
            "tail_scenario": "Flash crash",
            "data_quality_flag": "IV rank missing",
        },
        "execution_notes": {
            "fill_probability": "High",
            "spread_concern": None,
        },
        "missing_data": ["realized_vol"],
    }

    result = _normalize_eval(raw)

    # Legacy fields preserved
    assert result["recommendation"] == "ACCEPT"
    assert result["confidence"] == 0.65
    assert result["key_factors"] == ["Good premium", "Distant strike"]

    # Risk review upgraded to new shape
    rr = result["risk_review"]
    assert "primary_risks" in rr
    assert "Gap risk" in rr["primary_risks"]
    assert "Flash crash" in rr["primary_risks"]

    # Model calculations default to None values
    mc = result["model_calculations"]
    assert mc["expected_value_est"] is None
    assert mc["max_profit_per_share"] is None
    assert mc["max_loss_per_share"] is None
    assert mc["assumptions"] == []

    # Edge assessment defaults
    ea = result["edge_assessment"]
    assert ea["premium_vs_risk"] == "neutral"

    # Cross-check deltas empty by default
    assert result["cross_check_deltas"] == {}


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------

def test_build_analysis_facts_basic() -> None:
    """Verify build_analysis_facts normalizes a trade dict properly."""
    trade = {
        "symbol": "SPY",
        "spread_type": "put_credit",
        "short_strike": 665.0,
        "long_strike": 660.0,
        "price": 681.3,
        "expiration": "2025-08-15",
        "dte": 7,
        "net_credit": 0.92,
        "iv": 0.19,
        "bid": 0.90,
        "ask": 0.94,
        "open_interest": 4200,
        "volume": 1800,
        "vix": 17.44,
        "pop": 0.82,
    }

    facts = build_analysis_facts(trade)

    # Underlying
    assert facts["underlying"]["symbol"] == "SPY"
    assert facts["underlying"]["price"] == 681.3

    # Structure
    assert facts["structure"]["short_strike"] == 665.0
    assert facts["structure"]["long_strike"] == 660.0
    assert facts["structure"]["width"] == 5.0  # derived: |665 - 660|
    assert facts["structure"]["dte"] == 7.0

    # Pricing
    assert facts["pricing"]["net_credit"] == 0.92

    # Volatility
    assert facts["volatility"]["iv"] == 0.19

    # Liquidity
    assert facts["liquidity"]["bid"] == 0.90
    assert facts["liquidity"]["ask"] == 0.94

    # Probability
    assert facts["probability"]["pop"] == 0.82

    # Data quality — no missing required fields for this complete trade
    dq = facts["data_quality_flags"]
    assert "short_strike" not in dq
    assert "net_credit" not in dq


def test_build_analysis_facts_missing_fields() -> None:
    """Verify data_quality_flags track missing required fields."""
    trade = {"symbol": "SPY", "spread_type": "put_credit"}

    facts = build_analysis_facts(trade)

    dq = facts["data_quality_flags"]
    assert "underlying_price" in dq
    assert "short_strike" in dq
    assert "long_strike" in dq
    assert "dte" in dq
    assert "net_credit" in dq
    assert "width" in dq
    assert "iv" in dq


def test_compute_trade_metrics_put_credit() -> None:
    """Verify deterministic engine calculations for a put credit spread."""
    facts = build_analysis_facts({
        "symbol": "SPY",
        "spread_type": "put_credit",
        "short_strike": 665.0,
        "long_strike": 660.0,
        "price": 681.3,
        "dte": 7,
        "net_credit": 0.92,
        "pop": 0.82,
    })

    metrics = compute_trade_metrics(facts)

    # max_profit = net_credit = 0.92
    assert metrics["max_profit_per_share"] == 0.92

    # max_loss = width - net_credit = 5.0 - 0.92 = 4.08
    assert abs(metrics["max_loss_per_share"] - 4.08) < 0.001

    # breakeven (put credit) = short_strike - net_credit = 665 - 0.92 = 664.08
    assert abs(metrics["breakeven"] - 664.08) < 0.001

    # return_on_risk = 0.92 / 4.08 ≈ 0.2255
    assert abs(metrics["return_on_risk"] - 0.2255) < 0.01

    # pop_proxy = 0.82 (from explicit POP)
    assert metrics["pop_proxy"] == 0.82

    # ev_per_share = 0.82 * 0.92 - 0.18 * 4.08 ≈ 0.0200
    assert metrics["ev_per_share"] is not None
    assert abs(metrics["ev_per_share"] - 0.020) < 0.02

    # kelly_fraction should be positive for this profitable trade
    assert metrics["kelly_fraction"] is not None
    assert metrics["kelly_fraction"] > 0


def test_compute_trade_metrics_call_credit() -> None:
    """Verify breakeven direction for call credit spreads."""
    facts = build_analysis_facts({
        "symbol": "SPY",
        "spread_type": "call_credit",
        "short_strike": 700.0,
        "long_strike": 705.0,
        "price": 681.3,
        "dte": 7,
        "net_credit": 0.82,
    })

    metrics = compute_trade_metrics(facts)

    # breakeven (call credit) = short_strike + net_credit = 700 + 0.82 = 700.82
    assert abs(metrics["breakeven"] - 700.82) < 0.001


def test_validate_model_schema_valid() -> None:
    """Verify valid model output passes schema validation."""
    valid_eval = {
        "recommendation": "TAKE",
        "score_0_100": 65,
        "confidence_0_1": 0.78,
        "thesis": "Solid trade. Premium compensates for risk.",
        "model_calculations": {
            "max_profit_per_share": 0.92,
            "max_loss_per_share": 4.08,
            "expected_value_est": 0.42,
            "return_on_risk_est": 0.18,
            "probability_est": 0.82,
            "breakeven_est": 664.08,
            "assumptions": ["Used delta as POP proxy"],
        },
        "key_drivers": [
            {"factor": "Premium", "impact": "positive", "evidence": "0.92/5"},
            {"factor": "Distance", "impact": "positive", "evidence": "2.4% OTM"},
            {"factor": "DTE", "impact": "negative", "evidence": "7 DTE"},
        ],
        "risk_review": {
            "primary_risks": ["Gap risk", "Short DTE"],
            "assignment_risk": "low",
        },
    }

    violations = validate_model_schema(valid_eval)
    assert violations == []


def test_validate_model_schema_violations() -> None:
    """Verify schema validation catches missing/invalid fields."""
    bad_eval = {
        "recommendation": "MAYBE",  # invalid value
        "score_0_100": 150,          # out of range
        "confidence_0_1": 1.5,       # out of range
        # missing thesis, model_calculations, key_drivers, risk_review
    }

    violations = validate_model_schema(bad_eval)
    assert any("invalid_recommendation" in v for v in violations)
    assert any("score_out_of_range" in v for v in violations)
    assert any("confidence_out_of_range" in v for v in violations)
    assert any("missing_required_field:thesis" in v for v in violations)
    assert any("missing_required_field:model_calculations" in v for v in violations)
    assert any("missing_required_field:key_drivers" in v for v in violations)


def test_validate_model_schema_insufficient_key_drivers() -> None:
    """Verify validation flags fewer than 3 key_drivers."""
    eval_with_few_drivers = {
        "recommendation": "TAKE",
        "score_0_100": 50,
        "confidence_0_1": 0.5,
        "thesis": "Acceptable trade. Moderate premium.",
        "model_calculations": {
            "expected_value_est": 0.1,
            "return_on_risk_est": 0.15,
            "probability_est": 0.8,
        },
        "key_drivers": [
            {"factor": "Premium", "impact": "positive"},
        ],
        "risk_review": {"primary_risks": ["Gap risk"]},
    }

    violations = validate_model_schema(eval_with_few_drivers)
    assert any("key_drivers_count:1" in v for v in violations)


def test_normalize_eval_legacy_notes_to_assumptions() -> None:
    """Verify legacy 'notes' in model_calculations migrates to 'assumptions' list."""
    raw = {
        "recommendation": "TAKE",
        "score_0_100": 50,
        "confidence_0_1": 0.6,
        "thesis": "OK trade. Decent premium.",
        "model_calculations": {
            "expected_value_est": 0.3,
            "return_on_risk_est": 0.15,
            "probability_est": 0.78,
            "breakeven_est": 664.0,
            "notes": "Used delta as POP proxy",
        },
        "key_drivers": [
            {"factor": "A", "impact": "positive", "evidence": "x"},
            {"factor": "B", "impact": "negative", "evidence": "y"},
            {"factor": "C", "impact": "neutral", "evidence": "z"},
        ],
    }

    result = _normalize_eval(raw)
    mc = result["model_calculations"]

    # 'notes' should be migrated to 'assumptions' and removed
    assert "notes" not in mc
    assert mc["assumptions"] == ["Used delta as POP proxy"]