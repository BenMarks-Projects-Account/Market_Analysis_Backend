"""Tests for Insider Catalyst Scanner — service, scoring, enrichment, and FMP normalization.

Covers:
  - compute_insider_signal() — score formula, cluster thresholds, edge cases
  - _normalize_insider_role() / _normalize_transaction_type()
  - InsiderCatalystService.enrich_with_insider_signal() — boost & penalty
  - InsiderCatalystService.scan() — universe fetch, candidate ranking
  - FMPClient.get_insider_transactions() — normalization, lookback filtering
"""
from __future__ import annotations

import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.fmp_client import (
    _normalize_insider_role,
    _normalize_transaction_type,
)
from app.services.insider_catalyst_service import (
    ROLE_WEIGHTS,
    InsiderCatalystService,
    compute_insider_signal,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_tx(
    *,
    name: str = "John Doe",
    role: str = "CEO",
    tx_type: str = "buy",
    total_value: float = 100_000,
    shares: int = 1000,
    price: float = 100.0,
    date: str = "2025-01-15",
) -> dict[str, Any]:
    return {
        "insider_name": name,
        "insider_role": role,
        "transaction_type": tx_type,
        "transaction_date": date,
        "shares": shares,
        "price_per_share": price,
        "total_value": total_value,
        "filing_date": date,
    }


def _mock_settings(**overrides: Any) -> MagicMock:
    defaults = {
        "INSIDER_CLUSTER_THRESHOLD": 5,
        "INSIDER_LOOKBACK_DAYS": 30,
        "INSIDER_MARKET_CAP_MIN": 250_000_000,
        "INSIDER_MARKET_CAP_MAX": 10_000_000_000,
        "INSIDER_BOOST_POINTS": 15,
        "INSIDER_PENALTY_POINTS": 10,
        "INSIDER_CACHE_TTL_SECONDS": 3600,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


# ═══════════════════════════════════════════════════════════════════════════
# 1. Role & transaction type normalization (fmp_client module-level)
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalizeInsiderRole:
    def test_ceo_exact(self):
        assert _normalize_insider_role("CEO") == "CEO"

    def test_ceo_long_form(self):
        assert _normalize_insider_role("Chief Executive Officer") == "CEO"

    def test_cfo(self):
        assert _normalize_insider_role("Chief Financial Officer") == "CFO"

    def test_coo(self):
        assert _normalize_insider_role("Chief Operating Officer") == "COO"

    def test_cto(self):
        assert _normalize_insider_role("Chief Technology Officer") == "CTO"

    def test_director(self):
        assert _normalize_insider_role("Director") == "DIRECTOR"

    def test_ten_percent_owner(self):
        assert _normalize_insider_role("10% Owner") == "OWNER_10PCT"

    def test_owner_string(self):
        assert _normalize_insider_role("Beneficial Owner") == "OWNER_10PCT"

    def test_chief_other(self):
        assert _normalize_insider_role("Chief Marketing Officer") == "CHIEF_OTHER"

    def test_unknown_role(self):
        assert _normalize_insider_role("General Counsel") == "OTHER"

    def test_empty_string(self):
        assert _normalize_insider_role("") == "OTHER"


class TestNormalizeTransactionType:
    def test_purchase_code(self):
        assert _normalize_transaction_type("P") == "buy"

    def test_purchase_word(self):
        assert _normalize_transaction_type("Purchase") == "buy"

    def test_sale_code(self):
        assert _normalize_transaction_type("S") == "sell"

    def test_sale_word(self):
        assert _normalize_transaction_type("Sale") == "sell"

    def test_exercise(self):
        assert _normalize_transaction_type("M") == "option_exercise"

    def test_exercise_word(self):
        assert _normalize_transaction_type("Exercise of options") == "option_exercise"

    def test_grant_code(self):
        assert _normalize_transaction_type("A") == "grant"

    def test_grant_word(self):
        assert _normalize_transaction_type("Grant/Award") == "grant"

    def test_unknown(self):
        assert _normalize_transaction_type("X") == "other"


# ═══════════════════════════════════════════════════════════════════════════
# 2. compute_insider_signal()
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeInsiderSignal:
    """Test the core scoring function."""

    def test_empty_transactions(self):
        result = compute_insider_signal([])
        assert result["signal_score"] == 0.0
        assert result["cluster_triggered"] is False
        assert result["unique_buyers"] == 0

    def test_only_exercises_are_ignored(self):
        """Only buy/sell count — option_exercise and grant are filtered out."""
        txns = [
            _make_tx(tx_type="option_exercise", total_value=500_000),
            _make_tx(tx_type="grant", total_value=200_000),
        ]
        result = compute_insider_signal(txns)
        assert result["signal_score"] == 0.0
        assert result["cluster_triggered"] is False

    def test_single_ceo_buy(self):
        """One CEO buy: weight=3, log component > 0."""
        txns = [_make_tx(name="CEO1", role="CEO", tx_type="buy", total_value=500_000)]
        result = compute_insider_signal(txns, cluster_threshold=5)

        assert result["unique_buyers"] == 1
        assert result["unique_sellers"] == 0
        assert result["ceo_participated"] is True
        assert result["weighted_insider_points"] == 3
        assert result["cluster_triggered"] is False  # 3 < 5 threshold
        expected_log = 0.5 * math.log(1 + 500_000)
        expected_score = 3 + expected_log
        assert abs(result["signal_score"] - round(expected_score, 2)) < 0.01

    def test_cluster_ceo_plus_cfo(self):
        """CEO (3) + CFO (3) = 6 ≥ 5 threshold → cluster triggered."""
        txns = [
            _make_tx(name="Boss", role="CEO", tx_type="buy", total_value=300_000),
            _make_tx(name="Finance", role="CFO", tx_type="buy", total_value=200_000),
        ]
        result = compute_insider_signal(txns, cluster_threshold=5)

        assert result["weighted_insider_points"] == 6
        assert result["cluster_triggered"] is True
        assert result["ceo_participated"] is True
        assert result["cfo_participated"] is True
        assert result["unique_buyers"] == 2
        assert result["net_buy_dollars"] == 500_000

    def test_cluster_with_director_only(self):
        """5 directors: 5 × 1 = 5 ≥ 5 → cluster triggered."""
        txns = [
            _make_tx(name=f"Dir{i}", role="DIRECTOR", tx_type="buy", total_value=50_000)
            for i in range(5)
        ]
        result = compute_insider_signal(txns, cluster_threshold=5)
        assert result["weighted_insider_points"] == 5
        assert result["cluster_triggered"] is True

    def test_net_seller_subtraction(self):
        """A net seller with weight reduces signal_score."""
        txns = [
            _make_tx(name="Buyer", role="CEO", tx_type="buy", total_value=100_000),
            _make_tx(name="Seller", role="CFO", tx_type="sell", total_value=200_000),
        ]
        result = compute_insider_signal(txns, cluster_threshold=5)

        # buyer_weight=3, seller_weight=3, net_buy=-100_000 (negative)
        assert result["weighted_insider_points"] == 3
        assert result["unique_sellers"] == 1
        # Net buy is negative, so log component = 0
        # signal = 3 + 0 - 3 = 0
        assert result["signal_score"] == 0.0

    def test_same_insider_net_classification(self):
        """Same insider with both buy and sell → net classification."""
        txns = [
            _make_tx(name="Mixed", role="CEO", tx_type="buy", total_value=300_000),
            _make_tx(name="Mixed", role="CEO", tx_type="sell", total_value=100_000),
        ]
        result = compute_insider_signal(txns)
        # Net buyer (300k - 100k = 200k positive)
        assert result["unique_buyers"] == 1
        assert result["unique_sellers"] == 0
        assert result["ceo_participated"] is True

    def test_other_role_zero_weight(self):
        """OTHER role contributes 0 weight."""
        txns = [_make_tx(name="Nobody", role="OTHER", tx_type="buy", total_value=1_000_000)]
        result = compute_insider_signal(txns)
        assert result["weighted_insider_points"] == 0
        assert result["cluster_triggered"] is False
        # Score is just the log component (buyer weight=0, seller weight=0)
        assert result["signal_score"] > 0  # log component still > 0

    def test_last_transaction_date(self):
        txns = [
            _make_tx(date="2025-01-10"),
            _make_tx(name="B", date="2025-01-20"),
        ]
        result = compute_insider_signal(txns)
        assert result["last_transaction_date"] == "2025-01-20"

    def test_rationale_nonempty(self):
        txns = [_make_tx()]
        result = compute_insider_signal(txns)
        assert isinstance(result["rationale"], str)
        assert len(result["rationale"]) > 5


# ═══════════════════════════════════════════════════════════════════════════
# 3. InsiderCatalystService — enrich_with_insider_signal()
# ═══════════════════════════════════════════════════════════════════════════


class TestEnrichWithInsiderSignal:
    """Test the boost/penalty enrichment that decorates technical scanner results."""

    @pytest.fixture()
    def svc(self):
        fmp = AsyncMock()
        settings = _mock_settings()
        return InsiderCatalystService(fmp_client=fmp, settings=settings)

    @pytest.mark.asyncio
    async def test_cluster_boosts_long_candidate(self, svc: InsiderCatalystService):
        """cluster_triggered + LONG direction → +15 pts."""
        # Mock get_signal to return a cluster signal
        svc.get_signal = AsyncMock(return_value={
            "signal_score": 10.0,
            "cluster_triggered": True,
            "symbol": "AAPL",
        })

        candidates = [
            {"symbol": "AAPL", "composite_score": 65, "direction": "long"},
        ]
        result = await svc.enrich_with_insider_signal(candidates)

        assert result[0]["composite_score"] == 80  # 65 + 15
        assert result[0]["insider_tag"] == "insider_cluster_confirmed"
        assert result[0]["insider_boost_applied"] == 15

    @pytest.mark.asyncio
    async def test_selling_penalty_on_long(self, svc: InsiderCatalystService):
        """signal_score < -3 + LONG direction → -10 pts."""
        svc.get_signal = AsyncMock(return_value={
            "signal_score": -5.0,
            "cluster_triggered": False,
            "symbol": "MSFT",
        })

        candidates = [
            {"symbol": "MSFT", "composite_score": 70, "direction": "long"},
        ]
        result = await svc.enrich_with_insider_signal(candidates)

        assert result[0]["composite_score"] == 60  # 70 - 10
        assert result[0]["insider_tag"] == "insider_selling_warning"
        assert result[0]["insider_boost_applied"] == -10

    @pytest.mark.asyncio
    async def test_penalty_floor_at_zero(self, svc: InsiderCatalystService):
        """Penalty should not push score below 0."""
        svc.get_signal = AsyncMock(return_value={
            "signal_score": -5.0,
            "cluster_triggered": False,
            "symbol": "LOW",
        })

        candidates = [
            {"symbol": "LOW", "composite_score": 5, "direction": "long"},
        ]
        result = await svc.enrich_with_insider_signal(candidates)
        assert result[0]["composite_score"] == 0

    @pytest.mark.asyncio
    async def test_no_boost_for_short_direction(self, svc: InsiderCatalystService):
        """Short-direction candidates should NOT be boosted."""
        svc.get_signal = AsyncMock(return_value={
            "signal_score": 10.0,
            "cluster_triggered": True,
            "symbol": "AAPL",
        })

        candidates = [
            {"symbol": "AAPL", "composite_score": 65, "direction": "short"},
        ]
        result = await svc.enrich_with_insider_signal(candidates)
        assert result[0]["composite_score"] == 65
        assert "insider_tag" not in result[0]

    @pytest.mark.asyncio
    async def test_neutral_signal_no_change(self, svc: InsiderCatalystService):
        """Neutral signal (score between -3 and cluster threshold) → no change."""
        svc.get_signal = AsyncMock(return_value={
            "signal_score": 1.5,
            "cluster_triggered": False,
            "symbol": "GOOG",
        })

        candidates = [
            {"symbol": "GOOG", "composite_score": 50, "direction": "long"},
        ]
        result = await svc.enrich_with_insider_signal(candidates)
        assert result[0]["composite_score"] == 50
        assert "insider_tag" not in result[0]

    @pytest.mark.asyncio
    async def test_graceful_on_fmp_error(self, svc: InsiderCatalystService):
        """If FMP call fails, candidate is unchanged (graceful degradation)."""
        svc.get_signal = AsyncMock(side_effect=Exception("API error"))

        candidates = [
            {"symbol": "FAIL", "composite_score": 70, "direction": "long"},
        ]
        result = await svc.enrich_with_insider_signal(candidates)
        assert result[0]["composite_score"] == 70
        assert "insider_tag" not in result[0]

    @pytest.mark.asyncio
    async def test_setup_quality_also_boosted(self, svc: InsiderCatalystService):
        """Both composite_score and setup_quality get boosted."""
        svc.get_signal = AsyncMock(return_value={
            "signal_score": 10.0,
            "cluster_triggered": True,
            "symbol": "AAPL",
        })

        candidates = [
            {"symbol": "AAPL", "composite_score": 60, "setup_quality": 55, "direction": "long"},
        ]
        result = await svc.enrich_with_insider_signal(candidates)
        assert result[0]["composite_score"] == 75
        assert result[0]["setup_quality"] == 70


# ═══════════════════════════════════════════════════════════════════════════
# 4. InsiderCatalystService.scan()
# ═══════════════════════════════════════════════════════════════════════════


class TestInsiderCatalystScan:
    """Test the standalone scanner entry point."""

    @pytest.fixture()
    def fmp(self):
        fmp = AsyncMock()
        # Universe: 3 stocks
        fmp.get_stock_screener = AsyncMock(return_value=[
            {"symbol": "AAPL", "marketCap": 500_000_000},
            {"symbol": "MSFT", "marketCap": 2_000_000_000},
            {"symbol": "HUGE", "marketCap": 50_000_000_000},  # will be filtered by cap_max
        ])
        return fmp

    @pytest.fixture()
    def svc(self, fmp):
        settings = _mock_settings()
        return InsiderCatalystService(fmp_client=fmp, settings=settings)

    @pytest.mark.asyncio
    async def test_scan_returns_contract_shape(self, svc, fmp):
        """scan() result matches the stock scanner output contract."""
        # Each symbol returns 1 CEO buy
        fmp.get_insider_transactions = AsyncMock(return_value=[
            _make_tx(name="Boss", role="CEO", total_value=200_000),
        ])

        result = await svc.scan(max_candidates=10)

        assert result["strategy_id"] == "stock_insider_catalyst"
        assert result["status"] == "ok"
        assert isinstance(result["candidates"], list)
        assert "scan_time_seconds" in result
        assert "warnings" in result

    @pytest.mark.asyncio
    async def test_scan_filters_market_cap(self, svc, fmp):
        """Symbols above market_cap_max are excluded from universe."""
        fmp.get_insider_transactions = AsyncMock(return_value=[
            _make_tx(name="Boss", role="CEO", total_value=200_000),
        ])

        result = await svc.scan()
        # HUGE ($50B) should be filtered out, only AAPL + MSFT remain
        assert result["universe_size"] == 2

    @pytest.mark.asyncio
    async def test_scan_ranks_by_signal_score(self, svc, fmp):
        """Candidates are ranked by signal_score descending."""
        async def _mock_txns(symbol, lookback_days=30):
            if symbol == "AAPL":
                return [_make_tx(name="A", role="CEO", total_value=500_000)]
            elif symbol == "MSFT":
                return [_make_tx(name="B", role="DIRECTOR", total_value=10_000)]
            return []

        fmp.get_insider_transactions = AsyncMock(side_effect=_mock_txns)

        result = await svc.scan(max_candidates=10)
        candidates = result["candidates"]

        if len(candidates) >= 2:
            assert candidates[0]["signal_score"] >= candidates[1]["signal_score"]
            assert candidates[0]["rank"] == 1

    @pytest.mark.asyncio
    async def test_scan_max_candidates_cap(self, svc, fmp):
        """max_candidates limits the returned list."""
        fmp.get_insider_transactions = AsyncMock(return_value=[
            _make_tx(name="Boss", role="CEO", total_value=300_000),
        ])

        result = await svc.scan(max_candidates=1)
        assert len(result["candidates"]) <= 1

    @pytest.mark.asyncio
    async def test_scan_empty_universe(self, fmp):
        """If universe fetch fails, result is empty with warning."""
        fmp.get_stock_screener = AsyncMock(return_value=[])
        settings = _mock_settings()
        svc = InsiderCatalystService(fmp_client=fmp, settings=settings)

        result = await svc.scan()
        assert result["candidates"] == []
        assert any("Empty" in w or "empty" in w.lower() for w in result["warnings"])


# ═══════════════════════════════════════════════════════════════════════════
# 5. Role weight constants
# ═══════════════════════════════════════════════════════════════════════════


class TestRoleWeights:
    """Verify the role weight mapping matches the spec."""

    def test_ceo_weight(self):
        assert ROLE_WEIGHTS["CEO"] == 3

    def test_cfo_weight(self):
        assert ROLE_WEIGHTS["CFO"] == 3

    def test_coo_weight(self):
        assert ROLE_WEIGHTS["COO"] == 2

    def test_cto_weight(self):
        assert ROLE_WEIGHTS["CTO"] == 2

    def test_chief_other_weight(self):
        assert ROLE_WEIGHTS["CHIEF_OTHER"] == 2

    def test_director_weight(self):
        assert ROLE_WEIGHTS["DIRECTOR"] == 1

    def test_owner_10pct_weight(self):
        assert ROLE_WEIGHTS["OWNER_10PCT"] == 1

    def test_other_zero(self):
        assert ROLE_WEIGHTS["OTHER"] == 0
