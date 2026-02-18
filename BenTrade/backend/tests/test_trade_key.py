from app.utils.trade_key import (
    canonicalize_strategy_id,
    canonicalize_trade_key,
    is_canonical_trade_key,
    normalize_strike,
    trade_key,
)


def test_normalize_strike_stability():
    assert normalize_strike(450.0) == "450"
    assert normalize_strike("450.5000") == "450.5"
    assert normalize_strike(None) == "NA"


def test_trade_key_stability_and_defaults():
    key_a = trade_key("spy", "2026-03-20", "credit_put_spread", 450.0, 445.50, 7)
    key_b = trade_key("SPY", "2026-03-20", "credit_put_spread", "450", "445.5", "7")

    assert key_a == key_b
    assert key_a == "SPY|2026-03-20|put_credit_spread|450|445.5|7"

    key_default = trade_key("QQQ", None, "single", None, None, None)
    assert key_default == "QQQ|NA|single|NA|NA|NA"


def test_canonicalize_trade_key_from_legacy_alias() -> None:
    original = "qqq|2026-02-23|credit_put_spread|565|560|7"
    assert canonicalize_trade_key(original) == "QQQ|2026-02-23|put_credit_spread|565|560|7"


def test_canonicalize_strategy_id_alias_metadata() -> None:
    canonical, mapped, provided = canonicalize_strategy_id("credit_call_spread")
    assert canonical == "call_credit_spread"
    assert mapped is True
    assert provided == "credit_call_spread"


def test_is_canonical_trade_key_detects_alias_noncanonical() -> None:
    legacy = "SPY|2026-03-20|credit_put_spread|450|445|7"
    canonical = "SPY|2026-03-20|put_credit_spread|450|445|7"
    assert is_canonical_trade_key(legacy) is False
    assert is_canonical_trade_key(canonical) is True
