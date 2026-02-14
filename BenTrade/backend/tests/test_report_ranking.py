import unittest

from app.services.ranking import compute_rank_score, sort_trades_by_rank


class ReportRankingTests(unittest.TestCase):
    def test_rank_score_prefers_edge_efficiency_and_liquidity(self):
        high = {
            "ev_to_risk": 0.045,
            "return_on_risk": 0.32,
            "p_win_used": 0.82,
            "bid_ask_spread_pct": 0.05,
            "open_interest": 4200,
            "volume": 3200,
            "trade_quality_score": 0.72,
        }
        low = {
            "ev_to_risk": 0.005,
            "return_on_risk": 0.12,
            "p_win_used": 0.68,
            "bid_ask_spread_pct": 0.28,
            "open_interest": 60,
            "volume": 40,
            "trade_quality_score": 0.48,
        }

        self.assertGreater(compute_rank_score(high), compute_rank_score(low))

    def test_tie_breakers_are_deterministic(self):
        a = {
            "underlying": "SPY",
            "short_strike": 590,
            "long_strike": 585,
            "ev_to_risk": 0.030,
            "return_on_risk": 0.20,
            "p_win_used": 0.80,
            "bid_ask_spread_pct": 0.08,
            "open_interest": 2400,
            "volume": 900,
            "trade_quality_score": 0.65,
        }
        b = {
            "underlying": "SPY",
            "short_strike": 588,
            "long_strike": 583,
            "ev_to_risk": 0.030,
            "return_on_risk": 0.20,
            "p_win_used": 0.80,
            "bid_ask_spread_pct": 0.08,
            "open_interest": 2000,
            "volume": 900,
            "trade_quality_score": 0.65,
        }

        ordered = sort_trades_by_rank([b, a])
        self.assertEqual(ordered[0]["short_strike"], 590)

    def test_sort_assigns_descending_rank_scores(self):
        trades = [
            {
                "underlying": "QQQ",
                "short_strike": 500,
                "long_strike": 495,
                "ev_to_risk": 0.010,
                "return_on_risk": 0.14,
                "p_win_used": 0.73,
                "bid_ask_spread_pct": 0.12,
                "open_interest": 700,
                "volume": 300,
                "trade_quality_score": 0.58,
            },
            {
                "underlying": "QQQ",
                "short_strike": 498,
                "long_strike": 493,
                "ev_to_risk": 0.038,
                "return_on_risk": 0.29,
                "p_win_used": 0.81,
                "bid_ask_spread_pct": 0.06,
                "open_interest": 3000,
                "volume": 1900,
                "trade_quality_score": 0.70,
            },
            {
                "underlying": "QQQ",
                "short_strike": 496,
                "long_strike": 491,
                "ev_to_risk": 0.024,
                "return_on_risk": 0.20,
                "p_win_used": 0.77,
                "bid_ask_spread_pct": 0.09,
                "open_interest": 1800,
                "volume": 900,
                "trade_quality_score": 0.63,
            },
        ]

        ordered = sort_trades_by_rank(trades)
        self.assertEqual(len(ordered), 3)
        self.assertGreaterEqual(ordered[0]["rank_score"], ordered[1]["rank_score"])
        self.assertGreaterEqual(ordered[1]["rank_score"], ordered[2]["rank_score"])


if __name__ == "__main__":
    unittest.main()
