import unittest

from app.services.report_service import INDEX_RULES, evaluate_trade


class ReportEvaluationTests(unittest.TestCase):
    def test_evaluate_trade_reports_missing_fields(self):
        trade = {
            "underlying": "SPY",
            "return_on_risk": None,
            "short_delta_abs": None,
            "width": None,
            "trade_quality_score": None,
            "bid_ask_spread_pct": None,
            "open_interest": None,
            "volume": None,
        }
        ok, reasons = evaluate_trade(trade, INDEX_RULES["SPY"], validation_mode=False)
        self.assertFalse(ok)
        expected = {
            "missing_pop",
            "missing_ror",
            "missing_delta",
            "missing_width",
            "missing_iv_rv",
            "missing_trade_quality_score",
            "missing_bid_ask_spread_pct",
            "missing_open_interest",
            "missing_volume",
        }
        self.assertTrue(expected.issubset(set(reasons)))

    def test_validation_mode_relaxes_missing_iv_rv(self):
        trade = {
            "underlying": "SPY",
            "p_win_used": 0.74,
            "return_on_risk": 0.18,
            "short_delta_abs": 0.18,
            "width": 5.0,
            "iv_rv_ratio": None,
            "trade_quality_score": 0.62,
            "bid_ask_spread_pct": 0.11,
            "open_interest": 150,
            "volume": 40,
            "ev_per_share": 0.05,
            "max_profit_per_share": 1.0,
            "max_loss_per_share": 4.0,
            "kelly_fraction": -0.1,
            "bid": 1.20,
            "ask": 1.30,
            "spread_bid": 0.45,
            "spread_ask": 0.55,
        }

        strict_ok, strict_reasons = evaluate_trade(trade, INDEX_RULES["SPY"], validation_mode=False)
        self.assertFalse(strict_ok)
        self.assertIn("missing_iv_rv", strict_reasons)

        relaxed_ok, relaxed_reasons = evaluate_trade(trade, INDEX_RULES["SPY"], validation_mode=True)
        self.assertTrue(relaxed_ok)
        self.assertNotIn("missing_iv_rv", relaxed_reasons)


if __name__ == "__main__":
    unittest.main()
