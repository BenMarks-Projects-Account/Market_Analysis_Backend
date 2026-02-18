from app.services.decision_service import DecisionService


def test_trade_key_stability_ignores_underlying_alias_field_order():
    trade_a = {
        "underlying": "spy",
        "expiration": "2026-02-20",
        "spread_type": "put_credit_spread",
        "short_strike": 580,
        "long_strike": 575,
        "dte": 5,
    }
    trade_b = {
        "underlying_symbol": "SPY",
        "expiration": "2026-02-20",
        "spread_type": "put_credit_spread",
        "short_strike": 580.0,
        "long_strike": 575.0,
        "dte": 5,
    }

    key_a = DecisionService.build_trade_key(trade_a)
    key_b = DecisionService.build_trade_key(trade_b)

    assert key_a == key_b


def test_append_and_read_decisions(tmp_path):
    svc = DecisionService(results_dir=tmp_path)

    decision = svc.append_reject(
        report_file="analysis_20260215_010101.json",
        trade_key="SPY|2026-02-20|put_credit_spread|580|575|5",
        reason="manual_reject",
    )

    assert decision["type"] == "reject"
    entries = svc.list_decisions("analysis_20260215_010101.json")
    assert len(entries) == 1
    assert entries[0]["trade_key"] == "SPY|2026-02-20|put_credit_spread|580|575|5"


def test_append_reject_dedupes_when_alias_differs(tmp_path):
    svc = DecisionService(results_dir=tmp_path)

    svc.append_reject(
        report_file="analysis_20260215_010102.json",
        trade_key="SPY|2026-02-20|credit_put_spread|580|575|5",
        reason="manual_reject",
    )
    svc.append_reject(
        report_file="analysis_20260215_010102.json",
        trade_key="SPY|2026-02-20|put_credit_spread|580|575|5",
        reason="manual_reject",
    )

    entries = svc.list_decisions("analysis_20260215_010102.json")
    assert len(entries) == 1
    assert entries[0]["trade_key"] == "SPY|2026-02-20|put_credit_spread|580|575|5"
