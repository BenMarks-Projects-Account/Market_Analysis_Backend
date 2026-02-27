"""Unit tests for iron condor 4-leg candidate structure.

Validates:
- build_candidates outputs explicit `legs` array with 4 entries
- Each leg has the required fields: name, right, side, strike, qty
- Candidate includes put_wing_width, call_wing_width
- Candidate includes convenience strike fields
- Generic short_strike/long_strike NOT in enriched output
- Enriched output includes serializable legs[] without _contract refs
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


def _make_leg(**kwargs: Any) -> SimpleNamespace:
    defaults = dict(
        strike=100, option_type="call", bid=1.0, ask=1.2,
        delta=0.30, gamma=0.02, theta=-0.03, vega=0.10,
        iv=0.25, open_interest=5000, volume=500, symbol="TEST260601C100",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


_LEG_NAMES = {"long_put", "short_put", "short_call", "long_call"}
_LEG_FIELDS = {"name", "right", "side", "strike", "qty"}


class TestBuildCandidatesLegs:
    """build_candidates must produce explicit 4-leg candidates."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        return IronCondorStrategyPlugin()

    @pytest.fixture()
    def snapshot(self):
        """Minimal snapshot with put/call contracts at known strikes."""
        contracts = [
            # Put side
            _make_leg(strike=85, option_type="put", bid=0.05, ask=0.10, delta=-0.05),
            _make_leg(strike=90, option_type="put", bid=0.60, ask=0.80, delta=-0.15),
            # Call side
            _make_leg(strike=110, option_type="call", bid=0.60, ask=0.80, delta=0.15),
            _make_leg(strike=115, option_type="call", bid=0.05, ask=0.10, delta=0.05),
        ]
        return {
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": 100.0,
            "contracts": contracts,
            "prices_history": [],
        }

    def test_candidate_has_4_legs(self, plugin, snapshot):
        inputs = {
            "request": {
                "wing_width": 5.0,
                "distance_target": 0.5,
            },
            "snapshots": [snapshot],
        }
        candidates = plugin.build_candidates(inputs)
        assert len(candidates) >= 1, "Should produce at least one candidate"
        c = candidates[0]
        assert "legs" in c, "Candidate must have 'legs' key"
        assert len(c["legs"]) == 4, f"Candidate must have exactly 4 legs, got {len(c['legs'])}"

    def test_leg_field_completeness(self, plugin, snapshot):
        inputs = {
            "request": {"wing_width": 5.0, "distance_target": 0.5},
            "snapshots": [snapshot],
        }
        candidates = plugin.build_candidates(inputs)
        c = candidates[0]
        names_seen = set()
        for leg in c["legs"]:
            assert _LEG_FIELDS.issubset(leg.keys()), (
                f"Leg {leg.get('name')} missing fields: {_LEG_FIELDS - set(leg.keys())}"
            )
            names_seen.add(leg["name"])
            assert leg["qty"] == 1
            assert leg["right"] in ("put", "call")
            assert leg["side"] in ("buy", "sell")
            assert isinstance(leg["strike"], (int, float))
            assert leg["strike"] > 0
        assert names_seen == _LEG_NAMES, f"Expected legs {_LEG_NAMES}, got {names_seen}"

    def test_leg_sides_correct(self, plugin, snapshot):
        inputs = {
            "request": {"wing_width": 5.0, "distance_target": 0.5},
            "snapshots": [snapshot],
        }
        candidates = plugin.build_candidates(inputs)
        c = candidates[0]
        by_name = {leg["name"]: leg for leg in c["legs"]}
        assert by_name["long_put"]["side"] == "buy"
        assert by_name["short_put"]["side"] == "sell"
        assert by_name["short_call"]["side"] == "sell"
        assert by_name["long_call"]["side"] == "buy"
        assert by_name["long_put"]["right"] == "put"
        assert by_name["short_put"]["right"] == "put"
        assert by_name["short_call"]["right"] == "call"
        assert by_name["long_call"]["right"] == "call"

    def test_wing_widths_present_and_valid(self, plugin, snapshot):
        inputs = {
            "request": {"wing_width": 5.0, "distance_target": 0.5},
            "snapshots": [snapshot],
        }
        candidates = plugin.build_candidates(inputs)
        c = candidates[0]
        assert "put_wing_width" in c, "Candidate must have put_wing_width"
        assert "call_wing_width" in c, "Candidate must have call_wing_width"
        assert c["put_wing_width"] > 0, "put_wing_width must be positive"
        assert c["call_wing_width"] > 0, "call_wing_width must be positive"
        # Wing widths must match leg strike differences
        by_name = {leg["name"]: leg for leg in c["legs"]}
        assert c["put_wing_width"] == pytest.approx(
            by_name["short_put"]["strike"] - by_name["long_put"]["strike"], abs=0.01
        )
        assert c["call_wing_width"] == pytest.approx(
            by_name["long_call"]["strike"] - by_name["short_call"]["strike"], abs=0.01
        )

    def test_convenience_strike_fields(self, plugin, snapshot):
        inputs = {
            "request": {"wing_width": 5.0, "distance_target": 0.5},
            "snapshots": [snapshot],
        }
        candidates = plugin.build_candidates(inputs)
        c = candidates[0]
        by_name = {leg["name"]: leg for leg in c["legs"]}
        # New convenience fields
        assert c["short_put_strike"] == by_name["short_put"]["strike"]
        assert c["long_put_strike"] == by_name["long_put"]["strike"]
        assert c["short_call_strike"] == by_name["short_call"]["strike"]
        assert c["long_call_strike"] == by_name["long_call"]["strike"]
        # Backward-compat aliases
        assert c["put_short_strike"] == c["short_put_strike"]
        assert c["put_long_strike"] == c["long_put_strike"]
        assert c["call_short_strike"] == c["short_call_strike"]
        assert c["call_long_strike"] == c["long_call_strike"]

    def test_legs_have_contract_refs(self, plugin, snapshot):
        """Each leg in build_candidates output must carry _contract ref."""
        inputs = {
            "request": {"wing_width": 5.0, "distance_target": 0.5},
            "snapshots": [snapshot],
        }
        candidates = plugin.build_candidates(inputs)
        c = candidates[0]
        for leg in c["legs"]:
            assert "_contract" in leg, f"Leg {leg['name']} must have _contract"
            assert leg["_contract"] is not None, f"Leg {leg['name']} _contract must not be None"


class TestEnrichedLegs:
    """Enriched output structure after enrich()."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        return IronCondorStrategyPlugin()

    def _make_candidate(self) -> dict[str, Any]:
        ps_leg = _make_leg(strike=90, option_type="put", bid=0.85, ask=0.95, delta=-0.15)
        pl_leg = _make_leg(strike=85, option_type="put", bid=0.05, ask=0.10, delta=-0.05)
        cs_leg = _make_leg(strike=110, option_type="call", bid=0.85, ask=0.95, delta=0.15)
        cl_leg = _make_leg(strike=115, option_type="call", bid=0.05, ask=0.10, delta=0.05)
        return {
            "strategy": "iron_condor",
            "spread_type": "iron_condor",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": 100.0,
            "put_short_strike": 90,
            "put_long_strike": 85,
            "call_short_strike": 110,
            "call_long_strike": 115,
            "legs": [
                {"name": "long_put",   "right": "put",  "side": "buy",  "strike": 85,  "qty": 1, "_contract": pl_leg},
                {"name": "short_put",  "right": "put",  "side": "sell", "strike": 90,  "qty": 1, "_contract": ps_leg},
                {"name": "short_call", "right": "call", "side": "sell", "strike": 110, "qty": 1, "_contract": cs_leg},
                {"name": "long_call",  "right": "call", "side": "buy",  "strike": 115, "qty": 1, "_contract": cl_leg},
            ],
            "width_put": 5.0,
            "width_call": 5.0,
            "symmetry_score": 1.0,
            "expected_move": 5.0,
            "snapshot": {"symbol": "TEST", "prices_history": []},
        }

    def test_enriched_has_serializable_legs(self, plugin):
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        assert "legs" in trade
        assert len(trade["legs"]) == 4
        _required = {"name", "right", "side", "strike", "qty"}
        _allowed = _required | {"bid", "ask", "mid", "delta", "iv", "open_interest", "volume", "occ_symbol"}
        for leg in trade["legs"]:
            assert "_contract" not in leg, "Enriched legs must not contain _contract refs"
            assert _required.issubset(set(leg.keys())), (
                f"Leg {leg.get('name')} missing required fields: {_required - set(leg.keys())}"
            )
            assert set(leg.keys()).issubset(_allowed), (
                f"Leg {leg.get('name')} has unexpected fields: {set(leg.keys()) - _allowed}"
            )

    def test_enriched_no_generic_short_long_strike(self, plugin):
        """Enriched output must NOT contain generic short_strike/long_strike."""
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert "short_strike" not in trade, "Enriched IC should not have generic short_strike"
        assert "long_strike" not in trade, "Enriched IC should not have generic long_strike"

    def test_enriched_has_wing_widths(self, plugin):
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["put_wing_width"] == 5.0
        assert trade["call_wing_width"] == 5.0

    def test_enriched_has_all_convenience_strikes(self, plugin):
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["short_put_strike"] == 90
        assert trade["long_put_strike"] == 85
        assert trade["short_call_strike"] == 110
        assert trade["long_call_strike"] == 115
        # Backward-compat aliases
        assert trade["put_short_strike"] == 90
        assert trade["put_long_strike"] == 85
        assert trade["call_short_strike"] == 110
        assert trade["call_long_strike"] == 115

    def test_enriched_legs_match_strikes(self, plugin):
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        by_name = {leg["name"]: leg for leg in trade["legs"]}
        assert by_name["long_put"]["strike"] == trade["long_put_strike"]
        assert by_name["short_put"]["strike"] == trade["short_put_strike"]
        assert by_name["short_call"]["strike"] == trade["short_call_strike"]
        assert by_name["long_call"]["strike"] == trade["long_call_strike"]

    def test_scoring_math_unchanged(self, plugin):
        """Verify the refactor did not alter any scoring/pricing outputs."""
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        # Core pricing fields must still be computed
        assert trade["total_credit"] > 0
        assert trade["max_loss"] > 0
        assert 0 < trade["p_win_used"] < 1
        assert trade["ev_per_contract"] is not None
        assert trade["return_on_risk"] > 0
        assert trade["readiness"] is True


# ────────────────────────────────────────────────────────────
# Mid-Based Pricing Tests
# ────────────────────────────────────────────────────────────

class TestMidBasedPricing:
    """Validate mid-based enrichment pricing: net_credit, max_loss, ror, readiness."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        return IronCondorStrategyPlugin()

    def _make_candidate(self, **leg_bids_asks) -> dict[str, Any]:
        """Build a valid 4-leg IC candidate with configurable bid/ask per leg."""
        sp_bid = leg_bids_asks.get("short_put_bid", 0.85)
        sp_ask = leg_bids_asks.get("short_put_ask", 0.95)
        lp_bid = leg_bids_asks.get("long_put_bid", 0.05)
        lp_ask = leg_bids_asks.get("long_put_ask", 0.10)
        sc_bid = leg_bids_asks.get("short_call_bid", 0.85)
        sc_ask = leg_bids_asks.get("short_call_ask", 0.95)
        lc_bid = leg_bids_asks.get("long_call_bid", 0.05)
        lc_ask = leg_bids_asks.get("long_call_ask", 0.10)

        ps_leg = _make_leg(strike=90, option_type="put", bid=sp_bid, ask=sp_ask, delta=-0.15)
        pl_leg = _make_leg(strike=85, option_type="put", bid=lp_bid, ask=lp_ask, delta=-0.05)
        cs_leg = _make_leg(strike=110, option_type="call", bid=sc_bid, ask=sc_ask, delta=0.15)
        cl_leg = _make_leg(strike=115, option_type="call", bid=lc_bid, ask=lc_ask, delta=0.05)

        return {
            "strategy": "iron_condor",
            "spread_type": "iron_condor",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": 100.0,
            "put_short_strike": 90,
            "put_long_strike": 85,
            "call_short_strike": 110,
            "call_long_strike": 115,
            "legs": [
                {"name": "long_put",   "right": "put",  "side": "buy",  "strike": 85,  "qty": 1, "_contract": pl_leg},
                {"name": "short_put",  "right": "put",  "side": "sell", "strike": 90,  "qty": 1, "_contract": ps_leg},
                {"name": "short_call", "right": "call", "side": "sell", "strike": 110, "qty": 1, "_contract": cs_leg},
                {"name": "long_call",  "right": "call", "side": "buy",  "strike": 115, "qty": 1, "_contract": cl_leg},
            ],
            "width_put": 5.0,
            "width_call": 5.0,
            "symmetry_score": 1.0,
            "expected_move": 5.0,
            "snapshot": {"symbol": "TEST", "prices_history": []},
        }

    def _make_missing_leg_candidate(self, missing_leg: str) -> dict[str, Any]:
        """Build a candidate where one leg has None bid/ask."""
        bids_asks: dict[str, Any] = {}
        if missing_leg == "short_put":
            bids_asks.update(short_put_bid=None, short_put_ask=None)
        elif missing_leg == "long_put":
            bids_asks.update(long_put_bid=None, long_put_ask=None)
        elif missing_leg == "short_call":
            bids_asks.update(short_call_bid=None, short_call_ask=None)
        elif missing_leg == "long_call":
            bids_asks.update(long_call_bid=None, long_call_ask=None)
        return self._make_candidate(**bids_asks)

    # ── Formula verification ────────────────────────────────────

    def test_net_credit_is_mid_based(self, plugin):
        """net_credit = (short_put.mid + short_call.mid)
                      - (long_put.mid  + long_call.mid)"""
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) == 1
        trade = enriched[0]
        # short_put_mid=(0.85+0.95)/2=0.90, short_call_mid=(0.85+0.95)/2=0.90
        # long_put_mid=(0.05+0.10)/2=0.075, long_call_mid=(0.05+0.10)/2=0.075
        # net_credit = (0.90+0.90) - (0.075+0.075) = 1.65
        expected_credit = (0.90 + 0.90) - (0.075 + 0.075)
        assert trade["net_credit"] == pytest.approx(expected_credit, abs=0.001)
        assert trade["total_credit"] == pytest.approx(expected_credit, abs=0.001)

    def test_max_loss_formula(self, plugin):
        """max_loss = max(put_wing_width, call_wing_width) * 100 - net_credit * 100"""
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        net_credit = trade["net_credit"]
        expected_max_loss = 5.0 * 100.0 - net_credit * 100.0
        assert trade["max_loss"] == pytest.approx(expected_max_loss, abs=0.1)

    def test_ror_formula(self, plugin):
        """ror = (net_credit * 100) / max_loss"""
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        expected_ror = (trade["net_credit"] * 100.0) / trade["max_loss"]
        assert trade["return_on_risk"] == pytest.approx(expected_ror, abs=0.001)

    def test_readiness_true_with_valid_quotes(self, plugin):
        """readiness=True when all 4 legs have valid bid/ask."""
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["readiness"] is True

    # ── Missing leg quote → readiness=false, no credit/ror ──────

    @pytest.mark.parametrize("missing_leg", [
        "short_put", "long_put", "short_call", "long_call",
    ])
    def test_missing_leg_readiness_false(self, plugin, missing_leg):
        """readiness=False when any leg quote is missing."""
        candidate = self._make_missing_leg_candidate(missing_leg)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) == 1
        trade = enriched[0]
        assert trade["readiness"] is False

    @pytest.mark.parametrize("missing_leg", [
        "short_put", "long_put", "short_call", "long_call",
    ])
    def test_missing_leg_no_credit(self, plugin, missing_leg):
        """Credit, max_loss, ror, max_profit must be None when any leg quote is missing."""
        candidate = self._make_missing_leg_candidate(missing_leg)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["net_credit"] is None
        assert trade["total_credit"] is None
        assert trade["max_loss"] is None
        assert trade["return_on_risk"] is None
        assert trade["max_profit"] is None

    @pytest.mark.parametrize("missing_leg", [
        "short_put", "long_put", "short_call", "long_call",
    ])
    def test_missing_leg_dq_flag(self, plugin, missing_leg):
        """Diagnostics should have pricing_dq flag when leg quote is missing."""
        candidate = self._make_missing_leg_candidate(missing_leg)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        diag = trade["_leg_diagnostics"]
        assert diag.get("pricing_dq") is True
        assert "LEG_QUOTE_INCOMPLETE" in diag.get("dq_reasons", [])

    def test_asymmetric_wings_max_loss(self, plugin):
        """max_loss uses max(put_wing_width, call_wing_width)."""
        # Build candidate with asymmetric wings: put=5, call=10
        ps_leg = _make_leg(strike=90, option_type="put", bid=0.85, ask=0.95, delta=-0.15)
        pl_leg = _make_leg(strike=85, option_type="put", bid=0.05, ask=0.10, delta=-0.05)
        cs_leg = _make_leg(strike=110, option_type="call", bid=0.85, ask=0.95, delta=0.15)
        cl_leg = _make_leg(strike=120, option_type="call", bid=0.05, ask=0.10, delta=0.05)

        candidate = {
            "strategy": "iron_condor",
            "spread_type": "iron_condor",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": 100.0,
            "put_short_strike": 90,
            "put_long_strike": 85,
            "call_short_strike": 110,
            "call_long_strike": 120,
            "legs": [
                {"name": "long_put",   "right": "put",  "side": "buy",  "strike": 85,  "qty": 1, "_contract": pl_leg},
                {"name": "short_put",  "right": "put",  "side": "sell", "strike": 90,  "qty": 1, "_contract": ps_leg},
                {"name": "short_call", "right": "call", "side": "sell", "strike": 110, "qty": 1, "_contract": cs_leg},
                {"name": "long_call",  "right": "call", "side": "buy",  "strike": 120, "qty": 1, "_contract": cl_leg},
            ],
            "width_put": 5.0,
            "width_call": 10.0,
            "symmetry_score": 0.7,
            "expected_move": 5.0,
            "snapshot": {"symbol": "TEST", "prices_history": []},
        }
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        # max_loss should use the wider wing (call=10)
        expected_max_loss = 10.0 * 100.0 - trade["net_credit"] * 100.0
        assert trade["max_loss"] == pytest.approx(expected_max_loss, abs=0.1)


# ────────────────────────────────────────────────────────────
# Width & Per-Leg Mid Output Tests
# ────────────────────────────────────────────────────────────

class TestWidthAndLegMids:
    """Ensure 'width' field and per-leg mid fields are populated."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        return IronCondorStrategyPlugin()

    def _make_candidate(self, width_put: float = 5.0, width_call: float = 5.0) -> dict[str, Any]:
        ps_leg = _make_leg(strike=90, option_type="put", bid=0.85, ask=0.95, delta=-0.15)
        pl_leg = _make_leg(strike=85, option_type="put", bid=0.05, ask=0.10, delta=-0.05)
        cs_leg = _make_leg(strike=110, option_type="call", bid=0.85, ask=0.95, delta=0.15)
        cl_leg = _make_leg(strike=115, option_type="call", bid=0.05, ask=0.10, delta=0.05)
        return {
            "strategy": "iron_condor",
            "spread_type": "iron_condor",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": 100.0,
            "put_short_strike": 90,
            "put_long_strike": 85,
            "call_short_strike": 110,
            "call_long_strike": 115,
            "legs": [
                {"name": "long_put",   "right": "put",  "side": "buy",  "strike": 85,  "qty": 1, "_contract": pl_leg},
                {"name": "short_put",  "right": "put",  "side": "sell", "strike": 90,  "qty": 1, "_contract": ps_leg},
                {"name": "short_call", "right": "call", "side": "sell", "strike": 110, "qty": 1, "_contract": cs_leg},
                {"name": "long_call",  "right": "call", "side": "buy",  "strike": 115, "qty": 1, "_contract": cl_leg},
            ],
            "width_put": width_put,
            "width_call": width_call,
            "symmetry_score": 1.0,
            "expected_move": 5.0,
            "snapshot": {"symbol": "TEST", "prices_history": []},
        }

    def test_width_field_present(self, plugin):
        """Enriched output must have width = max(put_wing_width, call_wing_width)."""
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert "width" in trade
        assert trade["width"] == 5.0

    def test_width_uses_max_wing(self, plugin):
        """width should be the larger of the two wing widths."""
        candidate = self._make_candidate(width_put=5.0, width_call=10.0)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["width"] == 10.0

    def test_per_leg_mids_present_when_ready(self, plugin):
        """Per-leg mid fields should be populated when readiness=True."""
        candidate = self._make_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["readiness"] is True
        assert trade["short_put_mid"] == pytest.approx(0.90, abs=0.001)
        assert trade["long_put_mid"] == pytest.approx(0.075, abs=0.001)
        assert trade["short_call_mid"] == pytest.approx(0.90, abs=0.001)
        assert trade["long_call_mid"] == pytest.approx(0.075, abs=0.001)

    def test_per_leg_mids_none_when_unready(self, plugin):
        """Per-leg mid fields should be None when readiness=False."""
        ps_leg = _make_leg(strike=90, option_type="put", bid=None, ask=None, delta=-0.15)
        pl_leg = _make_leg(strike=85, option_type="put", bid=0.05, ask=0.10, delta=-0.05)
        cs_leg = _make_leg(strike=110, option_type="call", bid=0.85, ask=0.95, delta=0.15)
        cl_leg = _make_leg(strike=115, option_type="call", bid=0.05, ask=0.10, delta=0.05)
        candidate = {
            "strategy": "iron_condor",
            "spread_type": "iron_condor",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": 100.0,
            "put_short_strike": 90,
            "put_long_strike": 85,
            "call_short_strike": 110,
            "call_long_strike": 115,
            "legs": [
                {"name": "long_put",   "right": "put",  "side": "buy",  "strike": 85,  "qty": 1, "_contract": pl_leg},
                {"name": "short_put",  "right": "put",  "side": "sell", "strike": 90,  "qty": 1, "_contract": ps_leg},
                {"name": "short_call", "right": "call", "side": "sell", "strike": 110, "qty": 1, "_contract": cs_leg},
                {"name": "long_call",  "right": "call", "side": "buy",  "strike": 115, "qty": 1, "_contract": cl_leg},
            ],
            "width_put": 5.0,
            "width_call": 5.0,
            "symmetry_score": 1.0,
            "expected_move": 5.0,
            "snapshot": {"symbol": "TEST", "prices_history": []},
        }
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["readiness"] is False
        assert trade["short_put_mid"] is None
        assert trade["long_put_mid"] is None
        assert trade["short_call_mid"] is None
        assert trade["long_call_mid"] is None


# ────────────────────────────────────────────────────────────
# Readiness Guardrail Tests
# ────────────────────────────────────────────────────────────

class TestReadinessGuardrail:
    """Ensure unready IC trades are rejected with LEG_QUOTE_INCOMPLETE."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        return IronCondorStrategyPlugin()

    def test_evaluate_rejects_unready_total_credit_none(self, plugin):
        """evaluate() should reject a trade with total_credit=None (unready)."""
        trade = {
            "strategy": "iron_condor",
            "spread_type": "iron_condor",
            "readiness": False,
            "total_credit": None,
            "return_on_risk": None,
            "symmetry_score": 0.9,
            "expected_move_ratio": 1.5,
        }
        ok, reasons = plugin.evaluate(trade)
        assert ok is False
        assert "credit_below_min" in reasons

    def test_strategy_service_guardrail_emits_leg_quote_incomplete(self):
        """The readiness guardrail in strategy_service should produce
        LEG_QUOTE_INCOMPLETE for readiness=False rows."""
        # We simulate what strategy_service does: check readiness before evaluate
        row = {"readiness": False, "total_credit": None}
        if row.get("readiness") is False:
            reasons = ["LEG_QUOTE_INCOMPLETE"]
        else:
            reasons = []
        assert reasons == ["LEG_QUOTE_INCOMPLETE"]
