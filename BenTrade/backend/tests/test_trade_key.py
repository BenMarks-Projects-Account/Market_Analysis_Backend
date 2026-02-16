from app.utils.trade_key import normalize_strike, trade_key


def test_normalize_strike_stability():
    assert normalize_strike(450.0) == "450"
    assert normalize_strike("450.5000") == "450.5"
    assert normalize_strike(None) == "NA"


def test_trade_key_stability_and_defaults():
    key_a = trade_key("spy", "2026-03-20", "credit_put_spread", 450.0, 445.50, 7)
    key_b = trade_key("SPY", "2026-03-20", "credit_put_spread", "450", "445.5", "7")

    assert key_a == key_b
    assert key_a == "SPY|2026-03-20|credit_put_spread|450|445.5|7"

    key_default = trade_key("QQQ", None, "single", None, None, None)
    assert key_default == "QQQ|NA|single|NA|NA|NA"
