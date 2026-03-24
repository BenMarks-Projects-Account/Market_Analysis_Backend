"""Tests for portfolio_balancer.build_rebalance_plan()."""

import asyncio
import pytest

from app.services.portfolio_balancer import (
    build_rebalance_plan,
    _estimate_trade_risk,
    _estimate_trade_delta,
    _extract_max_loss,
    _extract_candidate_delta,
    _safe_float,
    _get_held_underlyings,
    _get_underlying_risk,
    _size_position,
    _extract_candidate_summary,
    _extract_stock_risk,
)

# ─── Fixtures ───

def _base_policy():
    return {
        "max_risk_per_trade": 500,
        "max_risk_total": 3000,
        "max_concurrent_trades": 10,
        "max_risk_per_underlying": 1000,
        "target_portfolio_delta_range": (-5.0, 5.0),
        "default_contracts_cap": 3,
        "regime_multiplier": 1.0,
    }

def _base_balance():
    return {"equity": 50000, "buying_power": 25000, "cash": 10000}

def _base_greeks():
    return {"delta": 0.5, "gamma": 0.01, "theta": -5.0, "vega": 2.0}

def _base_concentration():
    return {"by_underlying": {"items": []}, "by_strategy": {}, "by_expiration": {}}

def _make_close_rec(symbol, recommendation="CLOSE", max_loss=300, trade_delta=0.15, conviction=80):
    return {
        "symbol": symbol,
        "strategy": "put_credit_spread",
        "recommendation": recommendation,
        "conviction": conviction,
        "rationale_summary": f"Close {symbol} due to risk",
        "trade_health_score": 35,
        "position_snapshot": {"max_loss": max_loss, "cost_basis_total": max_loss},
        "live_greeks": {"trade_delta": trade_delta},
        "suggested_close_order": {"ready_for_preview": True, "legs": []},
    }

def _make_hold_rec(symbol, conviction=70):
    return {
        "symbol": symbol,
        "strategy": "put_credit_spread",
        "recommendation": "HOLD",
        "conviction": conviction,
        "trade_health_score": 75,
        "position_snapshot": {"max_loss": 250},
        "live_greeks": {"trade_delta": 0.1},
    }

def _make_options_candidate(symbol, scanner_key="put_credit_spread", max_loss_cents=-20000,
                             ev=45.0, ror=0.12, regime_alignment="aligned", rank=1):
    return {
        "symbol": symbol,
        "scanner_key": scanner_key,
        "math": {"max_loss": max_loss_cents, "ev": ev, "ror": ror, "pop": 72, "max_profit": 8000},
        "regime_alignment": regime_alignment,
        "rank": rank,
        "dte": 30,
        "dte_bucket": "30-45",
    }

def _make_stock_candidate(symbol, regime_alignment="aligned", rank=1):
    return {
        "symbol": symbol,
        "strategy": "pullback_swing",
        "scanner_key": "pullback_swing",
        "regime_alignment": regime_alignment,
        "rank": rank,
    }

def _run(coro):
    return asyncio.run(coro)


# ─── Helper unit tests ───

class TestEstimateTradeRisk:
    def test_uses_max_loss(self):
        rec = {"position_snapshot": {"max_loss": 400, "cost_basis_total": 200}}
        assert _estimate_trade_risk(rec) == 400

    def test_falls_back_to_cost_basis(self):
        rec = {"position_snapshot": {"max_loss": None, "cost_basis_total": 300}}
        assert _estimate_trade_risk(rec) == 300

    def test_no_snapshot(self):
        assert _estimate_trade_risk({}) == 0


class TestEstimateTradeDelta:
    def test_returns_trade_delta(self):
        rec = {"live_greeks": {"trade_delta": 0.25}}
        assert _estimate_trade_delta(rec) == 0.25

    def test_no_greeks(self):
        assert _estimate_trade_delta({}) == 0


class TestExtractMaxLoss:
    def test_converts_cents_to_dollars(self):
        cand = {"math": {"max_loss": -20000}}
        assert _extract_max_loss(cand) == 200.0

    def test_missing_max_loss(self):
        assert _extract_max_loss({"math": {}}) == 0


class TestExtractCandidateDelta:
    def test_put_credit(self):
        cand = {"scanner_key": "put_credit_spread", "math": {}}
        assert _extract_candidate_delta(cand) == 0.15

    def test_call_credit(self):
        cand = {"scanner_key": "call_credit_spread", "math": {}}
        assert _extract_candidate_delta(cand) == -0.15

    def test_iron_condor(self):
        cand = {"scanner_key": "iron_condor", "math": {}}
        assert _extract_candidate_delta(cand) == 0.0

    def test_unknown_strategy(self):
        cand = {"scanner_key": "butterfly_debit", "math": {}}
        assert _extract_candidate_delta(cand) == 0.0


class TestSafeFloat:
    def test_valid(self):
        assert _safe_float(3.14) == 3.14

    def test_string(self):
        assert _safe_float("2.5") == 2.5

    def test_none(self):
        assert _safe_float(None) is None

    def test_invalid(self):
        assert _safe_float("abc") is None


class TestGetHeldUnderlyings:
    def test_collects_symbols(self):
        holds = [{"symbol": "SPY"}, {"symbol": "QQQ"}]
        reduces = [{"symbol": "IWM"}]
        result = _get_held_underlyings(holds, reduces)
        assert result == {"SPY", "QQQ", "IWM"}

    def test_empty(self):
        assert _get_held_underlyings([], []) == set()


class TestGetUnderlyingRisk:
    def test_finds_symbol(self):
        conc = {"by_underlying": {"items": [{"symbol": "SPY", "risk": 500}]}}
        assert _get_underlying_risk(conc, "SPY") == 500

    def test_missing_symbol(self):
        conc = {"by_underlying": {"items": [{"symbol": "QQQ", "risk": 300}]}}
        assert _get_underlying_risk(conc, "SPY") == 0


class TestSizePosition:
    def test_basic_sizing(self):
        # 500 budget / 200 per contract = 2, capped at 3
        assert _size_position(200, 500, {"default_contracts_cap": 3}) == 2

    def test_cap_applied(self):
        # 500 / 100 = 5, capped at 3
        assert _size_position(100, 500, {"default_contracts_cap": 3}) == 3

    def test_minimum_one(self):
        # 500 / 600 = 0, floor to 1
        assert _size_position(600, 500, {"default_contracts_cap": 3}) == 1

    def test_zero_max_loss(self):
        assert _size_position(0, 500, {"default_contracts_cap": 3}) == 1


class TestExtractStockRisk:
    def test_returns_max_per_trade(self):
        assert _extract_stock_risk({}, 500) == 500


class TestExtractCandidateSummary:
    def test_extracts_fields(self):
        cand = {
            "symbol": "SPY",
            "scanner_key": "put_credit_spread",
            "dte": 30,
            "dte_bucket": "30-45",
            "math": {"ev": 45, "pop": 72, "max_profit": 80, "max_loss": -200},
            "event_risk": "FOMC in 5 days",
        }
        s = _extract_candidate_summary(cand)
        assert s["symbol"] == "SPY"
        assert s["ev"] == 45
        assert s["event_risk"] == "FOMC in 5 days"


# ─── Integration: build_rebalance_plan ───

class TestRebalancePlanCategorization:
    """Step 1: active trade recommendations are categorized correctly."""

    def test_close_and_hold_split(self):
        recs = [
            _make_close_rec("SPY", "CLOSE"),
            _make_close_rec("QQQ", "URGENT_REVIEW"),
            _make_hold_rec("IWM"),
        ]
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": recs},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert len(plan["close_actions"]) == 2
        assert plan["close_actions"][0]["action"] == "CLOSE"
        assert plan["close_actions"][1]["action"] == "CLOSE"
        assert len(plan["hold_positions"]) == 1
        assert plan["hold_positions"][0]["symbol"] == "IWM"

    def test_reduce_categorized(self):
        recs = [_make_close_rec("SPY", "REDUCE")]
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": recs},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert len(plan["close_actions"]) == 1
        assert plan["close_actions"][0]["action"] == "REDUCE"

    def test_empty_recommendations(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert plan["close_actions"] == []
        assert plan["hold_positions"] == []


class TestRiskFreed:
    """Step 2: risk and delta freed calculations."""

    def test_close_frees_full_risk(self):
        recs = [_make_close_rec("SPY", "CLOSE", max_loss=400, trade_delta=0.2)]
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": recs},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert plan["close_actions"][0]["risk_freed"] == 400.0
        assert plan["close_actions"][0]["delta_freed"] == 0.2

    def test_reduce_frees_half_risk(self):
        recs = [_make_close_rec("SPY", "REDUCE", max_loss=400, trade_delta=0.2)]
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": recs},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert plan["close_actions"][0]["risk_freed"] == 200.0
        assert plan["close_actions"][0]["delta_freed"] == 0.1


class TestPostAdjustmentState:
    """Step 3: post-adjustment state is computed correctly."""

    def test_post_adjustment_risk_and_slots(self):
        # 3 recs: 1 close, 1 reduce, 1 hold = 10-2 closes = 8 slots... wait
        # trade_count=3, closes=1, post_adj=2, open_slots=10-2=8
        recs = [
            _make_close_rec("SPY", "CLOSE", max_loss=300),
            _make_close_rec("QQQ", "REDUCE", max_loss=200),
            _make_hold_rec("IWM"),
        ]
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": recs},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        pa = plan["post_adjustment_state"]
        # Only CLOSE removed from trade count, REDUCE stays
        assert pa["open_trade_count"] == 2  # 3 - 1 close
        assert pa["open_slots"] == 8  # 10 - 2
        assert pa["risk_freed_by_closes"] == 400.0  # 300 + 200*0.5


class TestCandidateFiltering:
    """Step 4: new candidates filtered through constraints."""

    def test_accepts_valid_candidate(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY")],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert len(plan["open_actions"]) == 1
        assert plan["open_actions"][0]["symbol"] == "SPY"
        assert plan["open_actions"][0]["source"] == "options"

    def test_rejects_when_no_slots(self):
        policy = _base_policy()
        policy["max_concurrent_trades"] = 2
        recs = [_make_hold_rec("A"), _make_hold_rec("B")]
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": recs},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY")],
            account_balance=_base_balance(), risk_policy=policy,
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert len(plan["open_actions"]) == 0
        assert len(plan["skip_actions"]) == 1
        assert "Max concurrent trades" in plan["skip_actions"][0]["skip_reason"]

    def test_rejects_over_risk_budget(self):
        policy = _base_policy()
        policy["max_risk_total"] = 100  # Very tight budget
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY", max_loss_cents=-30000)],
            account_balance=_base_balance(), risk_policy=policy,
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert len(plan["open_actions"]) == 0
        assert len(plan["skip_actions"]) == 1
        assert "risk budget" in plan["skip_actions"][0]["skip_reason"]

    def test_rejects_over_per_trade_limit(self):
        policy = _base_policy()
        policy["max_risk_per_trade"] = 50  # Tiny per-trade limit
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY", max_loss_cents=-20000)],
            account_balance=_base_balance(), risk_policy=policy,
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert len(plan["open_actions"]) == 0
        assert "per-trade risk limit" in plan["skip_actions"][0]["skip_reason"]

    def test_rejects_concentration_breach(self):
        conc = {"by_underlying": {"items": [{"symbol": "SPY", "risk": 900}]}}
        policy = _base_policy()
        policy["max_risk_per_underlying"] = 1000
        # Candidate adds 200 risk → 900+200 = 1100 > 1000
        # But candidate must already be in used_underlyings from holds
        recs = [_make_hold_rec("SPY")]
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": recs},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY", max_loss_cents=-20000)],
            account_balance=_base_balance(), risk_policy=policy,
            portfolio_greeks=_base_greeks(), concentration=conc,
        ))
        assert len(plan["skip_actions"]) == 1
        assert "concentration limit" in plan["skip_actions"][0]["skip_reason"]

    def test_rejects_misaligned_regime(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY", regime_alignment="misaligned")],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
            regime_label="RISK_OFF",
        ))
        assert len(plan["skip_actions"]) == 1
        assert "misaligned" in plan["skip_actions"][0]["skip_reason"]

    def test_rejects_delta_breach(self):
        policy = _base_policy()
        policy["target_portfolio_delta_range"] = (-0.1, 0.1)
        greeks = {"delta": 0.05}  # near upper limit
        # put_credit adds 0.15 → 0.20 > 0.1
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY")],
            account_balance=_base_balance(), risk_policy=policy,
            portfolio_greeks=greeks, concentration=_base_concentration(),
        ))
        assert len(plan["skip_actions"]) == 1
        assert "portfolio delta" in plan["skip_actions"][0]["skip_reason"]


class TestCandidateSorting:
    """Candidates sorted by regime alignment then risk-adjusted return."""

    def test_aligned_first(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[],
            options_candidates=[
                _make_options_candidate("IWM", regime_alignment="neutral", ror=0.15, rank=1),
                _make_options_candidate("SPY", regime_alignment="aligned", ror=0.10, rank=2),
            ],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        # Aligned SPY should come first despite lower ror
        assert plan["open_actions"][0]["symbol"] == "SPY"
        assert plan["open_actions"][1]["symbol"] == "IWM"

    def test_higher_ror_within_alignment(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[],
            options_candidates=[
                _make_options_candidate("IWM", regime_alignment="aligned", ror=0.10, rank=1),
                _make_options_candidate("SPY", regime_alignment="aligned", ror=0.20, rank=2),
            ],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert plan["open_actions"][0]["symbol"] == "SPY"  # Higher ror


class TestPositionSizing:
    """Position sizing respects per-trade risk limit and contracts cap."""

    def test_sizing_within_cap(self):
        # max_loss = 200 (20000 cents / 100), max_risk_per_trade = 500
        # 500 / 200 = 2, cap = 3 → 2 contracts
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY", max_loss_cents=-20000)],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert plan["open_actions"][0]["contracts"] == 2

    def test_sizing_capped(self):
        policy = _base_policy()
        policy["max_risk_per_trade"] = 2000  # Very large budget
        policy["default_contracts_cap"] = 3
        # max_loss = 200, 2000/200 = 10, cap = 3 → 3 contracts
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY", max_loss_cents=-20000)],
            account_balance=_base_balance(), risk_policy=policy,
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert plan["open_actions"][0]["contracts"] == 3


class TestNetImpact:
    """Step 5: net impact summary shows before/after for risk, delta, trade count."""

    def test_full_cycle_net_impact(self):
        recs = [
            _make_close_rec("SPY", "CLOSE", max_loss=300, trade_delta=0.15),
            _make_hold_rec("IWM"),
        ]
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": recs},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("QQQ", max_loss_cents=-10000)],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        ni = plan["net_impact"]
        assert ni["positions_closed"] == 1
        assert ni["positions_held"] == 1
        assert ni["positions_opened"] == 1
        assert ni["trades_before"] == 2
        assert ni["trades_after"] == 2  # 2-1+1
        assert "risk_before" in ni
        assert "risk_after_closes" in ni
        assert "risk_after_opens" in ni
        assert "delta_before" in ni
        assert "delta_after" in ni
        assert "risk_budget_remaining" in ni

    def test_net_impact_no_changes(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        ni = plan["net_impact"]
        assert ni["positions_closed"] == 0
        assert ni["positions_opened"] == 0
        assert ni["risk_change"] == 0


class TestOutputShape:
    """Verify output contains all required top-level keys."""

    def test_all_keys_present(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert "close_actions" in plan
        assert "hold_positions" in plan
        assert "open_actions" in plan
        assert "skip_actions" in plan
        assert "net_impact" in plan
        assert "post_adjustment_state" in plan
        assert "risk_policy_used" in plan

    def test_risk_policy_used_fields(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
            regime_label="RISK_ON",
        ))
        rpu = plan["risk_policy_used"]
        assert rpu["regime_label"] == "RISK_ON"
        assert rpu["account_equity"] == 50000
        assert rpu["max_risk_per_trade"] == 500


class TestStockCandidates:
    """Stock candidates are handled correctly."""

    def test_stock_candidate_accepted(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[_make_stock_candidate("AAPL")],
            options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert len(plan["open_actions"]) == 1
        assert plan["open_actions"][0]["source"] == "stock"
        assert plan["open_actions"][0]["symbol"] == "AAPL"

    def test_stock_delta_is_one(self):
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[_make_stock_candidate("AAPL")],
            options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert plan["open_actions"][0]["delta_impact"] is not None
        assert plan["open_actions"][0]["delta_impact"] > 0


class TestMixedCandidates:
    """Integration with both stock and options candidates."""

    def test_fills_slots_from_both_sources(self):
        policy = _base_policy()
        policy["max_concurrent_trades"] = 3
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[_make_stock_candidate("AAPL", rank=3)],
            options_candidates=[
                _make_options_candidate("SPY", ror=0.15, rank=1),
                _make_options_candidate("QQQ", ror=0.10, rank=2),
            ],
            account_balance=_base_balance(), risk_policy=policy,
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert len(plan["open_actions"]) == 3
        sources = {a["source"] for a in plan["open_actions"]}
        assert "stock" in sources
        assert "options" in sources

    def test_risk_depleted_skips_remaining(self):
        policy = _base_policy()
        policy["max_risk_total"] = 400  # Only room for ~2 options trades
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": []},
            stock_candidates=[_make_stock_candidate("MSFT")],
            options_candidates=[
                _make_options_candidate("SPY", max_loss_cents=-15000, ror=0.15),
                _make_options_candidate("QQQ", max_loss_cents=-15000, ror=0.10),
            ],
            account_balance=_base_balance(), risk_policy=policy,
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        total_opened = len(plan["open_actions"])
        total_skipped = len(plan["skip_actions"])
        assert total_opened + total_skipped == 3
        assert total_skipped >= 1


class TestCloseOrderPresence:
    """Close actions include suggested_close_order from pipeline recommendations."""

    def test_close_order_passed_through(self):
        rec = _make_close_rec("SPY", "CLOSE")
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": [rec]},
            stock_candidates=[], options_candidates=[],
            account_balance=_base_balance(), risk_policy=_base_policy(),
            portfolio_greeks=_base_greeks(), concentration=_base_concentration(),
        ))
        assert plan["close_actions"][0]["close_order"]["ready_for_preview"] is True


class TestSkipReasonReadability:
    """Skip reasons should be specific and actionable."""

    def test_skip_reason_includes_symbol(self):
        conc = {"by_underlying": {"items": [{"symbol": "SPY", "risk": 900}]}}
        policy = _base_policy()
        policy["max_risk_per_underlying"] = 1000
        recs = [_make_hold_rec("SPY")]
        plan = _run(build_rebalance_plan(
            active_trade_results={"recommendations": recs},
            stock_candidates=[],
            options_candidates=[_make_options_candidate("SPY", max_loss_cents=-20000)],
            account_balance=_base_balance(), risk_policy=policy,
            portfolio_greeks=_base_greeks(), concentration=conc,
        ))
        reason = plan["skip_actions"][0]["skip_reason"]
        assert "SPY" in reason
        assert "$" in reason
