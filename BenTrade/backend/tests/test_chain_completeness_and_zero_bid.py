"""Tests for chain completeness warning and zero-bid short leg rejection.

Chain completeness: narrow_chain() warns when normalized contract count
falls below a symbol-specific threshold.

Zero-bid short leg: phase_d rejects credit-strategy candidates when
a short leg has bid=0 (no premium collectible).  Debit strategies
are exempt.
"""

from __future__ import annotations

import logging

import pytest

from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2Leg,
    V2RecomputedMath,
)
from app.services.scanner_v2.data.narrow import narrow_chain
from app.services.scanner_v2.diagnostics.reason_codes import (
    REJECT_ZERO_BID_SHORT_LEG,
)
from app.services.scanner_v2.phases import phase_d_quote_liquidity_sanity


# =====================================================================
#  Helpers
# =====================================================================

def _make_tradier_contract(
    strike: float,
    option_type: str = "put",
    expiration: str = "2026-04-17",
    bid: float = 2.00,
    ask: float = 2.20,
) -> dict:
    """Minimal Tradier-format contract dict."""
    return {
        "symbol": f"SPY260417P00{int(strike):03d}000",
        "root_symbol": "SPY",
        "option_type": option_type,
        "expiration_date": expiration,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "volume": 100,
        "open_interest": 5000,
        "greeks": {
            "delta": -0.30 if option_type == "put" else 0.30,
            "gamma": 0.01,
            "theta": -0.05,
            "vega": 0.10,
            "mid_iv": 0.20,
        },
    }


def _build_chain(n: int, symbol: str = "SPY") -> list[dict]:
    """Build a flat chain list with *n* valid contracts."""
    contracts = []
    for i in range(n):
        strike = 300.0 + i
        contracts.append(_make_tradier_contract(
            strike=strike,
            option_type="put" if i % 2 == 0 else "call",
        ))
    return contracts


def _make_candidate(
    *,
    scanner_key: str = "put_credit_spread",
    family_key: str = "vertical_spreads",
    legs: list[V2Leg] | None = None,
) -> V2Candidate:
    """Build a minimal V2Candidate for phase_d testing."""
    return V2Candidate(
        candidate_id="test|pcs|2026-04-01|380/375|0",
        scanner_key=scanner_key,
        strategy_id=scanner_key,
        family_key=family_key,
        symbol="SPY",
        underlying_price=400.0,
        expiration="2026-04-01",
        dte=30,
        legs=legs or [],
        math=V2RecomputedMath(),
    )


def _credit_legs(short_bid: float = 2.50) -> list[V2Leg]:
    """Two-leg put credit spread; short bid is configurable."""
    return [
        V2Leg(
            index=0, side="short", strike=380.0, option_type="put",
            expiration="2026-04-01",
            bid=short_bid, ask=2.60, mid=(short_bid + 2.60) / 2,
            delta=-0.30, gamma=0.01, theta=-0.05, vega=0.10,
            iv=0.20, open_interest=5000, volume=200,
        ),
        V2Leg(
            index=1, side="long", strike=375.0, option_type="put",
            expiration="2026-04-01",
            bid=1.80, ask=1.90, mid=1.85,
            delta=-0.22, gamma=0.01, theta=-0.04, vega=0.08,
            iv=0.21, open_interest=3000, volume=150,
        ),
    ]


# =====================================================================
#  Chain Completeness Warning Tests
# =====================================================================

class TestChainCompleteness:
    """narrow_chain() warns when contract count is below threshold."""

    def test_spy_low_count_triggers_warning(self):
        """SPY with 5 contracts (threshold 200) → warning."""
        chain = _build_chain(5)
        result = narrow_chain(chain, "SPY", 400.0, dte_min=0, dte_max=999)
        diag = result.diagnostics
        assert diag.chain_completeness_warning is True
        assert diag.chain_contract_count == 5
        assert diag.chain_expected_min == 200
        assert any("incomplete" in w.lower() for w in diag.warnings)

    def test_spy_normal_count_no_warning(self):
        """SPY with 250 contracts (threshold 200) → no warning."""
        chain = _build_chain(250)
        result = narrow_chain(chain, "SPY", 400.0, dte_min=0, dte_max=999)
        diag = result.diagnostics
        assert diag.chain_completeness_warning is False
        assert diag.chain_contract_count == 0  # not set when no warning

    def test_unknown_symbol_below_default(self):
        """Unknown symbol with 10 contracts (default threshold 50) → warning."""
        chain = _build_chain(10)
        result = narrow_chain(chain, "AAPL", 180.0, dte_min=0, dte_max=999)
        diag = result.diagnostics
        assert diag.chain_completeness_warning is True
        assert diag.chain_contract_count == 10
        assert diag.chain_expected_min == 50

    def test_unknown_symbol_above_default_no_warning(self):
        """Unknown symbol with 100 contracts (default 50) → no warning."""
        chain = _build_chain(100)
        result = narrow_chain(chain, "AAPL", 180.0, dte_min=0, dte_max=999)
        diag = result.diagnostics
        assert diag.chain_completeness_warning is False

    def test_iwm_threshold(self):
        """IWM threshold is 150."""
        chain = _build_chain(50)
        result = narrow_chain(chain, "IWM", 200.0, dte_min=0, dte_max=999)
        diag = result.diagnostics
        assert diag.chain_completeness_warning is True
        assert diag.chain_expected_min == 150

    def test_dia_threshold(self):
        """DIA threshold is 100."""
        chain = _build_chain(30)
        result = narrow_chain(chain, "DIA", 350.0, dte_min=0, dte_max=999)
        diag = result.diagnostics
        assert diag.chain_completeness_warning is True
        assert diag.chain_expected_min == 100

    def test_case_insensitive_symbol(self):
        """Symbol matching is case-insensitive."""
        chain = _build_chain(5)
        result = narrow_chain(chain, "spy", 400.0, dte_min=0, dte_max=999)
        diag = result.diagnostics
        assert diag.chain_completeness_warning is True
        assert diag.chain_expected_min == 200

    def test_warning_logged(self, caplog):
        """Warning is emitted via logging."""
        chain = _build_chain(3)
        with caplog.at_level(logging.WARNING, logger="bentrade.narrow_chain"):
            narrow_chain(chain, "SPY", 400.0, dte_min=0, dte_max=999)
        assert any("chain_possibly_incomplete" in r.message for r in caplog.records)

    def test_empty_chain_triggers_warning(self):
        """Empty chain (0 contracts) → warning."""
        result = narrow_chain([], "SPY", 400.0, dte_min=0, dte_max=999)
        diag = result.diagnostics
        assert diag.chain_completeness_warning is True
        assert diag.chain_contract_count == 0


# =====================================================================
#  Zero-Bid Short Leg Rejection Tests
# =====================================================================

class TestZeroBidShortLeg:
    """phase_d rejects credit candidates with zero-bid short legs."""

    def test_zero_bid_short_rejected(self):
        """put_credit_spread with short.bid=0 → rejected."""
        cand = _make_candidate(legs=_credit_legs(short_bid=0.0))
        [result] = phase_d_quote_liquidity_sanity([cand])
        reasons = result.diagnostics.reject_reasons
        assert REJECT_ZERO_BID_SHORT_LEG in reasons

    def test_normal_bid_short_passes(self):
        """put_credit_spread with short.bid=2.50 → no zero-bid rejection."""
        cand = _make_candidate(legs=_credit_legs(short_bid=2.50))
        [result] = phase_d_quote_liquidity_sanity([cand])
        reasons = result.diagnostics.reject_reasons
        assert REJECT_ZERO_BID_SHORT_LEG not in reasons

    def test_debit_strategy_exempt(self):
        """put_debit with short.bid=0 → NOT rejected (debit exempt)."""
        cand = _make_candidate(
            scanner_key="put_debit",
            family_key="vertical_spreads",
            legs=_credit_legs(short_bid=0.0),
        )
        [result] = phase_d_quote_liquidity_sanity([cand])
        reasons = result.diagnostics.reject_reasons
        assert REJECT_ZERO_BID_SHORT_LEG not in reasons

    def test_call_credit_spread_zero_bid_rejected(self):
        """call_credit_spread with short.bid=0 → rejected."""
        cand = _make_candidate(
            scanner_key="call_credit_spread",
            legs=_credit_legs(short_bid=0.0),
        )
        [result] = phase_d_quote_liquidity_sanity([cand])
        assert REJECT_ZERO_BID_SHORT_LEG in result.diagnostics.reject_reasons

    def test_iron_condor_zero_bid_short_rejected(self):
        """iron_condor with zero-bid short leg → rejected."""
        legs = [
            V2Leg(  # short put — zero bid
                index=0, side="short", strike=380.0, option_type="put",
                expiration="2026-04-01",
                bid=0.0, ask=0.10, mid=0.05,
                delta=-0.10, gamma=0.01, theta=-0.02, vega=0.05,
                iv=0.20, open_interest=2000, volume=100,
            ),
            V2Leg(  # long put
                index=1, side="long", strike=375.0, option_type="put",
                expiration="2026-04-01",
                bid=0.05, ask=0.15, mid=0.10,
                delta=-0.05, gamma=0.01, theta=-0.01, vega=0.03,
                iv=0.22, open_interest=1500, volume=80,
            ),
            V2Leg(  # short call
                index=2, side="short", strike=420.0, option_type="call",
                expiration="2026-04-01",
                bid=2.00, ask=2.20, mid=2.10,
                delta=0.25, gamma=0.01, theta=-0.04, vega=0.09,
                iv=0.19, open_interest=4000, volume=300,
            ),
            V2Leg(  # long call
                index=3, side="long", strike=425.0, option_type="call",
                expiration="2026-04-01",
                bid=1.50, ask=1.70, mid=1.60,
                delta=0.18, gamma=0.01, theta=-0.03, vega=0.07,
                iv=0.20, open_interest=3000, volume=200,
            ),
        ]
        cand = _make_candidate(
            scanner_key="iron_condor",
            family_key="iron_condors",
            legs=legs,
        )
        [result] = phase_d_quote_liquidity_sanity([cand])
        assert REJECT_ZERO_BID_SHORT_LEG in result.diagnostics.reject_reasons

    def test_reject_reason_message_includes_leg_info(self):
        """Rejection message includes leg index and bid value."""
        cand = _make_candidate(legs=_credit_legs(short_bid=0.0))
        [result] = phase_d_quote_liquidity_sanity([cand])
        items = result.diagnostics.items
        zbsl_items = [
            it for it in items
            if it.code == REJECT_ZERO_BID_SHORT_LEG
        ]
        assert len(zbsl_items) == 1
        assert "bid=0" in zbsl_items[0].message

    def test_already_rejected_candidate_skipped(self):
        """Candidate already rejected in Phase C is skipped entirely."""
        cand = _make_candidate(legs=_credit_legs(short_bid=0.0))
        # Simulate a pre-existing rejection
        cand.diagnostics.reject_reasons.append("v2_wrong_leg_count")
        [result] = phase_d_quote_liquidity_sanity([cand])
        # Should NOT add a second rejection
        assert REJECT_ZERO_BID_SHORT_LEG not in result.diagnostics.reject_reasons
