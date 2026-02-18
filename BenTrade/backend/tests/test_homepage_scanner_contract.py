"""Regression tests: homepage picks must use the SAME per-contract metrics as scanner trades.

The scanner path (_normalize_trade) builds a ``computed`` dict with per-contract values.
The homepage path (_build_pick) should surface those same per-contract values in
``key_metrics`` — NOT the legacy per-share flat fields.

These tests confirm:
 1. key_metrics.ev comes from computed.expected_value (per-contract), not ev_per_share.
 2. key_metrics includes max_profit and max_loss (per-contract).
 3. The pick output passes through computed, computed_metrics, metrics_status.
 4. _derive_ror prefers computed.return_on_risk over per-share fallback.
"""

import unittest
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.recommendation_service import RecommendationService


class _StubStrategyService:
    def list_strategy_ids(self):
        return []

    def list_reports(self, _sid):
        return []

    def get_report(self, _sid, _fn):
        return {"trades": []}


class _StubStockAnalysisService:
    async def stock_scanner(self, max_candidates=15):
        return {"candidates": []}


class _StubRegimeService:
    async def get_regime(self):
        return {"regime_label": "NEUTRAL", "regime_score": 50.0, "suggested_playbook": {}}


def _make_service() -> RecommendationService:
    return RecommendationService(
        strategy_service=_StubStrategyService(),
        stock_analysis_service=_StubStockAnalysisService(),
        regime_service=_StubRegimeService(),
    )


# ---- Fake normalized trade as _normalize_trade() would produce ----
def _fake_normalized_trade(**overrides) -> dict:
    """Simulate a trade AFTER _normalize_trade() + apply_metrics_contract()."""
    base = {
        "underlying": "AAPL",
        "symbol": "AAPL",
        "spread_type": "put_credit_spread",
        "strategy": "put_credit_spread",
        "expiration": "2026-03-21",
        "short_strike": 220,
        "long_strike": 215,
        "rank_score": 0.82,
        "composite_score": 0.82,
        "trade_key": "AAPL|2026-03-21|put_credit_spread|220|215|33",
        # --- Per-share fields (legacy, from scanner output) ---
        "ev_per_share": 0.35,
        "max_profit_per_share": 1.25,
        "max_loss_per_share": 3.75,
        "return_on_risk": 0.3333,
        "p_win_used": 0.72,
        # --- Per-contract fields (set by _normalize_trade) ---
        "ev_per_contract": 35.0,
        "expected_value": 35.0,
        "contractsMultiplier": 100,
        # --- computed dict (set by _normalize_trade) ---
        "computed": {
            "max_profit": 125.0,
            "max_loss": 375.0,
            "pop": 0.72,
            "return_on_risk": 0.3333,
            "expected_value": 35.0,
            "kelly_fraction": 0.15,
            "iv_rank": 45.0,
            "short_strike_z": -1.2,
            "bid_ask_pct": 0.05,
            "strike_dist_pct": 0.08,
            "rsi14": 55.0,
            "rv_20d": 0.22,
            "open_interest": 500,
            "volume": 120,
        },
        "details": {
            "break_even": 218.75,
            "dte": 33,
            "expected_move": 12.0,
            "iv_rv_ratio": 1.15,
            "trade_quality_score": 0.82,
            "market_regime": "NEUTRAL",
        },
        "pills": {
            "strategy_label": "Put Credit Spread",
            "dte": 33,
            "pop": 0.72,
        },
        "computed_metrics": {
            "max_profit_per_contract": 125.0,
            "max_loss_per_contract": 375.0,
            "ev_per_contract": 35.0,
            "pop": 0.72,
            "return_on_risk": 0.3333,
        },
        "metrics_status": {
            "max_profit": "ok",
            "max_loss": "ok",
            "expected_value": "ok",
            "pop": "ok",
            "return_on_risk": "ok",
        },
        "iv_rv_ratio": 1.15,
    }
    base.update(overrides)
    return base


def _build_candidate(trade: dict) -> dict:
    """Wrap a normalized trade into a candidate dict like _collect_strategy_candidates does."""
    return {
        "id": trade.get("trade_key", "test"),
        "symbol": str(trade.get("underlying") or "AAPL"),
        "type": "options",
        "strategy": str(trade.get("spread_type") or "put_credit_spread"),
        "rank_score": trade.get("rank_score", 0.82),
        "source": "test:latest.json",
        "raw": trade,
    }


NEUTRAL_REGIME = {"regime_label": "NEUTRAL", "regime_score": 50.0, "suggested_playbook": {}}


class TestBuildPickPerContractContract(unittest.TestCase):
    """key_metrics in _build_pick must reflect per-contract values from computed."""

    def setUp(self):
        self.svc = _make_service()
        self.trade = _fake_normalized_trade()
        self.candidate = _build_candidate(self.trade)
        self.pick = self.svc._build_pick(self.candidate, NEUTRAL_REGIME)

    def test_ev_is_per_contract(self):
        """key_metrics.ev must equal computed.expected_value (35.0), NOT ev_per_share (0.35)."""
        ev = self.pick["key_metrics"]["ev"]
        self.assertIsNotNone(ev)
        self.assertAlmostEqual(ev, 35.0, places=2,
                               msg="EV should be per-contract (35.0), not per-share (0.35)")

    def test_max_profit_present_and_per_contract(self):
        mp = self.pick["key_metrics"]["max_profit"]
        self.assertIsNotNone(mp)
        self.assertAlmostEqual(mp, 125.0, places=2,
                               msg="max_profit should be per-contract (125.0)")

    def test_max_loss_present_and_per_contract(self):
        ml = self.pick["key_metrics"]["max_loss"]
        self.assertIsNotNone(ml)
        self.assertAlmostEqual(ml, 375.0, places=2,
                               msg="max_loss should be per-contract (375.0)")

    def test_pop_matches_computed(self):
        pop = self.pick["key_metrics"]["pop"]
        self.assertAlmostEqual(pop, 0.72, places=4)

    def test_ror_matches_computed(self):
        ror = self.pick["key_metrics"]["ror"]
        self.assertIsNotNone(ror)
        self.assertAlmostEqual(ror, 0.3333, places=3)

    def test_computed_dict_passed_through(self):
        self.assertIn("computed", self.pick)
        self.assertIsInstance(self.pick["computed"], dict)
        self.assertAlmostEqual(self.pick["computed"]["expected_value"], 35.0)
        self.assertAlmostEqual(self.pick["computed"]["max_profit"], 125.0)
        self.assertAlmostEqual(self.pick["computed"]["max_loss"], 375.0)

    def test_computed_metrics_passed_through(self):
        self.assertIn("computed_metrics", self.pick)
        self.assertIsInstance(self.pick["computed_metrics"], dict)
        self.assertAlmostEqual(self.pick["computed_metrics"]["ev_per_contract"], 35.0)

    def test_metrics_status_passed_through(self):
        self.assertIn("metrics_status", self.pick)
        self.assertIsInstance(self.pick["metrics_status"], dict)
        self.assertEqual(self.pick["metrics_status"]["max_profit"], "ok")


class TestBuildPickFallbackPaths(unittest.TestCase):
    """Verify _build_pick still works when computed is empty (e.g. stock scanner)."""

    def setUp(self):
        self.svc = _make_service()

    def test_stock_scanner_candidate_no_computed(self):
        """Stock scanner candidates have no computed; key_metrics should still populate."""
        raw = {
            "symbol": "SPY",
            "composite_score": 0.86,
            "price": 600.12,
            "signals": {"rsi_14": 55.0, "iv_rv_ratio": 1.12},
        }
        candidate = {
            "id": "SPY|stock_scanner",
            "symbol": "SPY",
            "type": "stock",
            "strategy": "stock",
            "rank_score": 86.0,
            "source": "stock_scanner",
            "raw": raw,
        }
        pick = self.svc._build_pick(candidate, NEUTRAL_REGIME)
        self.assertEqual(pick["symbol"], "SPY")
        self.assertEqual(pick["type"], "stock")
        self.assertIsInstance(pick["key_metrics"], dict)
        # Stock picks have no EV/POP/RoR
        self.assertIsInstance(pick.get("computed"), dict)
        self.assertIsInstance(pick.get("computed_metrics"), dict)
        self.assertIsInstance(pick.get("metrics_status"), dict)

    def test_ev_falls_back_to_ev_per_contract_when_no_computed(self):
        """If computed is absent but ev_per_contract exists, use it (per-contract)."""
        trade = {
            "underlying": "MSFT",
            "ev_per_share": 0.50,
            "ev_per_contract": 50.0,
            "p_win_used": 0.65,
            "return_on_risk": 0.25,
            "rank_score": 0.70,
        }
        candidate = _build_candidate(trade)
        candidate["strategy"] = "put_credit_spread"
        pick = self.svc._build_pick(candidate, NEUTRAL_REGIME)
        ev = pick["key_metrics"]["ev"]
        self.assertIsNotNone(ev)
        self.assertAlmostEqual(ev, 50.0, places=2,
                               msg="Should use ev_per_contract (50.0), not ev_per_share (0.50)")

    def test_ev_ultimate_fallback_to_expected_value_flat(self):
        """If computed and ev_per_contract are absent, use expected_value flat field."""
        trade = {
            "underlying": "GOOG",
            "expected_value": 42.0,
            "p_win_used": 0.60,
            "rank_score": 0.65,
        }
        candidate = _build_candidate(trade)
        pick = self.svc._build_pick(candidate, NEUTRAL_REGIME)
        self.assertAlmostEqual(pick["key_metrics"]["ev"], 42.0, places=2)


class TestDeriveRorPrefersComputed(unittest.TestCase):
    """_derive_ror should prefer computed.return_on_risk over per-share fallbacks."""

    def setUp(self):
        self.svc = _make_service()

    def test_uses_computed_return_on_risk(self):
        raw = {
            "return_on_risk": 0.25,
            "max_profit_per_share": 1.0,
            "max_loss_per_share": 4.0,
            "computed": {"return_on_risk": 0.30},
        }
        ror = self.svc._derive_ror(raw)
        self.assertAlmostEqual(ror, 0.30, places=2,
                               msg="Should prefer computed.return_on_risk over flat return_on_risk")

    def test_falls_back_to_flat_return_on_risk(self):
        raw = {"return_on_risk": 0.25}
        ror = self.svc._derive_ror(raw)
        self.assertAlmostEqual(ror, 0.25, places=2)

    def test_computes_from_computed_max_profit_max_loss(self):
        raw = {
            "max_profit_per_share": 1.0,
            "max_loss_per_share": 4.0,
            "computed": {"max_profit": 100.0, "max_loss": 400.0},
        }
        ror = self.svc._derive_ror(raw)
        self.assertAlmostEqual(ror, 0.25, places=2,
                               msg="Should compute from computed.max_profit / computed.max_loss")


class TestPickScannerShapeAlignment(unittest.TestCase):
    """A single trade's homepage pick and scanner output must show identical key metrics."""

    def test_ev_pop_ror_match_between_pick_and_computed(self):
        svc = _make_service()
        trade = _fake_normalized_trade()
        candidate = _build_candidate(trade)
        pick = svc._build_pick(candidate, NEUTRAL_REGIME)

        # Scanner path shows computed.expected_value, computed.pop, computed.return_on_risk
        scanner_ev = trade["computed"]["expected_value"]
        scanner_pop = trade["computed"]["pop"]
        scanner_ror = trade["computed"]["return_on_risk"]
        scanner_mp = trade["computed"]["max_profit"]
        scanner_ml = trade["computed"]["max_loss"]

        # Homepage pick shows key_metrics.ev, key_metrics.pop, key_metrics.ror
        self.assertAlmostEqual(pick["key_metrics"]["ev"], scanner_ev, places=2,
                               msg="Homepage EV must match scanner EV")
        self.assertAlmostEqual(pick["key_metrics"]["pop"], scanner_pop, places=4,
                               msg="Homepage POP must match scanner POP")
        self.assertAlmostEqual(pick["key_metrics"]["ror"], scanner_ror, places=3,
                               msg="Homepage RoR must match scanner RoR")
        self.assertAlmostEqual(pick["key_metrics"]["max_profit"], scanner_mp, places=2,
                               msg="Homepage max_profit must match scanner max_profit")
        self.assertAlmostEqual(pick["key_metrics"]["max_loss"], scanner_ml, places=2,
                               msg="Homepage max_loss must match scanner max_loss")


if __name__ == "__main__":
    unittest.main()
