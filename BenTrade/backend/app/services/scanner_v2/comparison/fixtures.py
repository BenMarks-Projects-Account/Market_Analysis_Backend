"""Pre-built scanner snapshot fixtures for comparison testing.

Each fixture function returns a ``ComparisonSnapshot`` with synthetic
but realistic option chain data.  Use these for deterministic
harness testing without live market data.

Fixture naming convention:
    ``fixture_{symbol}_{scenario}``

Scenarios:
- ``golden_put_spread``   — clean 4-contract chain for 2 valid put credit spreads
- ``bad_liquidity``       — chain with missing OI / zero volume
- ``wide_spreads``        — chain with wide bid-ask spreads (inverted on one leg)
- ``empty_chain``         — chain with no options

Usage in tests::

    from app.services.scanner_v2.comparison.fixtures import (
        fixture_spy_golden_put_spread,
    )

    snapshot = fixture_spy_golden_put_spread()
    report = compare_from_results(
        scanner_key="put_credit_spread",
        snapshot=snapshot,
        legacy_result=...,
        v2_result=...,
    )
"""

from __future__ import annotations

from app.services.scanner_v2.comparison.contracts import ComparisonSnapshot
from app.services.scanner_v2.comparison.snapshots import (
    build_snapshot,
    build_synthetic_chain,
)


# ── Golden: 2 valid put credit spread candidates ────────────────────

def fixture_spy_golden_put_spread() -> ComparisonSnapshot:
    """Clean 4-put chain yielding 2 obvious put credit spreads.

    Spreads:
    - 590/585 (5-wide): short 590 put, long 585 put
    - 585/580 (5-wide): short 585 put, long 580 put

    All quotes valid, delta/IV present, OI/volume adequate.
    This is the "everything works" baseline.
    """
    chain = build_synthetic_chain(
        symbol="SPY",
        underlying_price=595.50,
        expiration="2026-03-20",
        put_strikes=[
            {
                "strike": 590.0,
                "bid": 1.50, "ask": 1.65,
                "delta": -0.30, "iv": 0.22,
                "oi": 5000, "volume": 800,
            },
            {
                "strike": 585.0,
                "bid": 0.65, "ask": 0.80,
                "delta": -0.18, "iv": 0.21,
                "oi": 3000, "volume": 450,
            },
            {
                "strike": 580.0,
                "bid": 0.25, "ask": 0.35,
                "delta": -0.10, "iv": 0.20,
                "oi": 2000, "volume": 300,
            },
            {
                "strike": 575.0,
                "bid": 0.10, "ask": 0.18,
                "delta": -0.05, "iv": 0.19,
                "oi": 1500, "volume": 200,
            },
        ],
    )

    return build_snapshot(
        snapshot_id="spy_golden_put_spread_2026-03-20",
        symbol="SPY",
        underlying_price=595.50,
        chain=chain,
        description=(
            "Clean 4-put chain for 2 valid put credit spreads. "
            "All quotes valid, greeks present, good liquidity."
        ),
        tags=["golden", "put_credit_spread", "vertical_spreads"],
        metadata={"dte": 9, "scenario": "known_good"},
    )


# ── Bad liquidity: missing OI and zero volume ──────────────────────

def fixture_spy_bad_liquidity() -> ComparisonSnapshot:
    """Chain with liquidity issues — missing OI, zero volume.

    The 590/585 spread has valid quotes but:
    - 590 put: OI = None (missing)
    - 585 put: volume = 0

    This tests whether the scanner properly flags liquidity issues
    without silently dropping candidates.
    """
    chain = build_synthetic_chain(
        symbol="SPY",
        underlying_price=595.50,
        expiration="2026-03-20",
        put_strikes=[
            {
                "strike": 590.0,
                "bid": 1.50, "ask": 1.65,
                "delta": -0.30, "iv": 0.22,
                "oi": None, "volume": 800,   # Missing OI
            },
            {
                "strike": 585.0,
                "bid": 0.65, "ask": 0.80,
                "delta": -0.18, "iv": 0.21,
                "oi": 3000, "volume": 0,      # Zero volume
            },
        ],
    )

    return build_snapshot(
        snapshot_id="spy_bad_liquidity_2026-03-20",
        symbol="SPY",
        underlying_price=595.50,
        chain=chain,
        description=(
            "Chain with missing OI and zero volume. "
            "Tests liquidity flagging without silent drops."
        ),
        tags=["bad_liquidity", "put_credit_spread", "data_quality"],
        metadata={"dte": 9, "scenario": "known_bad_liquidity"},
    )


# ── Wide / inverted bid-ask ─────────────────────────────────────────

def fixture_spy_wide_spreads() -> ComparisonSnapshot:
    """Chain with wide bid-ask and one inverted quote.

    - 590 put: normal but wide spread (bid=1.00, ask=2.00)
    - 585 put: inverted (bid=1.00, ask=0.50) — should be caught

    Tests whether inverted quotes are properly detected.
    """
    chain = build_synthetic_chain(
        symbol="SPY",
        underlying_price=595.50,
        expiration="2026-03-20",
        put_strikes=[
            {
                "strike": 590.0,
                "bid": 1.00, "ask": 2.00,
                "delta": -0.30, "iv": 0.22,
                "oi": 5000, "volume": 800,
            },
            {
                "strike": 585.0,
                "bid": 1.00, "ask": 0.50,    # Inverted!
                "delta": -0.18, "iv": 0.21,
                "oi": 3000, "volume": 450,
            },
        ],
    )

    return build_snapshot(
        snapshot_id="spy_wide_spreads_2026-03-20",
        symbol="SPY",
        underlying_price=595.50,
        chain=chain,
        description=(
            "Chain with wide bid-ask and one inverted quote. "
            "Tests inverted-quote detection."
        ),
        tags=["wide_spread", "inverted_quote", "data_quality"],
        metadata={"dte": 9, "scenario": "known_bad_quotes"},
    )


# ── Empty chain ─────────────────────────────────────────────────────

def fixture_spy_empty_chain() -> ComparisonSnapshot:
    """Chain with no options at all.

    Both legacy and V2 should produce zero candidates.
    Tests graceful handling of empty inputs.
    """
    chain = {"options": {"option": []}}

    return build_snapshot(
        snapshot_id="spy_empty_chain",
        symbol="SPY",
        underlying_price=595.50,
        chain=chain,
        description="Empty chain with no options. Tests graceful empty handling.",
        tags=["empty", "edge_case"],
        metadata={"dte": 0, "scenario": "empty"},
    )


# ── Iron condor: 4-leg golden scenario ──────────────────────────────

def fixture_spy_golden_iron_condor() -> ComparisonSnapshot:
    """8-option chain (4 puts + 4 calls) for a valid iron condor.

    IC structure: buy 575 put, sell 580 put, sell 610 call, buy 615 call
    """
    chain = build_synthetic_chain(
        symbol="SPY",
        underlying_price=595.50,
        expiration="2026-03-20",
        put_strikes=[
            {
                "strike": 575.0,
                "bid": 0.10, "ask": 0.18,
                "delta": -0.05, "iv": 0.19,
                "oi": 1500, "volume": 200,
            },
            {
                "strike": 580.0,
                "bid": 0.30, "ask": 0.42,
                "delta": -0.12, "iv": 0.20,
                "oi": 2000, "volume": 350,
            },
        ],
        call_strikes=[
            {
                "strike": 610.0,
                "bid": 0.35, "ask": 0.48,
                "delta": 0.12, "iv": 0.20,
                "oi": 2500, "volume": 400,
            },
            {
                "strike": 615.0,
                "bid": 0.12, "ask": 0.22,
                "delta": 0.05, "iv": 0.19,
                "oi": 1800, "volume": 250,
            },
        ],
    )

    return build_snapshot(
        snapshot_id="spy_golden_iron_condor_2026-03-20",
        symbol="SPY",
        underlying_price=595.50,
        chain=chain,
        description=(
            "Clean chain for a valid iron condor. "
            "4 puts + 4 calls with proper structure."
        ),
        tags=["golden", "iron_condor"],
        metadata={"dte": 9, "scenario": "known_good"},
    )
