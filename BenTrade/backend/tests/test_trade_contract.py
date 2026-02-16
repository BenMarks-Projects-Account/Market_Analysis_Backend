from app.models.trade_contract import TradeContract


def test_trade_contract_roundtrip_dict_preserves_keys():
    payload = {
        "spread_type": "put_credit",
        "underlying": "SPY",
        "short_strike": 580.0,
        "long_strike": 575.0,
        "dte": 7,
        "net_credit": 1.12,
        "width": 5.0,
        "return_on_risk": 0.288,
        "trade_quality_score": 0.74,
        "composite_score": 0.68,
        "rank_score": 0.72,
        "rank_in_report": 2,
        "model_evaluation": {"recommendation": "ACCEPT"},
        "extra_field": "kept",
    }

    contract = TradeContract.from_dict(payload)
    out = contract.to_dict()

    for key in payload:
        assert key in out
    assert out["underlying"] == "SPY"
    assert out["extra_field"] == "kept"
