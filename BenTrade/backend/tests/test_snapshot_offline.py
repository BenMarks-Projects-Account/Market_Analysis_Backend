"""Tests for manifest-based snapshot capture, offline replay, and fail-closed guard.

Covers:
  - SnapshotManifest completeness validation
  - SnapshotCaptureService captures all required artifacts
  - ManifestSnapshotSource loads from manifest
  - ManifestSnapshotSource replays chains, quotes, history, VIX correctly
  - OfflineLiveCallGuard blocks live provider methods
  - Round-trip: capture → offline replay → assertion of no live calls
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.snapshot_manifest import (
    CompletenessInfo,
    MarketContext,
    ScanConfig,
    SnapshotManifest,
    SymbolArtifacts,
)
from app.utils.snapshot_offline import (
    ManifestSnapshotSource,
    OfflineLiveCallError,
    OfflineLiveCallGuard,
    SnapshotDataMissing,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures: realistic option chain + quote data
# ---------------------------------------------------------------------------

GOLDEN_CHAIN: list[dict[str, Any]] = [
    {
        "symbol": "SPY260320P00500000",
        "option_type": "put",
        "strike": 500.0,
        "expiration_date": "2026-03-20",
        "bid": 1.23,
        "ask": 1.45,
        "last": 1.34,
        "volume": 1200,
        "open_interest": 5400,
        "greeks": {"delta": -0.25, "gamma": 0.03, "theta": -0.05, "smv_vol": 0.18},
    },
    {
        "symbol": "SPY260320C00510000",
        "option_type": "call",
        "strike": 510.0,
        "expiration_date": "2026-03-20",
        "bid": 2.10,
        "ask": 2.30,
        "last": 2.20,
        "volume": 800,
        "open_interest": 3200,
        "greeks": {"delta": 0.55, "gamma": 0.02, "theta": -0.04, "smv_vol": 0.17},
    },
]

GOLDEN_QUOTE: dict[str, Any] = {
    "symbol": "SPY",
    "last": 505.42,
    "bid": 505.40,
    "ask": 505.44,
    "close": 504.80,
    "change": 0.62,
    "volume": 52340000,
}

GOLDEN_HISTORY: list[dict[str, Any]] = [
    {"date": "2026-03-01", "close": 502.10},
    {"date": "2026-03-02", "close": 504.80},
    {"date": "2026-03-03", "close": 505.42},
]


# ---------------------------------------------------------------------------
# Test: SnapshotManifest completeness validation
# ---------------------------------------------------------------------------

class TestManifestCompleteness:
    def test_complete_manifest(self):
        """A manifest with all required artifacts should be complete."""
        m = SnapshotManifest(
            trace_id="abc123",
            created_at="2026-03-03T12:00:00Z",
            strategy_id="credit_spread",
            symbols=["SPY"],
            symbol_artifacts={
                "SPY": SymbolArtifacts(
                    symbol="SPY",
                    underlying_quote="SPY/underlying_quote.json",
                    prices_history="SPY/prices_history.json",
                    option_chains={"2026-03-20": "SPY/option_chain_2026-03-20.json"},
                ),
            },
            market_context_path="market_context.json",
        )
        result = m.validate_completeness()
        assert result.required_artifacts_present is True
        assert result.missing_artifacts == []

    def test_missing_underlying_quote(self):
        """Missing underlying_quote should be reported."""
        m = SnapshotManifest(
            trace_id="abc123",
            created_at="2026-03-03T12:00:00Z",
            strategy_id="credit_spread",
            symbols=["SPY"],
            symbol_artifacts={
                "SPY": SymbolArtifacts(
                    symbol="SPY",
                    prices_history="SPY/prices_history.json",
                    option_chains={"2026-03-20": "SPY/option_chain_2026-03-20.json"},
                ),
            },
            market_context_path="market_context.json",
        )
        result = m.validate_completeness()
        assert result.required_artifacts_present is False
        assert any("underlying_quote" in m for m in result.missing_artifacts)

    def test_missing_prices_history(self):
        """Missing prices_history should be reported."""
        m = SnapshotManifest(
            trace_id="abc123",
            created_at="2026-03-03T12:00:00Z",
            strategy_id="credit_spread",
            symbols=["SPY"],
            symbol_artifacts={
                "SPY": SymbolArtifacts(
                    symbol="SPY",
                    underlying_quote="SPY/underlying_quote.json",
                    option_chains={"2026-03-20": "SPY/option_chain_2026-03-20.json"},
                ),
            },
            market_context_path="market_context.json",
        )
        result = m.validate_completeness()
        assert result.required_artifacts_present is False
        assert any("prices_history" in m for m in result.missing_artifacts)

    def test_missing_symbol_artifacts(self):
        """A symbol with no artifacts at all should be reported."""
        m = SnapshotManifest(
            trace_id="abc123",
            created_at="2026-03-03T12:00:00Z",
            strategy_id="credit_spread",
            symbols=["SPY", "QQQ"],
            symbol_artifacts={
                "SPY": SymbolArtifacts(
                    symbol="SPY",
                    underlying_quote="SPY/underlying_quote.json",
                    prices_history="SPY/prices_history.json",
                    option_chains={"2026-03-20": "SPY/option_chain_2026-03-20.json"},
                ),
            },
            market_context_path="market_context.json",
        )
        result = m.validate_completeness()
        assert result.required_artifacts_present is False
        assert any("QQQ" in m for m in result.missing_artifacts)

    def test_missing_market_context(self):
        """Missing market_context should be reported."""
        m = SnapshotManifest(
            trace_id="abc123",
            created_at="2026-03-03T12:00:00Z",
            strategy_id="credit_spread",
            symbols=["SPY"],
            symbol_artifacts={
                "SPY": SymbolArtifacts(
                    symbol="SPY",
                    underlying_quote="SPY/underlying_quote.json",
                    prices_history="SPY/prices_history.json",
                    option_chains={"2026-03-20": "SPY/option_chain_2026-03-20.json"},
                ),
            },
        )
        result = m.validate_completeness()
        assert result.required_artifacts_present is False
        assert any("market_context" in m for m in result.missing_artifacts)


# ---------------------------------------------------------------------------
# Test: ManifestSnapshotSource loading
# ---------------------------------------------------------------------------

def _write_snapshot(tmp_path: Path) -> Path:
    """Write a minimal complete snapshot to tmp_path, return run_dir."""
    run_dir = tmp_path / "tradier" / "20260303" / "credit_spread" / "abc123"
    run_dir.mkdir(parents=True)

    # Symbol directory
    sym_dir = run_dir / "SPY"
    sym_dir.mkdir()

    # Underlying quote
    (sym_dir / "underlying_quote.json").write_text(json.dumps({
        "meta": {"provider": "tradier", "symbol": "SPY", "timestamp": "2026-03-03T12:00:00Z"},
        "quote": GOLDEN_QUOTE,
    }))

    # Prices history
    closes = [b["close"] for b in GOLDEN_HISTORY]
    (sym_dir / "prices_history.json").write_text(json.dumps({
        "meta": {"provider": "tradier", "symbol": "SPY", "bar_count": len(GOLDEN_HISTORY)},
        "bars": GOLDEN_HISTORY,
        "closes": closes,
    }))

    # Option chain
    (sym_dir / "option_chain_2026-03-20.json").write_text(json.dumps({
        "meta": {
            "provider": "tradier",
            "symbol": "SPY",
            "expiration": "2026-03-20",
            "underlying_price": 505.42,
        },
        "raw": GOLDEN_CHAIN,
    }))

    # Market context
    (run_dir / "market_context.json").write_text(json.dumps({
        "vix": 18.5,
        "regime_label": "NEUTRAL",
        "captured_at": "2026-03-03T12:00:00Z",
    }))

    # Manifest
    manifest = SnapshotManifest(
        trace_id="abc123",
        created_at="2026-03-03T12:00:00Z",
        strategy_id="credit_spread",
        symbols=["SPY"],
        symbol_artifacts={
            "SPY": SymbolArtifacts(
                symbol="SPY",
                underlying_quote="SPY/underlying_quote.json",
                prices_history="SPY/prices_history.json",
                option_chains={"2026-03-20": "SPY/option_chain_2026-03-20.json"},
            ),
        },
        market_context_path="market_context.json",
    )
    (run_dir / "snapshot_manifest.json").write_text(
        json.dumps(manifest.model_dump(), indent=2),
    )

    return run_dir


class TestManifestSnapshotSource:
    def test_load_from_manifest_path(self, tmp_path: Path):
        """Load a ManifestSnapshotSource from a manifest file."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        assert source.trace_id == "abc123"
        assert source.manifest.strategy_id == "credit_spread"

    def test_get_chain(self, tmp_path: Path):
        """Chain data is loaded correctly from the snapshot."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        chain = _run(source.get_chain("SPY", "2026-03-20"))
        assert len(chain) == 2
        assert chain[0]["strike"] == 500.0
        assert chain[0]["bid"] == 1.23

    def test_get_underlying_price(self, tmp_path: Path):
        """Underlying price is read from the quote file."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        price = source.get_underlying_price("SPY")
        assert price == 505.42

    def test_get_prices_history(self, tmp_path: Path):
        """Prices history is loaded from the snapshot."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        closes = source.get_prices_history("SPY")
        assert len(closes) == 3
        assert closes[-1] == 505.42

    def test_get_vix(self, tmp_path: Path):
        """VIX value is loaded from market context."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        vix = source.get_vix()
        assert vix == 18.5

    def test_get_available_expirations(self, tmp_path: Path):
        """Available expirations match what's in the manifest."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        exps = source.get_available_expirations("SPY")
        assert exps == ["2026-03-20"]

    def test_missing_chain_raises(self, tmp_path: Path):
        """Requesting a non-existent expiration raises SnapshotDataMissing."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        with pytest.raises(SnapshotDataMissing):
            _run(source.get_chain("SPY", "2026-04-17"))

    def test_missing_symbol_raises(self, tmp_path: Path):
        """Requesting a non-existent symbol raises SnapshotDataMissing."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        with pytest.raises(SnapshotDataMissing):
            _run(source.get_chain("QQQ", "2026-03-20"))

    def test_from_trace_id(self, tmp_path: Path):
        """Load snapshot by trace_id search."""
        _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_trace_id(tmp_path, "abc123")
        assert source.trace_id == "abc123"

    def test_from_latest(self, tmp_path: Path):
        """Load latest snapshot."""
        _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_latest(
            tmp_path, strategy_id="credit_spread",
        )
        assert source.trace_id == "abc123"

    def test_chain_caching(self, tmp_path: Path):
        """Second get_chain call returns cached data."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        chain1 = _run(source.get_chain("SPY", "2026-03-20"))
        chain2 = _run(source.get_chain("SPY", "2026-03-20"))
        assert chain1 is chain2  # Same object from cache

    def test_tradier_envelope_extraction(self, tmp_path: Path):
        """Chain stored as a Tradier envelope is properly extracted."""
        run_dir = _write_snapshot(tmp_path)
        # Overwrite chain with Tradier envelope format
        (run_dir / "SPY" / "option_chain_2026-03-20.json").write_text(json.dumps({
            "meta": {"provider": "tradier"},
            "raw": {"options": {"option": GOLDEN_CHAIN}},
        }))
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        chain = _run(source.get_chain("SPY", "2026-03-20"))
        assert len(chain) == 2

    def test_check_staleness_always_fresh(self, tmp_path: Path):
        """Manifest snapshots are never stale (intentional capture)."""
        run_dir = _write_snapshot(tmp_path)
        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        result = source.check_staleness("SPY", "2026-03-20")
        assert result["stale"] is False

    def test_underlying_price_fallback_to_chain_meta(self, tmp_path: Path):
        """If quote file is missing, price falls back to chain meta."""
        run_dir = _write_snapshot(tmp_path)
        # Remove quote file
        (run_dir / "SPY" / "underlying_quote.json").unlink()
        # Update manifest
        raw = json.loads((run_dir / "snapshot_manifest.json").read_text())
        raw["symbol_artifacts"]["SPY"]["underlying_quote"] = None
        (run_dir / "snapshot_manifest.json").write_text(json.dumps(raw))

        source = ManifestSnapshotSource.from_manifest_path(
            run_dir / "snapshot_manifest.json",
        )
        price = source.get_underlying_price("SPY")
        assert price == 505.42  # From chain meta


# ---------------------------------------------------------------------------
# Test: OfflineLiveCallGuard
# ---------------------------------------------------------------------------

class TestOfflineLiveCallGuard:
    def test_blocks_tradier_get_chain(self):
        """Guard raises OfflineLiveCallError for Tradier get_chain."""
        bds = MagicMock()
        bds.tradier_client = MagicMock()
        bds.tradier_client.get_chain = AsyncMock(return_value=[])
        bds.finnhub_client = MagicMock()
        bds.polygon_client = MagicMock()
        bds.fred_client = MagicMock()

        with OfflineLiveCallGuard(bds):
            with pytest.raises(OfflineLiveCallError):
                _run(bds.tradier_client.get_chain("SPY", "2026-03-20"))

    def test_blocks_fred_call(self):
        """Guard raises OfflineLiveCallError for FRED call."""
        bds = MagicMock()
        bds.tradier_client = MagicMock()
        bds.finnhub_client = MagicMock()
        bds.polygon_client = MagicMock()
        bds.fred_client = MagicMock()
        bds.fred_client.get_latest_series_value = AsyncMock(return_value=18.0)

        with OfflineLiveCallGuard(bds):
            with pytest.raises(OfflineLiveCallError):
                _run(bds.fred_client.get_latest_series_value())

    def test_restores_methods_after_exit(self):
        """Guard restores original methods after context exit."""
        bds = MagicMock()
        bds.tradier_client = MagicMock()
        original_method = bds.tradier_client.get_chain
        bds.finnhub_client = MagicMock()
        bds.polygon_client = MagicMock()
        bds.fred_client = MagicMock()

        with OfflineLiveCallGuard(bds):
            pass  # Guard is active then exits

        assert bds.tradier_client.get_chain is original_method

    def test_restores_on_exception(self):
        """Guard restores methods even when an exception occurs inside."""
        bds = MagicMock()
        bds.tradier_client = MagicMock()
        original = bds.tradier_client.get_quote
        bds.finnhub_client = MagicMock()
        bds.polygon_client = MagicMock()
        bds.fred_client = MagicMock()

        try:
            with OfflineLiveCallGuard(bds):
                raise ValueError("test error")
        except ValueError:
            pass

        assert bds.tradier_client.get_quote is original


# ---------------------------------------------------------------------------
# Test: SnapshotCaptureService (integration-ish)
# ---------------------------------------------------------------------------

class TestSnapshotCaptureService:
    def test_capture_produces_complete_manifest(self, tmp_path: Path):
        """Capture service produces a complete manifest with all artifacts."""
        from app.services.snapshot_capture_service import SnapshotCaptureService

        # Mock the providers
        tradier_mock = AsyncMock()
        tradier_mock.get_quote = AsyncMock(return_value=GOLDEN_QUOTE)
        tradier_mock.get_expirations = AsyncMock(return_value=["2026-03-20"])
        tradier_mock.fetch_chain_raw_payload = AsyncMock(return_value=GOLDEN_CHAIN)

        bds_mock = AsyncMock()
        bds_mock.get_underlying_price = AsyncMock(return_value=505.42)
        bds_mock.get_prices_history_dated = AsyncMock(return_value=GOLDEN_HISTORY)

        fred_mock = AsyncMock()
        fred_mock.get_latest_series_value = AsyncMock(return_value=18.5)

        service = SnapshotCaptureService(
            base_data_service=bds_mock,
            tradier_client=tradier_mock,
            fred_client=fred_mock,
            snapshot_dir=tmp_path,
        )

        manifest = _run(service.capture(
            strategy_id="credit_spread",
            symbols=["SPY"],
            preset_name="balanced",
            dte_min=1,
            dte_max=90,
        ))

        assert manifest.trace_id
        assert manifest.strategy_id == "credit_spread"
        assert "SPY" in manifest.symbols
        assert manifest.chains_captured >= 1
        assert manifest.completeness.required_artifacts_present is True
        assert manifest.completeness.missing_artifacts == []

        # Verify files exist on disk
        run_dir = SnapshotCaptureService.find_snapshot_by_trace_id(
            tmp_path, manifest.trace_id,
        )
        assert run_dir is not None
        assert (run_dir / "snapshot_manifest.json").is_file()
        assert (run_dir / "market_context.json").is_file()
        assert (run_dir / "scan_config.json").is_file()
        assert (run_dir / "SPY" / "underlying_quote.json").is_file()
        assert (run_dir / "SPY" / "prices_history.json").is_file()
        assert (run_dir / "SPY" / "option_chain_2026-03-20.json").is_file()

    def test_list_and_find_snapshots(self, tmp_path: Path):
        """Snapshots can be listed and found by trace_id."""
        from app.services.snapshot_capture_service import SnapshotCaptureService

        tradier_mock = AsyncMock()
        tradier_mock.get_quote = AsyncMock(return_value=GOLDEN_QUOTE)
        tradier_mock.get_expirations = AsyncMock(return_value=["2026-03-20"])
        tradier_mock.fetch_chain_raw_payload = AsyncMock(return_value=GOLDEN_CHAIN)

        bds_mock = AsyncMock()
        bds_mock.get_underlying_price = AsyncMock(return_value=505.42)
        bds_mock.get_prices_history_dated = AsyncMock(return_value=GOLDEN_HISTORY)

        fred_mock = AsyncMock()
        fred_mock.get_latest_series_value = AsyncMock(return_value=18.5)

        service = SnapshotCaptureService(
            base_data_service=bds_mock,
            tradier_client=tradier_mock,
            fred_client=fred_mock,
            snapshot_dir=tmp_path,
        )

        manifest = _run(service.capture(
            strategy_id="credit_spread",
            symbols=["SPY"],
            dte_min=1,
            dte_max=90,
        ))

        # List
        results = SnapshotCaptureService.list_snapshots(tmp_path)
        assert len(results) >= 1
        assert results[0]["trace_id"] == manifest.trace_id

        # Find by trace_id
        found = SnapshotCaptureService.find_snapshot_by_trace_id(
            tmp_path, manifest.trace_id,
        )
        assert found is not None


# ---------------------------------------------------------------------------
# Test: Round-trip capture → offline replay
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_capture_then_replay(self, tmp_path: Path):
        """Data captured by the service can be replayed from ManifestSnapshotSource."""
        from app.services.snapshot_capture_service import SnapshotCaptureService

        tradier_mock = AsyncMock()
        tradier_mock.get_quote = AsyncMock(return_value=GOLDEN_QUOTE)
        tradier_mock.get_expirations = AsyncMock(return_value=["2026-03-20"])
        tradier_mock.fetch_chain_raw_payload = AsyncMock(return_value=GOLDEN_CHAIN)

        bds_mock = AsyncMock()
        bds_mock.get_underlying_price = AsyncMock(return_value=505.42)
        bds_mock.get_prices_history_dated = AsyncMock(return_value=GOLDEN_HISTORY)

        fred_mock = AsyncMock()
        fred_mock.get_latest_series_value = AsyncMock(return_value=18.5)

        service = SnapshotCaptureService(
            base_data_service=bds_mock,
            tradier_client=tradier_mock,
            fred_client=fred_mock,
            snapshot_dir=tmp_path,
        )

        manifest = _run(service.capture(
            strategy_id="credit_spread",
            symbols=["SPY"],
            dte_min=1,
            dte_max=90,
        ))

        # Load from manifest
        source = ManifestSnapshotSource.from_trace_id(
            tmp_path, manifest.trace_id,
        )

        # Replay all data
        chain = _run(source.get_chain("SPY", "2026-03-20"))
        assert len(chain) == 2
        assert chain[0]["strike"] == 500.0

        price = source.get_underlying_price("SPY")
        assert price == 505.42

        closes = source.get_prices_history("SPY")
        assert len(closes) == 3

        vix = source.get_vix()
        assert vix == 18.5

        exps = source.get_available_expirations("SPY")
        assert "2026-03-20" in exps

    def test_offline_replay_no_live_calls(self, tmp_path: Path):
        """When using ManifestSnapshotSource with guard, NO live calls occur."""
        from app.services.snapshot_capture_service import SnapshotCaptureService

        tradier_mock = AsyncMock()
        tradier_mock.get_quote = AsyncMock(return_value=GOLDEN_QUOTE)
        tradier_mock.get_expirations = AsyncMock(return_value=["2026-03-20"])
        tradier_mock.fetch_chain_raw_payload = AsyncMock(return_value=GOLDEN_CHAIN)

        bds_mock = MagicMock()
        bds_mock.get_underlying_price = AsyncMock(return_value=505.42)
        bds_mock.get_prices_history_dated = AsyncMock(return_value=GOLDEN_HISTORY)

        fred_mock = AsyncMock()
        fred_mock.get_latest_series_value = AsyncMock(return_value=18.5)

        service = SnapshotCaptureService(
            base_data_service=bds_mock,
            tradier_client=tradier_mock,
            fred_client=fred_mock,
            snapshot_dir=tmp_path,
        )

        manifest = _run(service.capture(
            strategy_id="credit_spread",
            symbols=["SPY"],
            dte_min=1,
            dte_max=90,
        ))

        # Set up a mock BDS with real-looking clients
        replay_bds = MagicMock()
        replay_bds.tradier_client = MagicMock()
        replay_bds.tradier_client.get_chain = AsyncMock()
        replay_bds.tradier_client.get_quote = AsyncMock()
        replay_bds.tradier_client.get_expirations = AsyncMock()
        replay_bds.tradier_client.get_daily_closes = AsyncMock()
        replay_bds.finnhub_client = MagicMock()
        replay_bds.polygon_client = MagicMock()
        replay_bds.fred_client = MagicMock()
        replay_bds.fred_client.get_latest_series_value = AsyncMock()

        source = ManifestSnapshotSource.from_trace_id(
            tmp_path, manifest.trace_id,
        )

        with OfflineLiveCallGuard(replay_bds):
            # These should work — they go through the snapshot source
            chain = _run(source.get_chain("SPY", "2026-03-20"))
            assert len(chain) == 2

            price = source.get_underlying_price("SPY")
            assert price is not None

            vix = source.get_vix()
            assert vix is not None

            # These should FAIL — they try to use live providers
            with pytest.raises(OfflineLiveCallError):
                _run(replay_bds.tradier_client.get_chain("SPY", "2026-03-20"))

            with pytest.raises(OfflineLiveCallError):
                _run(replay_bds.fred_client.get_latest_series_value())
