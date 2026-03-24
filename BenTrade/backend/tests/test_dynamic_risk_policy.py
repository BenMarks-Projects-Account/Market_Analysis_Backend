"""Tests for build_dynamic_policy and _compute_max_contracts.

Verifies:
- Dollar limits scale with account equity
- Regime adjustment tightens/loosens limits
- Fallback to static policy when equity <= 0
- Position sizing scales with account size
- Greek targets are included
- Base percentages are stored for transparency
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.risk_policy_service import (
    RiskPolicyService,
    build_dynamic_policy,
    _compute_max_contracts,
)


# ── build_dynamic_policy: scaling with equity ─────────────────────────

class TestDynamicPolicyScaling:
    """Dollar limits must scale proportionally with account equity."""

    def test_50k_account_neutral(self):
        bal = {"equity": 50_000, "option_buying_power": 80_000}
        p = build_dynamic_policy(bal, regime_label="NEUTRAL")

        assert p["account_equity"] == 50_000
        assert p["buying_power"] == 80_000
        assert p["max_risk_per_trade"] == 500.0       # 1% of 50K
        assert p["max_risk_total"] == 3_000.0          # 6% of 50K
        assert p["max_risk_per_underlying"] == 1_000.0 # 2% of 50K
        assert p["regime_multiplier"] == 1.0
        assert p["dynamic"] is True

    def test_150k_account_neutral(self):
        bal = {"equity": 150_000, "option_buying_power": 250_000}
        p = build_dynamic_policy(bal, regime_label="NEUTRAL")

        assert p["max_risk_per_trade"] == 1_500.0      # 1% of 150K
        assert p["max_risk_total"] == 9_000.0           # 6% of 150K
        assert p["max_risk_per_underlying"] == 3_000.0  # 2% of 150K

    def test_limits_differ_by_account_size(self):
        small = build_dynamic_policy({"equity": 50_000})
        large = build_dynamic_policy({"equity": 150_000})

        assert large["max_risk_per_trade"] == 3 * small["max_risk_per_trade"]
        assert large["max_risk_total"] == 3 * small["max_risk_total"]

    def test_buying_power_usage(self):
        bal = {"equity": 100_000, "option_buying_power": 200_000}
        p = build_dynamic_policy(bal)
        assert p["max_buying_power_usage"] == 120_000.0  # 60% of 200K

    def test_min_cash_reserve(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal)
        assert p["min_cash_reserve"] == 20_000.0  # 20% of 100K

    def test_max_position_value(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal)
        assert p["max_position_value"] == 5_000.0  # 5% of 100K

    def test_portfolio_size_equals_equity(self):
        """portfolio_size should be set to equity for backward compatibility."""
        bal = {"equity": 75_000}
        p = build_dynamic_policy(bal)
        assert p["portfolio_size"] == 75_000


# ── Regime adjustment ─────────────────────────────────────────────────

class TestRegimeAdjustment:

    def test_risk_off_tightens_limits(self):
        bal = {"equity": 50_000}
        p = build_dynamic_policy(bal, regime_label="RISK_OFF")

        assert p["regime_multiplier"] == 0.70
        assert p["max_risk_per_trade"] == 350.0  # 1% × 50K × 0.70
        assert p["max_risk_total"] == 2_100.0    # 6% × 50K × 0.70

    def test_risk_off_caution_tightens(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal, regime_label="RISK_OFF_CAUTION")
        assert p["regime_multiplier"] == 0.70

    def test_risk_on_loosens_slightly(self):
        bal = {"equity": 50_000}
        p = build_dynamic_policy(bal, regime_label="RISK_ON")

        assert p["regime_multiplier"] == 1.10
        assert p["max_risk_per_trade"] == 550.0  # 1% × 50K × 1.10

    def test_neutral_no_adjustment(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal, regime_label="NEUTRAL")
        assert p["regime_multiplier"] == 1.0
        assert p["max_risk_per_trade"] == 1_000.0

    def test_none_regime_no_adjustment(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal, regime_label=None)
        assert p["regime_multiplier"] == 1.0

    def test_same_account_different_regimes(self):
        bal = {"equity": 100_000}
        risk_on = build_dynamic_policy(bal, "RISK_ON")
        neutral = build_dynamic_policy(bal, "NEUTRAL")
        risk_off = build_dynamic_policy(bal, "RISK_OFF")

        assert risk_on["max_risk_per_trade"] > neutral["max_risk_per_trade"]
        assert neutral["max_risk_per_trade"] > risk_off["max_risk_per_trade"]


# ── Fallback behavior ────────────────────────────────────────────────

class TestDynamicPolicyFallback:

    def test_zero_equity_falls_back(self):
        bal = {"equity": 0}
        p = build_dynamic_policy(bal)
        assert p["dynamic"] is False
        assert p["max_risk_per_trade"] == 1_000.0  # static default

    def test_missing_equity_falls_back(self):
        bal = {}
        p = build_dynamic_policy(bal)
        assert p["dynamic"] is False

    def test_negative_equity_falls_back(self):
        bal = {"equity": -5000}
        p = build_dynamic_policy(bal)
        assert p["dynamic"] is False

    def test_total_equity_field(self):
        """Should accept total_equity as an alternative to equity."""
        bal = {"total_equity": 80_000}
        p = build_dynamic_policy(bal)
        assert p["account_equity"] == 80_000
        assert p["dynamic"] is True


# ── _compute_max_contracts ───────────────────────────────────────────

class TestComputeMaxContracts:

    def test_50k_at_1pct(self):
        # $50K × 1% = $500 budget → 1 contract ($500/spread)
        assert _compute_max_contracts(50_000, 0.01) == 1

    def test_150k_at_1pct(self):
        # $150K × 1% = $1500 budget → 3 contracts
        assert _compute_max_contracts(150_000, 0.01) == 3

    def test_500k_at_1pct(self):
        # $500K × 1% = $5000 budget → 10 contracts (capped)
        assert _compute_max_contracts(500_000, 0.01) == 10

    def test_1M_at_1pct(self):
        # $1M × 1% = $10000 budget → 10 contracts (capped at max 10)
        assert _compute_max_contracts(1_000_000, 0.01) == 10

    def test_minimum_is_1(self):
        # Very small account → at least 1 contract
        assert _compute_max_contracts(10_000, 0.01) == 1
        assert _compute_max_contracts(5_000, 0.005) == 1

    def test_risk_off_reduces_contracts(self):
        # $150K × 1% × 0.70 = $1050 → 2 contracts
        assert _compute_max_contracts(150_000, 0.01 * 0.70) == 2


# ── Output shape ──────────────────────────────────────────────────────

class TestDynamicPolicyShape:

    def test_base_percentages_included(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal)
        assert "base_percentages" in p
        bp = p["base_percentages"]
        assert bp["max_trade_risk_pct"] == 0.01
        assert bp["max_total_risk_pct"] == 0.06

    def test_greek_targets_included(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal)
        assert "target_portfolio_delta_range" in p
        assert p["target_portfolio_delta_range"] == (-1.0, 1.0)
        assert "max_portfolio_delta_per_10k" in p

    def test_quality_gates_pass_through(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal)
        assert p["min_open_interest"] == 500
        assert p["min_volume"] == 50
        assert p["min_pop"] == 0.60
        assert p["min_return_on_risk"] == 0.10
        assert p["min_ev_to_risk"] == 0.02

    def test_concentration_pcts_included(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal)
        assert p["max_same_underlying_risk_pct"] == 0.30
        assert p["max_same_expiration_risk_pct"] == 0.25
        assert p["max_same_strategy_risk_pct"] == 0.40

    def test_suggested_max_contracts_in_output(self):
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal)
        assert "suggested_max_contracts" in p
        assert p["suggested_max_contracts"] == 2  # $100K × 1% = $1000 / $500 = 2

    def test_max_same_expiration_risk_scales(self):
        """max_same_expiration_risk should be 25% of max_risk_total."""
        bal = {"equity": 100_000}
        p = build_dynamic_policy(bal)
        assert p["max_same_expiration_risk"] == 0.25 * p["max_risk_total"]


# ── static_default_policy ─────────────────────────────────────────────

class TestStaticDefaultPolicy:

    def test_static_matches_instance(self):
        import tempfile
        svc = RiskPolicyService(Path(tempfile.mkdtemp()))
        assert svc.default_policy() == RiskPolicyService.static_default_policy()

    def test_has_dynamic_false(self):
        p = RiskPolicyService.static_default_policy()
        assert p["dynamic"] is False
