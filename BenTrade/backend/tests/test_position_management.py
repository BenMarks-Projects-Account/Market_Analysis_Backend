"""Tests for position management enrichment.

Covers:
- Strategy classification
- Income position enrichment (credit spreads, iron condors)
- Debit position enrichment (debit spreads, butterflies)
- Equity position enrichment
- Management status determination
- Action suggestions
- Portfolio summary aggregation
- Null/missing data handling
"""

import pytest
from app.services.position_management import (
    ACTION_CLOSE,
    ACTION_HOLD,
    ACTION_WATCH,
    MANAGEMENT_POLICIES,
    STATUS_AT_STOP,
    STATUS_AT_TARGET,
    STATUS_EXPIRED,
    STATUS_IN_DANGER,
    STATUS_NEUTRAL,
    STATUS_ON_TRACK,
    STATUS_TIME_DECAY,
    build_portfolio_summary,
    classify_strategy,
    determine_status,
    enrich_all_recommendations,
    enrich_recommendation_with_management,
    get_management_policy,
    suggest_action,
)


# ── Strategy classification ─────────────────────────────────


class TestClassifyStrategy:
    def test_income_strategies(self):
        assert classify_strategy("put_credit_spread") == "income"
        assert classify_strategy("call_credit_spread") == "income"
        assert classify_strategy("iron_condor") == "income"
        assert classify_strategy("iron_butterfly") == "income"

    def test_directional_strategies(self):
        assert classify_strategy("put_debit") == "directional"
        assert classify_strategy("call_debit") == "directional"

    def test_butterfly_strategy(self):
        assert classify_strategy("butterfly_debit") == "butterfly"

    def test_equity(self):
        assert classify_strategy("stock", "equity") == "equity"
        assert classify_strategy(None, "equity") == "equity"

    def test_fallback_heuristics(self):
        assert classify_strategy("some_credit_thing") == "income"
        assert classify_strategy("calendar_call_spread") == "income"
        assert classify_strategy("diagonal_put_spread") == "income"

    def test_default_income(self):
        assert classify_strategy("unknown_strategy") == "income"
        assert classify_strategy(None) == "income"


# ── Management policies ─────────────────────────────────────


class TestManagementPolicies:
    def test_all_classes_have_policies(self):
        for cls in ("income", "directional", "butterfly", "equity"):
            policy = get_management_policy(cls)
            assert "profit_target_pct" in policy
            assert "stop_loss_multiplier" in policy

    def test_income_policy_values(self):
        p = MANAGEMENT_POLICIES["income"]
        assert p["profit_target_pct"] == 0.50
        assert p["stop_loss_multiplier"] == 2.0

    def test_unknown_class_falls_back(self):
        policy = get_management_policy("nonexistent")
        assert policy == MANAGEMENT_POLICIES["income"]


# ── Status determination ────────────────────────────────────


class TestDetermineStatus:
    def setup_method(self):
        self.policy = MANAGEMENT_POLICIES["income"]

    def test_expired(self):
        assert determine_status(50, 10, 0, "income", self.policy) == STATUS_EXPIRED
        assert determine_status(50, 10, -1, "income", self.policy) == STATUS_EXPIRED

    def test_at_target(self):
        assert determine_status(95, 0, 30, "income", self.policy) == STATUS_AT_TARGET
        assert determine_status(100, 0, 30, "income", self.policy) == STATUS_AT_TARGET

    def test_at_stop(self):
        assert determine_status(0, 90, 30, "income", self.policy) == STATUS_AT_STOP
        assert determine_status(0, 100, 30, "income", self.policy) == STATUS_AT_STOP

    def test_time_decay_profitable(self):
        # Within min_dte_to_manage (7) and profitable (>=60%)
        assert determine_status(65, 10, 5, "income", self.policy) == STATUS_TIME_DECAY

    def test_in_danger_gamma_zone(self):
        # Within min_dte_to_manage but not profitable enough
        assert determine_status(30, 20, 5, "income", self.policy) == STATUS_IN_DANGER

    def test_in_danger_loss_progress(self):
        assert determine_status(10, 55, 30, "income", self.policy) == STATUS_IN_DANGER

    def test_on_track(self):
        assert determine_status(40, 10, 30, "income", self.policy) == STATUS_ON_TRACK

    def test_neutral(self):
        assert determine_status(15, 10, 30, "income", self.policy) == STATUS_NEUTRAL

    def test_equity_no_time_decay(self):
        equity_policy = MANAGEMENT_POLICIES["equity"]
        # Equity should not trigger TIME_DECAY even with dte
        assert determine_status(65, 10, 5, "equity", equity_policy) != STATUS_TIME_DECAY


# ── Action suggestions ──────────────────────────────────────


class TestSuggestAction:
    def test_at_target_suggests_close(self):
        action = suggest_action(STATUS_AT_TARGET, 95, 0, 30, 500)
        assert action["action"] == ACTION_CLOSE
        assert action["urgency"] == "high"
        assert "lock in" in action["message"].lower() or "profit" in action["message"].lower()

    def test_at_stop_suggests_close(self):
        action = suggest_action(STATUS_AT_STOP, 0, 95, 30, -200)
        assert action["action"] == ACTION_CLOSE
        assert action["urgency"] == "high"

    def test_on_track_suggests_hold(self):
        action = suggest_action(STATUS_ON_TRACK, 50, 10, 30, 150)
        assert action["action"] == ACTION_HOLD
        assert action["urgency"] == "low"

    def test_in_danger_suggests_watch(self):
        action = suggest_action(STATUS_IN_DANGER, 10, 60, 30, -100)
        assert action["action"] == ACTION_WATCH
        assert action["urgency"] == "medium"

    def test_time_decay_suggests_close(self):
        action = suggest_action(STATUS_TIME_DECAY, 70, 5, 5, 200)
        assert action["action"] == ACTION_CLOSE
        assert action["urgency"] == "medium"
        assert "gamma" in action["message"].lower()


# ── Income enrichment ───────────────────────────────────────


class TestIncomeEnrichment:
    """Test enrichment for credit spread / income positions."""

    def _make_rec(self, entry_price=1.00, mark_price=0.50, unrealized_pnl=50.0, dte=30):
        return {
            "strategy_id": "put_credit_spread",
            "strategy": "put_credit_spread",
            "dte": dte,
            "expiration": "2026-05-01",
            "legs": [{"symbol": "SPY260501P00530000", "side": "short", "strike": 530}],
            "position_snapshot": {
                "avg_open_price": entry_price,
                "mark_price": mark_price,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": 0.5 if entry_price else None,
            },
        }

    def test_basic_enrichment(self):
        rec = self._make_rec(entry_price=1.00, mark_price=0.50, unrealized_pnl=50.0, dte=30)
        result = enrich_recommendation_with_management(rec)

        assert result["strategy_class"] == "income"
        assert result["profit_target_value"] is not None
        assert result["stop_loss_value"] is not None
        assert result["profit_progress_pct"] is not None
        assert result["loss_progress_pct"] is not None
        assert result["management_status"] in (
            STATUS_AT_TARGET, STATUS_ON_TRACK, STATUS_NEUTRAL,
            STATUS_IN_DANGER, STATUS_AT_STOP, STATUS_TIME_DECAY, STATUS_EXPIRED,
        )
        assert "action" in result["suggested_action"]

    def test_at_50_pct_profit(self):
        """Credit=1.00, mark=0.50 → 50% profit captured = 100% of target (50% policy)."""
        rec = self._make_rec(entry_price=1.00, mark_price=0.50, unrealized_pnl=50.0)
        result = enrich_recommendation_with_management(rec)

        # Profit progress: pnl_per_unit=0.50, max_profit=0.50 → 100%
        assert result["profit_progress_pct"] >= 95
        assert result["management_status"] == STATUS_AT_TARGET

    def test_at_breakeven(self):
        """Credit=1.00, mark=1.00 → no progress."""
        rec = self._make_rec(entry_price=1.00, mark_price=1.00, unrealized_pnl=0)
        result = enrich_recommendation_with_management(rec)

        assert result["profit_progress_pct"] == 0
        assert result["loss_progress_pct"] == 0
        assert result["management_status"] == STATUS_NEUTRAL

    def test_losing_position(self):
        """Credit=1.00, mark=2.00 → loss of 1.00, which is 50% of 2.0 stop."""
        rec = self._make_rec(entry_price=1.00, mark_price=2.00, unrealized_pnl=-100.0)
        result = enrich_recommendation_with_management(rec)

        assert result["loss_progress_pct"] == 50.0
        assert result["management_status"] == STATUS_IN_DANGER

    def test_at_stop(self):
        """Credit=1.00, mark=3.00 → loss of 2.00 = 100% of stop (2× credit)."""
        rec = self._make_rec(entry_price=1.00, mark_price=3.00, unrealized_pnl=-200.0)
        result = enrich_recommendation_with_management(rec)

        assert result["loss_progress_pct"] >= 90
        assert result["management_status"] == STATUS_AT_STOP

    def test_management_levels_correct(self):
        """Verify profit target and stop loss values."""
        rec = self._make_rec(entry_price=1.00, mark_price=0.80)
        result = enrich_recommendation_with_management(rec)

        # Profit target: credit × (1 - 0.50) = 0.50
        assert result["profit_target_value"] == 0.50
        # Stop loss: credit × (1 + 2.0) = 3.00
        assert result["stop_loss_value"] == 3.00

    def test_passes_through_original_fields(self):
        rec = self._make_rec()
        rec["recommendation"] = "HOLD"
        rec["conviction"] = 0.82
        rec["rationale_summary"] = "All good."
        result = enrich_recommendation_with_management(rec)

        assert result["recommendation"] == "HOLD"
        assert result["conviction"] == 0.82
        assert result["rationale_summary"] == "All good."


# ── Debit enrichment ────────────────────────────────────────


class TestDebitEnrichment:
    def _make_rec(self, entry_price=2.00, mark_price=3.50, unrealized_pnl=150.0, dte=30):
        return {
            "strategy_id": "call_debit",
            "strategy": "call_debit",
            "dte": dte,
            "expiration": "2026-05-01",
            "legs": [{"symbol": "SPY260501C00530000", "side": "long", "strike": 530}],
            "position_snapshot": {
                "avg_open_price": entry_price,
                "mark_price": mark_price,
                "unrealized_pnl": unrealized_pnl,
                "width": 5.0,
            },
        }

    def test_profitable_debit(self):
        """Debit=2.00, mark=3.50, width=5 → profit = 1.50."""
        rec = self._make_rec()
        result = enrich_recommendation_with_management(rec)

        assert result["strategy_class"] == "directional"
        assert result["pnl_per_unit"] == 1.50
        assert result["profit_progress_pct"] > 0
        assert result["management_status"] == STATUS_ON_TRACK

    def test_losing_debit(self):
        """Debit=2.00, mark=1.00 → loss = 1.00 = 50% of max_loss (2.00 × 1.0)."""
        rec = self._make_rec(entry_price=2.00, mark_price=1.00, unrealized_pnl=-100.0)
        result = enrich_recommendation_with_management(rec)

        assert result["loss_progress_pct"] == 50.0
        assert result["management_status"] == STATUS_IN_DANGER


# ── Equity enrichment ───────────────────────────────────────


class TestEquityEnrichment:
    def test_stock_position(self):
        rec = {
            "strategy_id": "equity",
            "position_type": "equity",
            "dte": None,
            "expiration": None,
            "legs": [],
            "position_snapshot": {
                "avg_open_price": 100.0,
                "mark_price": 108.0,
                "unrealized_pnl": 800.0,
            },
        }
        result = enrich_recommendation_with_management(rec)

        assert result["strategy_class"] == "equity"
        # 10% target policy: target = 110.0, stop = 93.0
        assert result["profit_target_value"] == 110.0
        assert result["stop_loss_value"] == 93.0
        assert result["profit_progress_pct"] == 80.0  # 8/10 = 80%
        assert result["management_status"] == STATUS_ON_TRACK


# ── Null/missing data ──────────────────────────────────────


class TestNullHandling:
    def test_missing_entry_price(self):
        rec = {
            "strategy_id": "put_credit_spread",
            "dte": 30,
            "legs": [{}],
            "position_snapshot": {
                "avg_open_price": None,
                "mark_price": 0.50,
                "unrealized_pnl": 50.0,
            },
        }
        result = enrich_recommendation_with_management(rec)
        assert result["management_status"] == STATUS_NEUTRAL
        assert result["profit_target_value"] is None

    def test_missing_mark_price(self):
        rec = {
            "strategy_id": "put_credit_spread",
            "dte": 30,
            "legs": [{}],
            "position_snapshot": {
                "avg_open_price": 1.00,
                "mark_price": None,
                "unrealized_pnl": None,
            },
        }
        result = enrich_recommendation_with_management(rec)
        # Should still compute targets even without current price
        assert result["profit_target_value"] is not None
        assert result["stop_loss_value"] is not None

    def test_empty_recommendation(self):
        rec = {}
        result = enrich_recommendation_with_management(rec)
        # Should not crash
        assert result["management_status"] == STATUS_NEUTRAL


# ── Portfolio summary ───────────────────────────────────────


class TestPortfolioSummary:
    def test_basic_summary(self):
        recs = [
            {"management_status": STATUS_AT_TARGET, "total_pnl": 200, "position_snapshot": {}},
            {"management_status": STATUS_ON_TRACK, "total_pnl": 50, "position_snapshot": {}},
            {"management_status": STATUS_AT_STOP, "total_pnl": -150, "position_snapshot": {}},
            {"management_status": STATUS_NEUTRAL, "total_pnl": 0, "position_snapshot": {}},
        ]
        summary = build_portfolio_summary(recs)

        assert summary["total_positions"] == 4
        assert summary["total_pnl"] == 100.0
        assert summary["positions_at_target"] == 1
        assert summary["positions_at_stop"] == 1
        assert summary["positions_on_track"] == 1
        assert summary["positions_neutral"] == 1
        assert summary["actions_needed"] == 2  # AT_TARGET + AT_STOP
        assert summary["winning"] == 2
        assert summary["losing"] == 1

    def test_empty_portfolio(self):
        summary = build_portfolio_summary([])
        assert summary["total_positions"] == 0
        assert summary["total_pnl"] == 0
        assert summary["actions_needed"] == 0


# ── Enrichment batch ────────────────────────────────────────


class TestEnrichAllRecommendations:
    def test_batch_enrichment(self):
        recs = [
            {
                "strategy_id": "put_credit_spread",
                "dte": 30,
                "legs": [{}],
                "position_snapshot": {
                    "avg_open_price": 1.00,
                    "mark_price": 0.50,
                    "unrealized_pnl": 50.0,
                },
            },
            {
                "strategy_id": "call_debit",
                "dte": 20,
                "legs": [{}],
                "position_snapshot": {
                    "avg_open_price": 2.00,
                    "mark_price": 2.50,
                    "unrealized_pnl": 50.0,
                },
            },
        ]
        enriched = enrich_all_recommendations(recs)
        assert len(enriched) == 2
        assert enriched[0]["strategy_class"] == "income"
        assert enriched[1]["strategy_class"] == "directional"
        # Both have management status
        assert all("management_status" in r for r in enriched)
