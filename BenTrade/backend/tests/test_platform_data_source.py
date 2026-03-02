"""Tests for runtime Platform Data Source toggle & supporting features.

Covers:
  A) PlatformSettings — load / save / persistence / env defaults
  B) SnapshotChainSource — staleness checking, prices_history, underlying derivation
  C) SnapshotRecorder — prices_history capture
  D) run_snapshot_cleanup — retention pruning
  E) StrategyService.generate() — data_source_mode resolution, filter_trace fields
  F) Admin endpoints — GET/PUT /platform/data-source, POST /snapshot-cleanup
"""
from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from app.services.platform_settings import (
    MODE_LIVE,
    MODE_SNAPSHOT,
    PlatformSettings,
)
from app.utils.snapshot import (
    SnapshotChainSource,
    SnapshotRecorder,
    run_snapshot_cleanup,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures
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
        "symbol": "SPY260320C00520000",
        "option_type": "call",
        "strike": 520.0,
        "expiration_date": "2026-03-20",
        "bid": 3.10,
        "ask": 3.40,
        "last": 3.25,
        "volume": 800,
        "open_interest": 3200,
        "greeks": {"delta": 0.60, "gamma": 0.04, "theta": -0.07, "smv_vol": 0.20},
    },
]

# Chain data where call+put at same strike enables derivation
ATM_CHAIN: list[dict[str, Any]] = [
    {
        "symbol": "SPY260320C00510000",
        "option_type": "call",
        "strike": 510.0,
        "expiration_date": "2026-03-20",
        "bid": 5.00,
        "ask": 5.40,
        "volume": 100,
        "open_interest": 200,
    },
    {
        "symbol": "SPY260320P00510000",
        "option_type": "put",
        "strike": 510.0,
        "expiration_date": "2026-03-20",
        "bid": 4.80,
        "ask": 5.20,
        "volume": 100,
        "open_interest": 200,
    },
]


def _write_snapshot(
    base: Path,
    provider: str,
    date_str: str,
    symbol: str,
    expiration: str,
    chain: list[dict[str, Any]],
    *,
    underlying_price: float | None = None,
    timestamp: str | None = None,
) -> Path:
    """Write a minimal snapshot file."""
    file_dir = base / provider / date_str / symbol / expiration
    file_dir.mkdir(parents=True, exist_ok=True)
    filename = "chain_120000_abc123.json"
    meta: dict[str, Any] = {
        "provider": provider,
        "symbol": symbol,
        "expiration": expiration,
        "underlying_price": underlying_price,
    }
    if timestamp:
        meta["timestamp"] = timestamp
    path = file_dir / filename
    path.write_text(
        json.dumps({"meta": meta, "raw": chain}, indent=2),
        encoding="utf-8",
    )
    return path


def _write_prices_history(
    base: Path,
    provider: str,
    date_str: str,
    symbol: str,
    closes: list[float],
) -> Path:
    """Write a prices_history.json file."""
    sym_dir = base / provider / date_str / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)
    path = sym_dir / "prices_history.json"
    data = {
        "meta": {
            "provider": provider,
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "close_count": len(closes),
        },
        "closes": closes,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════════════════════
# A) PlatformSettings
# ═══════════════════════════════════════════════════════════════════════════


class TestPlatformSettings:
    """PlatformSettings load, save, persistence, and env-var fallback."""

    def test_default_mode_is_live(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path)
        assert ps.data_source_mode == MODE_LIVE

    def test_env_default_snapshot(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path, env_default_mode="snapshot")
        assert ps.data_source_mode == MODE_SNAPSHOT

    def test_env_default_invalid_falls_back_to_live(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path, env_default_mode="bogus")
        assert ps.data_source_mode == MODE_LIVE

    def test_set_mode_persists(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path)
        result = ps.set_data_source_mode("snapshot")
        assert result["data_source_mode"] == MODE_SNAPSHOT
        assert result["updated_at"] is not None

        # Read from disk again
        ps2 = PlatformSettings(tmp_path)
        assert ps2.data_source_mode == MODE_SNAPSHOT

    def test_set_mode_invalid_raises(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path)
        with pytest.raises(ValueError, match="Invalid data_source_mode"):
            ps.set_data_source_mode("hybrid")

    def test_get_state_returns_copy(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path)
        state = ps.get_state()
        assert isinstance(state, dict)
        assert "data_source_mode" in state
        assert "version" in state
        # Mutation of copy doesn't affect internal state
        state["data_source_mode"] = "snapshot"
        assert ps.data_source_mode == MODE_LIVE

    def test_file_persistence_round_trip(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path)
        ps.set_data_source_mode("snapshot")

        # File exists
        settings_file = tmp_path / "platform_settings.json"
        assert settings_file.is_file()
        raw = json.loads(settings_file.read_text(encoding="utf-8"))
        assert raw["data_source_mode"] == "snapshot"
        assert raw["version"] == 1

    def test_corrupted_file_falls_back_to_env(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "platform_settings.json"
        settings_file.write_text("{{not valid json}", encoding="utf-8")

        ps = PlatformSettings(tmp_path, env_default_mode="snapshot")
        assert ps.data_source_mode == MODE_SNAPSHOT

    def test_mode_case_insensitive(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path)
        ps.set_data_source_mode("SNAPSHOT")
        assert ps.data_source_mode == MODE_SNAPSHOT

    def test_mode_with_whitespace(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path)
        ps.set_data_source_mode("  live  ")
        assert ps.data_source_mode == MODE_LIVE

    def test_toggle_back_and_forth(self, tmp_path: Path) -> None:
        ps = PlatformSettings(tmp_path)
        ps.set_data_source_mode("snapshot")
        assert ps.data_source_mode == MODE_SNAPSHOT
        ps.set_data_source_mode("live")
        assert ps.data_source_mode == MODE_LIVE


# ═══════════════════════════════════════════════════════════════════════════
# B) SnapshotChainSource — staleness, prices_history, underlying derivation
# ═══════════════════════════════════════════════════════════════════════════


class TestSnapshotStaleness:
    """check_staleness() freshness/staleness detection."""

    def test_fresh_snapshot_not_stale(self, tmp_path: Path) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        _write_snapshot(
            tmp_path, "tradier", "20260301", "SPY", "2026-03-20",
            GOLDEN_CHAIN, timestamp=ts,
        )
        source = SnapshotChainSource(tmp_path, max_age_hours=48)
        result = source.check_staleness("SPY", "2026-03-20")
        assert result["stale"] is False
        assert result["snapshot_timestamp"] == ts
        assert result["warning"] is None

    def test_old_snapshot_is_stale(self, tmp_path: Path) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        _write_snapshot(
            tmp_path, "tradier", "20260228", "SPY", "2026-03-20",
            GOLDEN_CHAIN, timestamp=old_ts,
        )
        source = SnapshotChainSource(tmp_path, max_age_hours=48)
        result = source.check_staleness("SPY", "2026-03-20")
        assert result["stale"] is True
        assert "72" in str(result["warning"]) or "old" in result["warning"].lower()

    def test_missing_snapshot_is_stale(self, tmp_path: Path) -> None:
        source = SnapshotChainSource(tmp_path, max_age_hours=48)
        result = source.check_staleness("SPY", "2026-03-20")
        assert result["stale"] is True
        assert result["warning"] is not None

    def test_no_max_age_never_stale(self, tmp_path: Path) -> None:
        """When max_age_hours is None, staleness check always returns fresh."""
        source = SnapshotChainSource(tmp_path, max_age_hours=None)
        result = source.check_staleness("SPY", "2026-03-20")
        assert result["stale"] is False
        assert result["max_age_hours"] is None

    def test_staleness_missing_symbol_dir(self, tmp_path: Path) -> None:
        """Snapshot for different symbol — target symbol is stale."""
        _write_snapshot(
            tmp_path, "tradier", "20260301", "QQQ", "2026-03-20",
            GOLDEN_CHAIN,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        source = SnapshotChainSource(tmp_path, max_age_hours=48)
        result = source.check_staleness("SPY", "2026-03-20")
        assert result["stale"] is True


class TestSnapshotPricesHistory:
    """get_prices_history() loading from snapshot dirs."""

    def test_loads_prices_history(self, tmp_path: Path) -> None:
        closes = [510.0, 511.5, 512.3, 509.8]
        _write_prices_history(tmp_path, "tradier", "20260301", "SPY", closes)
        source = SnapshotChainSource(tmp_path)
        result = source.get_prices_history("SPY")
        assert result == closes

    def test_empty_when_missing(self, tmp_path: Path) -> None:
        source = SnapshotChainSource(tmp_path)
        assert source.get_prices_history("SPY") == []

    def test_case_insensitive_symbol(self, tmp_path: Path) -> None:
        closes = [100.0, 101.0]
        _write_prices_history(tmp_path, "tradier", "20260301", "QQQ", closes)
        source = SnapshotChainSource(tmp_path)
        assert source.get_prices_history("qqq") == closes

    def test_latest_date_dir_preferred(self, tmp_path: Path) -> None:
        old_closes = [500.0]
        new_closes = [520.0, 521.0]
        _write_prices_history(tmp_path, "tradier", "20260228", "SPY", old_closes)
        _write_prices_history(tmp_path, "tradier", "20260301", "SPY", new_closes)
        source = SnapshotChainSource(tmp_path)
        assert source.get_prices_history("SPY") == new_closes


class TestSnapshotUnderlyingDerivation:
    """_derive_underlying_from_chain() ATM estimation."""

    def test_derives_price_from_atm_chain(self, tmp_path: Path) -> None:
        # Write chain with no underlying_price in meta
        _write_snapshot(
            tmp_path, "tradier", "20260301", "SPY", "2026-03-20",
            ATM_CHAIN, underlying_price=None,
        )
        source = SnapshotChainSource(tmp_path)
        price = source.get_underlying_price("SPY")
        assert price is not None
        # ATM strike=510, call mid=5.20, put mid=5.00
        # Estimated = 510 + 5.20 - 5.00 = 510.20
        assert abs(price - 510.20) < 0.01

    def test_meta_price_takes_precedence(self, tmp_path: Path) -> None:
        """When meta has underlying_price, derivation is not needed."""
        _write_snapshot(
            tmp_path, "tradier", "20260301", "SPY", "2026-03-20",
            ATM_CHAIN, underlying_price=513.42,
        )
        source = SnapshotChainSource(tmp_path)
        assert source.get_underlying_price("SPY") == 513.42


# ═══════════════════════════════════════════════════════════════════════════
# C) SnapshotRecorder — prices_history capture
# ═══════════════════════════════════════════════════════════════════════════


class TestRecorderPricesHistory:
    """SnapshotRecorder.save_prices_history()."""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        closes = [510.0, 511.5, 512.0]
        path = recorder.save_prices_history(
            closes, provider="tradier", symbol="SPY",
        )
        assert path is not None and path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["closes"] == closes
        assert data["meta"]["symbol"] == "SPY"
        assert data["meta"]["close_count"] == 3

    def test_save_respects_symbol_filter(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(
            tmp_path, enabled=True, capture_symbols={"SPY"},
        )
        # SPY should work
        p1 = recorder.save_prices_history(
            [100.0], provider="tradier", symbol="SPY",
        )
        assert p1 is not None
        # QQQ should be filtered
        p2 = recorder.save_prices_history(
            [200.0], provider="tradier", symbol="QQQ",
        )
        assert p2 is None

    def test_file_is_loadable_by_chain_source(self, tmp_path: Path) -> None:
        """Round-trip: save with recorder → load with SnapshotChainSource."""
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        closes = [510.1, 511.2, 512.3]
        path = recorder.save_prices_history(
            closes, provider="tradier", symbol="SPY",
        )
        assert path is not None

        source = SnapshotChainSource(tmp_path)
        loaded = source.get_prices_history("SPY")
        assert loaded == closes


# ═══════════════════════════════════════════════════════════════════════════
# D) run_snapshot_cleanup — retention pruning
# ═══════════════════════════════════════════════════════════════════════════


class TestSnapshotCleanup:
    """run_snapshot_cleanup() prunes old date directories."""

    def _make_date_dir(self, base: Path, provider: str, age_days: int) -> Path:
        """Create a snapshot date directory *age_days* days old."""
        d = datetime.now(timezone.utc).date() - timedelta(days=age_days)
        date_str = d.strftime("%Y%m%d")
        dir_ = base / provider / date_str
        dir_.mkdir(parents=True, exist_ok=True)
        # Put a marker file so the dir isn't empty
        (dir_ / "marker.txt").write_text("test", encoding="utf-8")
        return dir_

    def test_removes_old_dirs(self, tmp_path: Path) -> None:
        old_dir = self._make_date_dir(tmp_path, "tradier", age_days=10)
        new_dir = self._make_date_dir(tmp_path, "tradier", age_days=1)

        removed = run_snapshot_cleanup(tmp_path, retention_days=7)
        assert len(removed) >= 1
        assert not old_dir.exists()
        assert new_dir.exists()

    def test_keeps_recent_dirs(self, tmp_path: Path) -> None:
        recent = self._make_date_dir(tmp_path, "tradier", age_days=3)

        removed = run_snapshot_cleanup(tmp_path, retention_days=7)
        assert len(removed) == 0
        assert recent.exists()

    def test_never_removes_today(self, tmp_path: Path) -> None:
        today_dir = self._make_date_dir(tmp_path, "tradier", age_days=0)

        removed = run_snapshot_cleanup(tmp_path, retention_days=0)
        assert today_dir.exists()

    def test_empty_dir_is_noop(self, tmp_path: Path) -> None:
        removed = run_snapshot_cleanup(tmp_path, retention_days=7)
        assert removed == []

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        removed = run_snapshot_cleanup(tmp_path / "nonexistent", retention_days=7)
        assert removed == []

    def test_multiple_providers(self, tmp_path: Path) -> None:
        old1 = self._make_date_dir(tmp_path, "tradier", age_days=15)
        old2 = self._make_date_dir(tmp_path, "other_provider", age_days=15)
        new1 = self._make_date_dir(tmp_path, "tradier", age_days=2)

        removed = run_snapshot_cleanup(tmp_path, retention_days=7)
        assert not old1.exists()
        assert not old2.exists()
        assert new1.exists()
        assert len(removed) >= 2


# ═══════════════════════════════════════════════════════════════════════════
# E) StrategyService — data_source_mode in filter_trace + report
# ═══════════════════════════════════════════════════════════════════════════


class TestStrategyServiceDataSourceMode:
    """StrategyService constructor accepts platform_settings / snapshot_dir."""

    def test_constructor_accepts_platform_settings(self, tmp_path: Path) -> None:
        """StrategyService can be instantiated with platform_settings kwarg."""
        from app.services.strategy_service import StrategyService

        mock_bds = MagicMock()
        mock_bds.config = MagicMock()
        mock_bds.config.SNAPSHOT_MAX_AGE_HOURS = 48

        ps = PlatformSettings(tmp_path, env_default_mode="live")

        svc = StrategyService(
            base_data_service=mock_bds,
            results_dir=tmp_path / "results",
            platform_settings=ps,
            snapshot_dir=tmp_path / "snapshots",
        )
        assert svc.platform_settings is ps
        assert svc._snapshot_dir == tmp_path / "snapshots"

    def test_input_snapshot_data_source_mode_live(self) -> None:
        """_build_input_snapshot includes data_source_mode='live' by default."""
        from app.services.strategy_service import StrategyService

        snap = StrategyService._build_input_snapshot(
            {
                "symbol": "SPY",
                "underlying_price": 510.0,
                "expiration": "2026-03-20",
                "dte": 30,
                "contracts": [],
                "prices_history": [500.0, 505.0, 510.0],
            },
            data_source_mode="live",
        )
        assert snap is not None
        assert snap["data_source_mode"] == "live"
        assert "tradier" in snap["pricing_source"]

    def test_input_snapshot_data_source_mode_snapshot(self) -> None:
        """_build_input_snapshot includes data_source_mode='snapshot'."""
        from app.services.strategy_service import StrategyService

        snap = StrategyService._build_input_snapshot(
            {
                "symbol": "SPY",
                "underlying_price": 510.0,
                "expiration": "2026-03-20",
                "dte": 30,
                "contracts": [],
                "prices_history": [500.0, 505.0, 510.0],
            },
            data_source_mode="snapshot",
        )
        assert snap is not None
        assert snap["data_source_mode"] == "snapshot"
        assert "snapshot" in snap["pricing_source"]


# ═══════════════════════════════════════════════════════════════════════════
# F) Admin endpoints (routes_admin.py)
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminDataSourceEndpoints:
    """Admin endpoints for Platform Data Source management."""

    @pytest.fixture
    def app_and_client(self, tmp_path):
        """Create a minimal ASGI app with platform_settings on app.state."""
        from starlette.testclient import TestClient
        from fastapi import FastAPI
        from app.api.routes_admin import router

        app = FastAPI()
        app.include_router(router, prefix="/api/admin")

        # Set up app.state with required attributes
        ps = PlatformSettings(tmp_path, env_default_mode="live")
        app.state.platform_settings = ps

        # Snapshot dir
        snapshot_dir = tmp_path / "snapshots"
        snapshot_dir.mkdir()
        app.state.snapshot_dir = snapshot_dir

        # Config
        config = MagicMock()
        config.SNAPSHOT_RETENTION_DAYS = 7
        app.state.config = config

        # Other required state attributes that routes might access
        app.state.validation_log = MagicMock()
        app.state.validation_log.get_recent.return_value = []
        app.state.validation_log.rollup.return_value = {
            "top_codes": [], "counts_by_severity": {},
        }

        # source_health needs base_data_service
        bds = MagicMock()
        bds.check_source_health = MagicMock(return_value={})
        app.state.base_data_service = bds

        return app, TestClient(app)

    def test_get_data_source(self, app_and_client) -> None:
        app, client = app_and_client
        resp = client.get("/api/admin/platform/data-source")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data_source_mode"] == "live"
        assert "has_snapshots" in data

    def test_put_data_source_to_snapshot(self, app_and_client) -> None:
        app, client = app_and_client
        resp = client.put(
            "/api/admin/platform/data-source",
            json={"data_source_mode": "snapshot"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data_source_mode"] == "snapshot"

        # Verify it persisted
        resp2 = client.get("/api/admin/platform/data-source")
        assert resp2.json()["data_source_mode"] == "snapshot"

    def test_put_data_source_invalid_mode(self, app_and_client) -> None:
        app, client = app_and_client
        resp = client.put(
            "/api/admin/platform/data-source",
            json={"data_source_mode": "invalid"},
        )
        assert resp.status_code == 400

    def test_put_data_source_toggle_back(self, app_and_client) -> None:
        app, client = app_and_client
        # Toggle to snapshot
        client.put(
            "/api/admin/platform/data-source",
            json={"data_source_mode": "snapshot"},
        )
        # Toggle back to live
        resp = client.put(
            "/api/admin/platform/data-source",
            json={"data_source_mode": "live"},
        )
        assert resp.status_code == 200
        assert resp.json()["data_source_mode"] == "live"

    def test_snapshot_cleanup_endpoint(self, app_and_client, tmp_path) -> None:
        app, client = app_and_client
        # Create an old snapshot dir
        old_date = (datetime.now(timezone.utc).date() - timedelta(days=30))
        old_dir = tmp_path / "snapshots" / "tradier" / old_date.strftime("%Y%m%d")
        old_dir.mkdir(parents=True)
        (old_dir / "marker.txt").write_text("test", encoding="utf-8")

        resp = client.post("/api/admin/platform/snapshot-cleanup")
        assert resp.status_code == 200
        data = resp.json()
        assert "removed_directories" in data
        assert len(data["removed_directories"]) >= 1
