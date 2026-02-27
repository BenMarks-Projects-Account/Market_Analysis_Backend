"""Unit tests for the quote smoke test and OCC symbol validation.

Tests cover:
1. TradierClient._normalize_option_symbol — valid/invalid OCC symbols
2. TradierClient.get_option_quotes — mock API interaction
3. StrategyService._quote_smoke_test — chain census, diagnosis logic
4. Pipeline abort on broken quote pipeline
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    """Python 3.14-safe helper to run a coroutine synchronously."""
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────
# OCC Symbol Validation
# ──────────────────────────────────────────────────────────────────────────

class TestNormalizeOptionSymbol:
    """TradierClient._normalize_option_symbol must accept valid OCC symbols
    and reject invalid ones."""

    @pytest.fixture()
    def validate(self):
        from app.clients.tradier_client import TradierClient
        return TradierClient._normalize_option_symbol

    @pytest.mark.parametrize("symbol,expected", [
        # Valid OCC symbols
        ("SPY260320P00500000", "SPY260320P00500000"),
        ("QQQ260320C00450000", "QQQ260320C00450000"),
        ("IWM260417P00200000", "IWM260417P00200000"),
        ("X260320P00025000", "X260320P00025000"),      # 1-char root
        ("MSFT260320C00400000", "MSFT260320C00400000"),  # 4-char root
        ("GOOGL260320P01500000", "GOOGL260320P01500000"),  # 5-char root
        ("BRKB26260320C00500000", None),  # >6 char root → invalid
        # Case normalization
        ("spy260320p00500000", "SPY260320P00500000"),
    ])
    def test_valid_invalid(self, validate, symbol, expected):
        assert validate(symbol) == expected

    def test_rejects_equity_symbol(self, validate):
        """Equity symbols (1-10 chars, no date/PC/strike) must be rejected."""
        assert validate("SPY") is None

    def test_rejects_empty(self, validate):
        assert validate("") is None
        assert validate(None) is None

    def test_rejects_random_garbage(self, validate):
        assert validate("NOTAVALID!!") is None
        assert validate("123456789012345678") is None


# ──────────────────────────────────────────────────────────────────────────
# get_option_quotes
# ──────────────────────────────────────────────────────────────────────────

class TestGetOptionQuotes:
    """TradierClient.get_option_quotes fetches quotes for OCC symbols."""

    @pytest.fixture()
    def client(self):
        from app.clients.tradier_client import TradierClient
        from app.config import Settings
        from app.utils.cache import TTLCache

        settings = MagicMock(spec=Settings)
        settings.TRADIER_TOKEN = "test-token"
        settings.TRADIER_BASE_URL = "https://sandbox.tradier.com/v1"
        settings.QUOTE_CACHE_TTL_SECONDS = 0  # disable caching
        http_client = AsyncMock()
        cache = TTLCache()
        return TradierClient(settings, http_client, cache)

    def test_rejects_equity_symbols(self, client):
        """get_option_quotes should reject non-OCC symbols gracefully."""
        result = _run(client.get_option_quotes(["SPY", "QQQ"]))
        assert result == {}

    def test_accepts_occ_symbols(self, client):
        """get_option_quotes should pass OCC symbols to the API."""
        async def _test():
            with patch("app.clients.tradier_client.request_json", new_callable=AsyncMock) as mock_rj:
                mock_rj.return_value = {
                    "quotes": {
                        "quote": [
                            {
                                "symbol": "SPY260320P00500000",
                                "bid": 2.50,
                                "ask": 2.70,
                                "last": 2.60,
                            }
                        ]
                    }
                }
                result = await client.get_option_quotes(["SPY260320P00500000"])
                assert "SPY260320P00500000" in result
                assert result["SPY260320P00500000"]["bid"] == 2.50
                assert result["SPY260320P00500000"]["ask"] == 2.70

        _run(_test())

    def test_returns_empty_for_empty_input(self, client):
        result = _run(client.get_option_quotes([]))
        assert result == {}


# ──────────────────────────────────────────────────────────────────────────
# Quote Smoke Test
# ──────────────────────────────────────────────────────────────────────────

def _make_contract(**kwargs: Any) -> SimpleNamespace:
    """Build a minimal OptionContract-like object for smoke test tests."""
    defaults = dict(
        strike=500.0, option_type="put", bid=2.50, ask=2.70,
        delta=-0.15, iv=0.22, open_interest=5000, volume=300,
        symbol="SPY260320P00500000", expiration="2026-03-20",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _build_service(tradier_client=None):
    """Build a minimal StrategyService with mocked dependencies."""
    from pathlib import Path
    from app.services.strategy_service import StrategyService

    bds = MagicMock()
    bds.tradier_client = tradier_client or MagicMock()
    results_dir = Path(__file__).parent / "_test_results_smoke"
    results_dir.mkdir(exist_ok=True)
    return StrategyService(
        base_data_service=bds,
        results_dir=results_dir,
    )


class TestQuoteSmokeTest:
    """StrategyService._quote_smoke_test chain census and diagnosis."""

    def test_no_snapshots(self):
        svc = _build_service()
        result = _run(svc._quote_smoke_test([], "iron_condor"))
        assert result["diagnosis"] == "NO_SNAPSHOTS"

    def test_empty_chain(self):
        svc = _build_service()
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": []}
        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))
        assert result["diagnosis"] == "EMPTY_CHAIN"

    def test_chain_with_bid_ask_and_direct_ok(self):
        """When chain has bid/ask AND direct quote returns data → PIPELINE_OK."""
        tc = MagicMock()
        tc.get_option_quotes = AsyncMock(return_value={
            "SPY260320P00500000": {"bid": 2.50, "ask": 2.70, "last": 2.60},
            "SPY260320C00550000": {"bid": 1.20, "ask": 1.40, "last": 1.30},
        })
        svc = _build_service(tradier_client=tc)

        contracts = [
            _make_contract(option_type="put", strike=500, symbol="SPY260320P00500000"),
            _make_contract(option_type="call", strike=550, symbol="SPY260320C00550000"),
        ]
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts}

        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))
        assert result["diagnosis"] == "PIPELINE_OK"
        assert result["chain_quote_summary"]["has_both"] == 2
        assert result["chain_quote_summary"]["missing_bid"] == 0
        assert len(result["contract_probes"]) == 2

    def test_chain_no_bid_ask_direct_also_empty(self):
        """When chain has no bid/ask AND direct quote empty → NO_QUOTE_DATA_ANYWHERE."""
        tc = MagicMock()
        tc.get_option_quotes = AsyncMock(return_value={})
        svc = _build_service(tradier_client=tc)

        contracts = [
            _make_contract(option_type="put", bid=None, ask=None, symbol="SPY260320P00500000"),
            _make_contract(option_type="call", bid=None, ask=None, symbol="SPY260320C00550000"),
        ]
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts}

        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))
        assert result["diagnosis"] == "NO_QUOTE_DATA_ANYWHERE"
        assert result["chain_quote_summary"]["has_both"] == 0

    def test_chain_ok_direct_fails(self):
        """When chain has bid/ask but direct quote returns empty → expected."""
        tc = MagicMock()
        tc.get_option_quotes = AsyncMock(return_value={})
        svc = _build_service(tradier_client=tc)

        contracts = [
            _make_contract(option_type="put", bid=2.50, ask=2.70, symbol="SPY260320P00500000"),
            _make_contract(option_type="call", bid=1.20, ask=1.40, symbol="SPY260320C00550000"),
        ]
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts}

        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))
        assert result["diagnosis"] == "CHAIN_OK_BUT_DIRECT_QUOTE_FAILED"

    def test_chain_missing_but_direct_ok(self):
        """When chain has no bid/ask but direct API has data → normalize_chain bug."""
        tc = MagicMock()
        tc.get_option_quotes = AsyncMock(return_value={
            "SPY260320P00500000": {"bid": 2.50, "ask": 2.70, "last": 2.60},
        })
        svc = _build_service(tradier_client=tc)

        contracts = [
            _make_contract(option_type="put", bid=None, ask=None, symbol="SPY260320P00500000"),
            _make_contract(option_type="call", bid=None, ask=None, symbol="SPY260320C00550000"),
        ]
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts}

        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))
        assert result["diagnosis"] == "CHAIN_MISSING_QUOTES_BUT_DIRECT_OK"

    def test_direct_quote_api_error(self):
        """When the Tradier API throws an exception → QUOTE_API_ERROR."""
        tc = MagicMock()
        tc.get_option_quotes = AsyncMock(side_effect=Exception("Connection refused"))
        svc = _build_service(tradier_client=tc)

        contracts = [
            _make_contract(option_type="put", symbol="SPY260320P00500000"),
        ]
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts}

        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))
        assert result["diagnosis"] == "QUOTE_API_ERROR"
        assert "Connection refused" in result["quote_error_message"]

    def test_prefers_spy_snapshot(self):
        """Should pick SPY snapshot even if it's not first."""
        tc = MagicMock()
        tc.get_option_quotes = AsyncMock(return_value={})
        svc = _build_service(tradier_client=tc)

        contracts_qqq = [_make_contract(option_type="put", symbol="QQQ260320P00450000")]
        contracts_spy = [_make_contract(option_type="put", symbol="SPY260320P00500000")]

        snapshots = [
            {"symbol": "QQQ", "expiration": "2026-03-20", "contracts": contracts_qqq},
            {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts_spy},
        ]

        result = _run(svc._quote_smoke_test(snapshots, "iron_condor"))
        assert result["snapshot_symbol"] == "SPY"

    def test_no_occ_symbols_in_chain(self):
        """Contracts without OCC symbols → NO_OCC_SYMBOLS_IN_CHAIN."""
        svc = _build_service()
        contracts = [
            _make_contract(option_type="put", symbol="SHORT"),  # not OCC format
        ]
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts}

        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))
        assert result["diagnosis"] == "NO_OCC_SYMBOLS_IN_CHAIN"

    def test_chain_census_counts_zeros(self):
        """Chain census correctly counts zero-bid and zero-ask contracts."""
        tc = MagicMock()
        tc.get_option_quotes = AsyncMock(return_value={})
        svc = _build_service(tradier_client=tc)

        contracts = [
            _make_contract(option_type="put", bid=0.0, ask=0.05, symbol="SPY260320P00400000"),
            _make_contract(option_type="call", bid=1.50, ask=0.0, symbol="SPY260320C00600000"),
        ]
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts}

        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))
        cs = result["chain_quote_summary"]
        assert cs["zero_bid"] == 1
        assert cs["zero_ask"] == 1
        assert cs["has_both"] == 2  # both still set (even if 0)

    def test_result_has_required_fields(self):
        """Smoke test result should contain the structured JSON fields."""
        tc = MagicMock()
        tc.get_option_quotes = AsyncMock(return_value={
            "SPY260320P00500000": {"bid": 2.50, "ask": 2.70, "last": 2.60},
        })
        svc = _build_service(tradier_client=tc)

        contracts = [
            _make_contract(option_type="put", symbol="SPY260320P00500000"),
        ]
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts}

        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))

        # Required top-level fields
        assert "provider" in result
        assert "quote_endpoint" in result
        assert "timestamp" in result
        assert "diagnosis" in result
        assert "chain_quote_summary" in result
        assert "contract_probes" in result
        assert "direct_quote_results" in result
        assert "request_params" in result

        # Probe details
        probe = result["contract_probes"][0]
        assert "occ_symbol" in probe
        assert "chain_bid" in probe
        assert "chain_ask" in probe
        assert "strike" in probe

        # Direct quote result
        dqr = result["direct_quote_results"][0]
        assert "direct_bid" in dqr
        assert "direct_ask" in dqr
        assert "match_status" in dqr
        assert "chain_status" in dqr

    def test_no_tradier_client(self):
        """When base_data_service has no tradier_client → NO_TRADIER_CLIENT."""
        from pathlib import Path
        from app.services.strategy_service import StrategyService

        bds = MagicMock(spec=[])  # spec=[] means no attributes at all
        results_dir = Path(__file__).parent / "_test_results_smoke"
        results_dir.mkdir(exist_ok=True)
        svc = StrategyService(base_data_service=bds, results_dir=results_dir)

        contracts = [_make_contract(option_type="put", symbol="SPY260320P00500000")]
        snapshot = {"symbol": "SPY", "expiration": "2026-03-20", "contracts": contracts}

        result = _run(svc._quote_smoke_test([snapshot], "iron_condor"))
        assert result["diagnosis"] == "NO_TRADIER_CLIENT"


class TestQuoteSmokeTestAbort:
    """Verify the generate() pipeline notes when quote pipeline is broken."""

    def test_pipeline_broken_note_added(self):
        """When diagnosis=NO_QUOTE_DATA_ANYWHERE, a QUOTE_PIPELINE_BROKEN
        note should be appended to the report notes."""
        from pathlib import Path
        from app.services.strategy_service import StrategyService

        # Build a service with mocked everything
        bds = MagicMock()
        bds.tradier_client = MagicMock()
        bds.tradier_client.get_option_quotes = AsyncMock(return_value={})
        bds.get_source_health_snapshot = MagicMock(return_value={})
        bds.tradier_client.get_expirations = AsyncMock(return_value=["2026-03-20"])

        # Chain contracts with no bid/ask → will trigger NO_QUOTE_DATA_ANYWHERE
        bds.get_analysis_inputs = AsyncMock(return_value={
            "underlying_price": 500.0,
            "contracts": [
                _make_contract(option_type="put", bid=None, ask=None, symbol="SPY260320P00500000"),
                _make_contract(option_type="call", bid=None, ask=None, symbol="SPY260320C00550000"),
            ],
            "prices_history": [500.0] * 30,
            "vix": 18.0,
            "notes": [],
        })

        results_dir = Path(__file__).parent / "_test_results_smoke"
        results_dir.mkdir(exist_ok=True)
        svc = StrategyService(
            base_data_service=bds,
            results_dir=results_dir,
        )

        result = _run(svc.generate("iron_condor", {"symbols": ["SPY"]}))

        # Check that the QUOTE_PIPELINE_BROKEN note was added
        ft = result.get("filter_trace") or {}
        smoke = ft.get("quote_smoke_test") or {}
        assert smoke.get("diagnosis") == "NO_QUOTE_DATA_ANYWHERE"

        notes = result.get("diagnostics", {}).get("notes", [])
        pipe_notes = [n for n in notes if "QUOTE_PIPELINE_BROKEN" in n]
        assert len(pipe_notes) >= 1, f"Expected QUOTE_PIPELINE_BROKEN in notes: {notes}"
