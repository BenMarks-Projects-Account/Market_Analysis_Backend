"""Quick smoke tests for json_repair pipeline + fallback builder."""
from common.json_repair import extract_and_repair_json, REPAIR_METRICS
from common.model_analysis import _build_fallback_stock_analysis


def test_direct_parse():
    obj, m = extract_and_repair_json('{"recommendation": "BUY", "score": 75}')
    assert obj == {"recommendation": "BUY", "score": 75}
    assert m == "direct"


def test_strip_fences():
    raw = '```json\n{"recommendation": "BUY"}\n```'
    obj, m = extract_and_repair_json(raw)
    assert obj == {"recommendation": "BUY"}
    assert m == "strip_fences"


def test_extract_block():
    raw = 'Here is your analysis:\n{"recommendation": "PASS"}\nHope this helps!'
    obj, m = extract_and_repair_json(raw)
    assert obj == {"recommendation": "PASS"}
    assert m == "extract_block"


def test_trailing_comma():
    raw = '{"a": 1, "b": 2,}'
    obj, m = extract_and_repair_json(raw)
    assert obj == {"a": 1, "b": 2}
    assert m == "repaired"


def test_python_literals():
    raw = '{"ok": True, "val": None, "flag": False}'
    obj, m = extract_and_repair_json(raw)
    assert obj == {"ok": True, "val": None, "flag": False}
    assert m in ("repaired", "extract_block")


def test_smart_quotes():
    raw = '{\u201crecommendation\u201d: \u201cBUY\u201d}'
    obj, m = extract_and_repair_json(raw)
    assert obj == {"recommendation": "BUY"}
    assert m == "repaired"


def test_total_failure():
    obj, m = extract_and_repair_json("no json here at all")
    assert obj is None
    assert m is None


def test_empty():
    obj, m = extract_and_repair_json("")
    assert obj is None
    assert m is None


def test_fallback_builder():
    fb = _build_fallback_stock_analysis(
        {"symbol": "SPY", "composite_score": 72},
        "stock_pullback_swing",
        "test fail",
    )
    assert fb["recommendation"] == "PASS"
    assert fb["score"] == 72
    assert fb["confidence"] == 20
    assert fb["_fallback"] is True
    assert "MODEL_PARSE_FAILED" in fb["data_quality"]["warnings"]
    assert fb["timestamp"]  # non-empty


def test_fallback_no_engine_score():
    fb = _build_fallback_stock_analysis(
        {"symbol": "QQQ"},
        "stock_momentum_breakout",
        "missing score",
    )
    assert fb["score"] == 50  # default when no composite_score


if __name__ == "__main__":
    test_direct_parse(); print("PASS: direct_parse")
    test_strip_fences(); print("PASS: strip_fences")
    test_extract_block(); print("PASS: extract_block")
    test_trailing_comma(); print("PASS: trailing_comma")
    test_python_literals(); print("PASS: python_literals")
    test_smart_quotes(); print("PASS: smart_quotes")
    test_total_failure(); print("PASS: total_failure")
    test_empty(); print("PASS: empty")
    test_fallback_builder(); print("PASS: fallback_builder")
    test_fallback_no_engine_score(); print("PASS: fallback_no_engine_score")
    print(f"\nAll 10 tests passed. Repair metrics: {REPAIR_METRICS}")
