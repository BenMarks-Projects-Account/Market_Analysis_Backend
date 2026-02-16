from app.api.routes_decisions import RejectDecisionRequest


def test_reject_decision_request_shape():
    req = RejectDecisionRequest(
        report_file="analysis_20260215_010101.json",
        trade_key="SPY|2026-02-20|put_credit|580|575|5",
        reason="manual_reject",
    )

    assert req.report_file.endswith('.json')
    assert req.trade_key.startswith('SPY|')
