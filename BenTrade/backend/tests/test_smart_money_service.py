"""Tests for smart money service — signal computation and synthesis.

Covers:
  - _current_13f_quarter() — quarter calculation
  - _classify_insider_role() — insider role mapping
  - _classify_transaction_type() — FMP transaction code mapping
  - _compute_insider_signals() — cluster detection, scoring
  - _compute_institutional_summary() — institutional metrics
  - _compute_congressional() — 180-day filtering
  - _generate_synthesis() — template output
  - get_smart_money_data() — main entry, parallel fetch, error resilience
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.smart_money_service import (
    _classify_insider_role,
    _classify_transaction_type,
    _compute_congressional,
    _compute_insider_signals,
    _compute_institutional_summary,
    _compute_mutual_fund_summary,
    _current_13f_quarter,
    _generate_synthesis,
    _prev_quarter,
    get_smart_money_data,
)


# ── Quarter helpers ────────────────────────────────────────────────────

class TestQuarterHelpers:
    def test_prev_quarter_q1(self):
        assert _prev_quarter(2026, 1) == (2025, 4)

    def test_prev_quarter_q2(self):
        assert _prev_quarter(2026, 2) == (2026, 1)

    def test_prev_quarter_q4(self):
        assert _prev_quarter(2025, 4) == (2025, 3)

    def test_current_13f_quarter_returns_tuple(self):
        year, quarter = _current_13f_quarter()
        assert isinstance(year, int)
        assert quarter in (1, 2, 3, 4)
        assert year >= 2024  # sanity


# ── Insider role classification ────────────────────────────────────────

class TestClassifyInsiderRole:
    def test_ceo(self):
        assert _classify_insider_role("Chief Executive Officer") == "ceo"

    def test_cfo(self):
        assert _classify_insider_role("CFO") == "cfo"

    def test_president(self):
        assert _classify_insider_role("President and COO") == "officer"

    def test_director(self):
        assert _classify_insider_role("Independent Director") == "director"

    def test_10pct_owner(self):
        assert _classify_insider_role("10% Owner") == "10pct_owner"

    def test_none(self):
        assert _classify_insider_role(None) == "other"

    def test_unknown(self):
        assert _classify_insider_role("Family Trust") == "other"


# ── Transaction type classification ────────────────────────────────────

class TestClassifyTransactionType:
    def test_purchase(self):
        assert _classify_transaction_type("P") == "buy"

    def test_sale(self):
        assert _classify_transaction_type("S") == "sell"

    def test_option_exercise(self):
        assert _classify_transaction_type("M") == "option_exercise"

    def test_award(self):
        assert _classify_transaction_type("A") == "grant"

    def test_none(self):
        assert _classify_transaction_type(None) == "unknown"


# ── Insider signals ────────────────────────────────────────────────────

def _make_insider_tx(name, tx_type, shares, price, days_ago, role="Officer"):
    tx_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "reportingName": name,
        "transactionDate": tx_date.strftime("%Y-%m-%d"),
        "transactionType": tx_type,
        "securitiesTransacted": shares,
        "price": price,
        "typeOfOwner": role,
    }


class TestComputeInsiderSignals:
    def test_empty_transactions(self):
        result = _compute_insider_signals([])
        assert result["signal"] == "neutral"
        assert result["score_contribution"] == 0
        assert result["transaction_table_90d"] == []

    def test_cluster_buying(self):
        txns = [
            _make_insider_tx("Alice", "P", 1000, 50, 10),
            _make_insider_tx("Bob", "P", 2000, 50, 15),
            _make_insider_tx("Charlie", "P", 500, 50, 20),
        ]
        result = _compute_insider_signals(txns)
        assert result["cluster_buy"] is True
        assert result["signal"] == "cluster_buying"
        assert result["score_contribution"] >= 15  # +15 cluster + possible +5 net

    def test_cluster_selling(self):
        txns = [
            _make_insider_tx("Alice", "S", 1000, 50, 5),
            _make_insider_tx("Bob", "S", 2000, 50, 10),
            _make_insider_tx("Charlie", "S", 5000, 50, 15),
        ]
        result = _compute_insider_signals(txns)
        assert result["cluster_sell"] is True
        assert result["signal"] == "cluster_selling"
        assert result["score_contribution"] <= -10

    def test_net_buying(self):
        txns = [
            _make_insider_tx("Alice", "P", 1000, 50, 30),
        ]
        result = _compute_insider_signals(txns)
        assert result["signal"] == "net_buying"
        assert result["net_value_90d"] > 0

    def test_officer_activity_tracked(self):
        txns = [
            _make_insider_tx("Jane CEO", "P", 5000, 100, 20, "Chief Executive Officer"),
        ]
        result = _compute_insider_signals(txns)
        assert len(result["officer_activity"]) == 1
        assert result["officer_activity"][0]["role"] == "ceo"


# ── Institutional summary ─────────────────────────────────────────────

class TestComputeInstitutionalSummary:
    def test_empty_data(self):
        result = _compute_institutional_summary(None, None, None, 2025, 4)
        assert result["total_pct"] is None
        assert result["holder_count"] is None
        assert result["score_contribution"] == 0

    def test_with_summary_and_holders(self):
        summary = [{
            "investorsHolding": 500,
            "ownershipPercent": 0.72,
            "totalInvested": 1_000_000,
            "lastTotalInvested": 900_000,
        }]
        holders = [
            {
                "investorName": f"Fund {i}",
                "shares": 100_000 - i * 1000,
                "changePercentage": 5.0 if i < 5 else -2.0,
                "weightInPortfolio": 0.02,
            }
            for i in range(25)
        ]
        float_data = [{"floatShares": 10_000_000}]

        result = _compute_institutional_summary(summary, holders, float_data, 2025, 4)
        assert result["total_pct"] == 72.0
        assert result["holder_count"] == 500
        assert result["net_flow_direction"] == "buying"
        assert result["score_contribution"] == 10
        assert len(result["top_holders"]) == 20
        assert result["top10_concentration_pct"] is not None

    def test_selling_flow_gives_negative_score(self):
        summary = [{
            "investorsHolding": 100,
            "ownershipPercent": 0.5,
            "totalInvested": 800_000,
            "lastTotalInvested": 900_000,
        }]
        result = _compute_institutional_summary(summary, None, None, 2025, 4)
        assert result["net_flow_direction"] == "selling"
        assert result["score_contribution"] == -10


# ── Congressional ──────────────────────────────────────────────────────

class TestComputeCongressional:
    def test_empty(self):
        result = _compute_congressional(None, None)
        assert result["trades"] == []
        assert result["total_count"] == 0

    def test_filters_old_trades(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
        recent_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        senate = [
            {"transactionDate": old_date, "firstName": "Old", "lastName": "Senator"},
            {"transactionDate": recent_date, "firstName": "New", "lastName": "Senator"},
        ]
        result = _compute_congressional(senate, None)
        assert result["total_count"] == 1
        assert result["trades"][0]["name"] == "New Senator"


# ── Mutual fund summary ───────────────────────────────────────────────

class TestComputeMutualFundSummary:
    def test_empty(self):
        result = _compute_mutual_fund_summary(None)
        assert result["holders"] == []

    def test_basic(self):
        data = [{"holderName": "Vanguard", "shares": 1_000_000, "value": 50_000_000, "change": 5000, "filingDate": "2025-12-31"}]
        result = _compute_mutual_fund_summary(data)
        assert len(result["holders"]) == 1
        assert result["holders"][0]["name"] == "Vanguard"


# ── Synthesis ──────────────────────────────────────────────────────────

class TestGenerateSynthesis:
    def test_produces_nonempty_string(self):
        inst = {"total_pct": 72.0, "holder_count": 500, "net_flow_direction": "buying", "top_holders": []}
        insider = {"signal": "cluster_buying", "cluster_buy_count": 3, "net_value_90d": 500_000, "officer_activity": []}
        congressional = {"total_count": 2}
        result = _generate_synthesis(inst, insider, congressional)
        assert isinstance(result, str)
        assert len(result) > 20
        assert "72.0%" in result
        assert "cluster" in result.lower()

    def test_handles_missing_data_gracefully(self):
        inst = {"total_pct": None, "top_holders": []}
        insider = {"signal": "neutral", "net_value_90d": 0, "officer_activity": []}
        congressional = {"total_count": 0}
        result = _generate_synthesis(inst, insider, congressional)
        assert "Limited" in result or "No significant" in result


# ── Main entry point ──────────────────────────────────────────────────

class TestGetSmartMoneyData:
    @pytest.mark.asyncio
    async def test_returns_full_payload(self):
        fmp = MagicMock()
        # Stub all 8 FMP methods
        fmp.get_institutional_holders = AsyncMock(return_value=[])
        fmp.get_institutional_positions_summary = AsyncMock(return_value=[])
        fmp.get_shares_float = AsyncMock(return_value=[])
        fmp.get_insider_trading_by_symbol = AsyncMock(return_value=[])
        fmp.get_insider_trade_statistics = AsyncMock(return_value=[])
        fmp.get_mutual_fund_holders = AsyncMock(return_value=[])
        fmp.get_senate_trades = AsyncMock(return_value=[])
        fmp.get_house_trades = AsyncMock(return_value=[])

        result = await get_smart_money_data(fmp, "AAPL")
        assert result["symbol"] == "AAPL"
        assert "_source" in result
        assert result["_source"] == "fmp"
        assert "institutional" in result
        assert "insider" in result
        assert "congressional" in result
        assert "mutual_funds" in result
        assert "synthesis" in result
        assert "score_contribution" in result

    @pytest.mark.asyncio
    async def test_handles_fmp_errors_gracefully(self):
        fmp = MagicMock()
        # All methods raise exceptions
        fmp.get_institutional_holders = AsyncMock(side_effect=Exception("API error"))
        fmp.get_institutional_positions_summary = AsyncMock(side_effect=Exception("API error"))
        fmp.get_shares_float = AsyncMock(side_effect=Exception("API error"))
        fmp.get_insider_trading_by_symbol = AsyncMock(side_effect=Exception("API error"))
        fmp.get_insider_trade_statistics = AsyncMock(side_effect=Exception("API error"))
        fmp.get_mutual_fund_holders = AsyncMock(side_effect=Exception("API error"))
        fmp.get_senate_trades = AsyncMock(side_effect=Exception("API error"))
        fmp.get_house_trades = AsyncMock(side_effect=Exception("API error"))

        # Should NOT raise — errors handled via return_exceptions=True
        result = await get_smart_money_data(fmp, "AAPL")
        assert result["symbol"] == "AAPL"
        assert result["insider"]["signal"] == "neutral"
