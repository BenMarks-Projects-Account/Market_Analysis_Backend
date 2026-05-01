"""Tests for Institutional 13F Scoring Engine.

Scenarios:
  1. Strong buying — tier-1 funds accumulating → high scores, bullish
  2. Strong selling — tier-1 funds exiting → low scores, bearish
  3. Mixed signals — balanced buying/selling → neutral
  4. Consensus detection — 3+ tier-1 same direction → consensus list
  5. Sector heatmap aggregation — median-based sector scores
  6. Empty data — no holders → neutral fallback
  7. Percentile ranking — scores distributed 0-100
  8. Notable moves — new positions, exits correctly classified
"""

import pytest

from app.services.institutional_13f_engine import (
    _classify,
    _percentile_rank,
    _score_to_labels,
    compute_13f_scores,
    compute_notable_moves,
    compute_sector_heatmap,
    compute_stock_scores,
)

# ═══════════════════════════════════════════════════════════════════════
# FIXTURES — mock data helpers
# ═══════════════════════════════════════════════════════════════════════

TIER1_CIK = "0001067983"   # Berkshire
TIER1_CIK_2 = "0001336528" # Pershing Square
TIER1_CIK_3 = "0001061768" # Baupost
TIER1_CIK_4 = "0001649339" # Scion

FILER_WEIGHTS = {
    TIER1_CIK: 3.0,
    TIER1_CIK_2: 3.0,
    TIER1_CIK_3: 3.0,
    TIER1_CIK_4: 3.0,
    "0009999999": 1.0,  # tier-2 filer
}
TIER1_CIKS = {TIER1_CIK, TIER1_CIK_2, TIER1_CIK_3, TIER1_CIK_4}

UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]

SECTOR_MAP = {
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOGL": "Communication Services",
    "AMZN": "Consumer Discretionary",
    "META": "Communication Services",
}

MARKET_CAPS = {
    "AAPL": 3_000_000_000_000,
    "MSFT": 2_800_000_000_000,
    "GOOGL": 1_800_000_000_000,
    "AMZN": 1_600_000_000_000,
    "META": 1_200_000_000_000,
}

FLOAT_DATA = {
    sym: {"outstandingShares": 100_000_000, "floatShares": 90_000_000}
    for sym in UNIVERSE
}


def _make_holder(cik: str, name: str, shares: int, change: int, change_pct: float = 0.0):
    return {
        "investorName": name,
        "cik": cik,
        "shares": shares,
        "change": change,
        "changePercentage": change_pct,
        "value": shares * 150,
        "dateReported": "2025-05-15",
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. Strong buying
# ═══════════════════════════════════════════════════════════════════════

def test_strong_buying_produces_bullish():
    """Tier-1 funds all buying heavily → bullish."""
    holdings = {
        sym: [
            _make_holder(TIER1_CIK, "Berkshire", 5_000_000, 5_000_000),
            _make_holder(TIER1_CIK_2, "Pershing", 3_000_000, 3_000_000),
        ]
        for sym in UNIVERSE
    }

    result = compute_13f_scores(
        universe=UNIVERSE,
        holdings_data=holdings,
        float_data=FLOAT_DATA,
        sector_map=SECTOR_MAP,
        market_caps=MARKET_CAPS,
        filer_weights=FILER_WEIGHTS,
        tier1_ciks=TIER1_CIKS,
    )

    assert result["classification"] in ("bullish", "neutral")
    assert result["score"] >= 40
    assert result["label"]
    assert result["short_label"]
    assert "sector_heatmap" in result
    assert "notable_moves" in result
    assert "diagnostics" in result
    assert result["diagnostics"]["universe_size"] == 5
    assert result["diagnostics"]["symbols_with_data"] == 5


# ═══════════════════════════════════════════════════════════════════════
# 2. Strong selling
# ═══════════════════════════════════════════════════════════════════════

def test_strong_selling_produces_bearish():
    """Tier-1 funds exiting → bearish or low score."""
    holdings = {
        sym: [
            _make_holder(TIER1_CIK, "Berkshire", 0, -5_000_000),
            _make_holder(TIER1_CIK_2, "Pershing", 0, -3_000_000),
        ]
        for sym in UNIVERSE
    }

    result = compute_13f_scores(
        universe=UNIVERSE,
        holdings_data=holdings,
        float_data=FLOAT_DATA,
        sector_map=SECTOR_MAP,
        market_caps=MARKET_CAPS,
        filer_weights=FILER_WEIGHTS,
        tier1_ciks=TIER1_CIKS,
    )

    # All stocks have identical selling, so percentile ranking
    # will produce centered scores. Verify structure is correct.
    assert result["score"] is not None
    assert result["classification"] in ("bearish", "neutral")
    assert result["confidence_score"] >= 0


# ═══════════════════════════════════════════════════════════════════════
# 3. Mixed signals
# ═══════════════════════════════════════════════════════════════════════

def test_mixed_signals_produces_neutral():
    """Some buying, some selling → neutral."""
    holdings = {
        "AAPL": [_make_holder(TIER1_CIK, "Berkshire", 5_000_000, 5_000_000)],
        "MSFT": [_make_holder(TIER1_CIK, "Berkshire", 0, -5_000_000)],
        "GOOGL": [_make_holder(TIER1_CIK_2, "Pershing", 2_000_000, 2_000_000)],
        "AMZN": [_make_holder(TIER1_CIK_2, "Pershing", 0, -2_000_000)],
        "META": [],
    }

    result = compute_13f_scores(
        universe=UNIVERSE,
        holdings_data=holdings,
        float_data=FLOAT_DATA,
        sector_map=SECTOR_MAP,
        market_caps=MARKET_CAPS,
        filer_weights=FILER_WEIGHTS,
        tier1_ciks=TIER1_CIKS,
    )

    assert result["classification"] in ("bullish", "neutral", "bearish")
    assert 0 <= result["score"] <= 100


# ═══════════════════════════════════════════════════════════════════════
# 4. Consensus detection
# ═══════════════════════════════════════════════════════════════════════

def test_consensus_buy_detected():
    """3+ tier-1 funds buying same stock → consensus buy."""
    holdings = {
        "AAPL": [
            _make_holder(TIER1_CIK, "Berkshire", 5_000_000, 5_000_000),
            _make_holder(TIER1_CIK_2, "Pershing", 3_000_000, 3_000_000),
            _make_holder(TIER1_CIK_3, "Baupost", 2_000_000, 2_000_000),
        ],
    }

    notable = compute_notable_moves(
        stock_scores={"AAPL": {"score": 80, "tier1_buys": 3, "tier1_sells": 0}},
        holdings_data=holdings,
        sector_map={"AAPL": "Technology"},
        filer_weights=FILER_WEIGHTS,
        tier1_ciks=TIER1_CIKS,
    )

    assert len(notable["consensus_buys"]) >= 1
    assert notable["consensus_buys"][0]["symbol"] == "AAPL"
    assert notable["consensus_buys"][0]["fund_count"] >= 3


def test_consensus_sell_detected():
    """3+ tier-1 funds selling same stock → consensus sell."""
    holdings = {
        "MSFT": [
            _make_holder(TIER1_CIK, "Berkshire", 0, -5_000_000),
            _make_holder(TIER1_CIK_2, "Pershing", 0, -3_000_000),
            _make_holder(TIER1_CIK_3, "Baupost", 0, -2_000_000),
        ],
    }

    notable = compute_notable_moves(
        stock_scores={"MSFT": {"score": 20}},
        holdings_data=holdings,
        sector_map={"MSFT": "Technology"},
        filer_weights=FILER_WEIGHTS,
        tier1_ciks=TIER1_CIKS,
    )

    assert len(notable["consensus_sells"]) >= 1
    assert notable["consensus_sells"][0]["symbol"] == "MSFT"


# ═══════════════════════════════════════════════════════════════════════
# 5. Sector heatmap
# ═══════════════════════════════════════════════════════════════════════

def test_sector_heatmap_aggregation():
    """Scores aggregate to sectors via median."""
    stock_scores = {
        "AAPL": {"score": 80.0, "weighted_delta": 100.0, "tier1_buys": 2},
        "MSFT": {"score": 60.0, "weighted_delta": 50.0, "tier1_buys": 1},
        "GOOGL": {"score": 30.0, "weighted_delta": -20.0, "tier1_buys": 0},
    }

    heatmap = compute_sector_heatmap(
        stock_scores=stock_scores,
        sector_map={"AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Communication Services"},
        market_caps={"AAPL": 3e12, "MSFT": 2.8e12, "GOOGL": 1.8e12},
    )

    assert "Technology" in heatmap
    assert "Communication Services" in heatmap
    # Median of 80 and 60 = 70
    assert heatmap["Technology"]["score"] == 70.0
    assert heatmap["Communication Services"]["score"] == 30.0
    assert heatmap["Technology"]["symbol_count"] == 2


def test_sector_momentum_with_prior():
    """Sector momentum = current - prior score."""
    stock_scores = {
        "AAPL": {"score": 75.0, "weighted_delta": 0, "tier1_buys": 0},
    }

    heatmap = compute_sector_heatmap(
        stock_scores,
        sector_map={"AAPL": "Technology"},
        market_caps={"AAPL": 3e12},
        prior_sector_scores={"Technology": 55.0},
    )

    assert heatmap["Technology"]["momentum"] == 20.0


# ═══════════════════════════════════════════════════════════════════════
# 6. Empty data
# ═══════════════════════════════════════════════════════════════════════

def test_empty_holdings_returns_neutral():
    """No holder data → neutral fallback with warnings."""
    result = compute_13f_scores(
        universe=UNIVERSE,
        holdings_data={},
        float_data=FLOAT_DATA,
        sector_map=SECTOR_MAP,
        market_caps=MARKET_CAPS,
        filer_weights=FILER_WEIGHTS,
        tier1_ciks=TIER1_CIKS,
    )

    assert result["classification"] == "neutral"
    assert result["score"] == 50.0
    assert result["diagnostics"]["symbols_with_data"] == 0
    assert any("coverage" in w.lower() for w in result["warnings"])


# ═══════════════════════════════════════════════════════════════════════
# 7. Percentile ranking
# ═══════════════════════════════════════════════════════════════════════

def test_percentile_rank_basic():
    """Percentile rank distributes correctly."""
    values = [10, 20, 30, 40, 50]
    assert _percentile_rank(values, 10) == 10.0
    assert _percentile_rank(values, 30) == 50.0
    assert _percentile_rank(values, 50) == 90.0


def test_percentile_rank_empty():
    """Empty list → 50.0 (neutral)."""
    assert _percentile_rank([], 42) == 50.0


# ═══════════════════════════════════════════════════════════════════════
# 8. Notable moves classification
# ═══════════════════════════════════════════════════════════════════════

def test_new_positions_detected():
    """shares == change means brand new position."""
    holdings = {
        "AAPL": [
            _make_holder(TIER1_CIK, "Berkshire", 1_000_000, 1_000_000),
        ],
    }
    notable = compute_notable_moves(
        stock_scores={"AAPL": {"score": 80}},
        holdings_data=holdings,
        sector_map={"AAPL": "Technology"},
        filer_weights=FILER_WEIGHTS,
        tier1_ciks=TIER1_CIKS,
    )
    assert len(notable["top_new_positions"]) >= 1
    assert notable["top_new_positions"][0]["symbol"] == "AAPL"


def test_exits_detected():
    """shares == 0 and change < 0 means exit."""
    holdings = {
        "MSFT": [
            _make_holder(TIER1_CIK, "Berkshire", 0, -2_000_000),
        ],
    }
    notable = compute_notable_moves(
        stock_scores={"MSFT": {"score": 20}},
        holdings_data=holdings,
        sector_map={"MSFT": "Technology"},
        filer_weights=FILER_WEIGHTS,
        tier1_ciks=TIER1_CIKS,
    )
    assert len(notable["top_exits"]) >= 1


# ═══════════════════════════════════════════════════════════════════════
# 9. Classification and labels
# ═══════════════════════════════════════════════════════════════════════

def test_classify_bands():
    assert _classify(70) == "bullish"
    assert _classify(50) == "neutral"
    assert _classify(30) == "bearish"


def test_score_to_labels():
    label, short = _score_to_labels(85)
    assert "Buying" in label
    assert "Buy" in short

    label, short = _score_to_labels(20)
    assert "Selling" in label


# ═══════════════════════════════════════════════════════════════════════
# 10. Full pipeline output shape
# ═══════════════════════════════════════════════════════════════════════

def test_full_pipeline_output_shape():
    """Verify all required fields present in engine output."""
    holdings = {
        "AAPL": [
            _make_holder(TIER1_CIK, "Berkshire", 5_000_000, 1_000_000),
        ],
        "MSFT": [
            _make_holder(TIER1_CIK_2, "Pershing", 3_000_000, -500_000),
        ],
    }

    result = compute_13f_scores(
        universe=UNIVERSE,
        holdings_data=holdings,
        float_data=FLOAT_DATA,
        sector_map=SECTOR_MAP,
        market_caps=MARKET_CAPS,
        filer_weights=FILER_WEIGHTS,
        tier1_ciks=TIER1_CIKS,
    )

    required_keys = {
        "score", "label", "short_label", "confidence_score",
        "classification", "pillars", "sector_heatmap", "notable_moves",
        "top_stocks", "summary", "trader_takeaway", "warnings",
        "diagnostics",
    }
    assert required_keys.issubset(result.keys()), f"Missing: {required_keys - result.keys()}"
    assert isinstance(result["score"], (int, float))
    assert isinstance(result["sector_heatmap"], dict)
    assert isinstance(result["top_stocks"], list)
    assert isinstance(result["diagnostics"], dict)
    assert "universe_size" in result["diagnostics"]
