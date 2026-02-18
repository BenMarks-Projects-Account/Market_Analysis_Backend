from common.model_analysis import _coerce_stock_model_output


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
