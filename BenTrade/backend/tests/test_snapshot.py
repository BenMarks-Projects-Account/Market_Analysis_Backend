"""Tests for snapshot recorder, chain sources, and round-trip replay.

Covers:
  - SnapshotRecorder writes valid JSON with correct structure
  - SnapshotRecorder respects symbol filter and per-symbol limit
  - SnapshotRecorder writes index file
  - SnapshotChainSource loads latest snapshot
  - SnapshotChainSource derives expirations from directory structure
  - SnapshotChainSource reads underlying_price from metadata
  - SnapshotChainSource raises FileNotFoundError when missing
  - Round-trip: captured data → SnapshotChainSource → normalize_chain matches live path
  - TradierChainSource satisfies OptionChainSource protocol
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.utils.snapshot import (
    OptionChainSource,
    SnapshotChainSource,
    SnapshotRecorder,
    TradierChainSource,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Golden fixture: realistic Tradier option-chain row (pre-normalization)
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


# ---------------------------------------------------------------------------
# SnapshotRecorder tests
# ---------------------------------------------------------------------------
class TestSnapshotRecorder:
    def test_save_writes_valid_json(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        path = recorder.save_chain_response(
            GOLDEN_CHAIN,
            provider="tradier",
            symbol="SPY",
            expiration="2026-03-20",
            endpoint="/markets/options/chains",
            http_status=200,
        )
        assert path is not None and path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "meta" in data
        assert "raw" in data
        assert data["meta"]["symbol"] == "SPY"
        assert data["meta"]["expiration"] == "2026-03-20"
        assert data["meta"]["provider"] == "tradier"
        assert data["meta"]["http_status"] == 200
        assert isinstance(data["raw"], list)
        assert len(data["raw"]) == 2

    def test_raw_preserves_all_fields(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        path = recorder.save_chain_response(
            GOLDEN_CHAIN,
            provider="tradier",
            symbol="SPY",
            expiration="2026-03-20",
        )
        assert path is not None
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data["raw"]
        assert raw[0]["greeks"]["delta"] == -0.25
        assert raw[1]["open_interest"] == 3200
        assert raw[0]["symbol"] == "SPY260320P00500000"

    def test_raw_text_fallback_for_non_json(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        path = recorder.save_chain_response(
            "not valid json {{{",
            provider="tradier",
            symbol="QQQ",
            expiration="2026-03-27",
        )
        assert path is not None
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["raw"]["_raw_text"] == "not valid json {{{"

    def test_full_envelope_raw_saved(self, tmp_path: Path) -> None:
        """Full Tradier envelope (from fetch_chain_raw_payload) is preserved."""
        envelope = {"options": {"option": GOLDEN_CHAIN}}
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        path = recorder.save_chain_response(
            envelope,
            provider="tradier",
            symbol="SPY",
            expiration="2026-03-20",
        )
        assert path is not None
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["raw"]["options"]["option"] == GOLDEN_CHAIN

    def test_folder_structure(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        path = recorder.save_chain_response(
            GOLDEN_CHAIN,
            provider="tradier",
            symbol="SPY",
            expiration="2026-03-20",
        )
        assert path is not None
        # Should contain: tradier / YYYYMMDD / SPY / 2026-03-20 / chain_*.json
        parts = path.relative_to(tmp_path).parts
        assert parts[0] == "tradier"
        assert parts[1].isdigit() and len(parts[1]) == 8  # YYYYMMDD
        assert parts[2] == "SPY"
        assert parts[3] == "2026-03-20"
        assert parts[4].startswith("chain_")

    def test_symbol_filter(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True, capture_symbols={"SPY"})
        assert recorder.should_capture("SPY") is True
        assert recorder.should_capture("QQQ") is False

        p1 = recorder.save_chain_response(
            GOLDEN_CHAIN, provider="tradier", symbol="SPY", expiration="2026-03-20",
        )
        p2 = recorder.save_chain_response(
            GOLDEN_CHAIN, provider="tradier", symbol="QQQ", expiration="2026-03-20",
        )
        assert p1 is not None
        assert p2 is None  # filtered out

    def test_limit_per_symbol(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True, limit_per_symbol=2)
        paths = []
        for exp in ["2026-03-20", "2026-03-27", "2026-04-03"]:
            p = recorder.save_chain_response(
                GOLDEN_CHAIN, provider="tradier", symbol="SPY", expiration=exp,
            )
            paths.append(p)
        assert paths[0] is not None
        assert paths[1] is not None
        assert paths[2] is None  # limit reached (2)

    def test_disabled_recorder_skips(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=False)
        p = recorder.save_chain_response(
            GOLDEN_CHAIN, provider="tradier", symbol="SPY", expiration="2026-03-20",
        )
        assert p is None

    def test_write_index(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        recorder.save_chain_response(
            GOLDEN_CHAIN, provider="tradier", symbol="SPY", expiration="2026-03-20",
        )
        recorder.save_chain_response(
            GOLDEN_CHAIN, provider="tradier", symbol="QQQ", expiration="2026-03-20",
        )
        index_path = recorder.write_index()
        assert index_path is not None and index_path.exists()
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert index["trace_id"] == recorder.trace_id
        assert index["file_count"] == 2
        assert len(index["files"]) == 2

    def test_write_index_empty(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        assert recorder.write_index() is None

    def test_reset_run(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        recorder.save_chain_response(
            GOLDEN_CHAIN, provider="tradier", symbol="SPY", expiration="2026-03-20",
        )
        old_trace = recorder.trace_id
        recorder.reset_run()
        assert recorder.trace_id != old_trace
        # Limit counters reset — should be able to save again with limit=1
        assert recorder.should_capture("SPY") is True

    def test_underlying_price_in_meta(self, tmp_path: Path) -> None:
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        path = recorder.save_chain_response(
            GOLDEN_CHAIN,
            provider="tradier",
            symbol="SPY",
            expiration="2026-03-20",
            underlying_price=513.42,
        )
        assert path is not None
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["meta"]["underlying_price"] == 513.42


# ---------------------------------------------------------------------------
# SnapshotChainSource tests
# ---------------------------------------------------------------------------
def _write_snapshot(
    base: Path,
    provider: str,
    date_str: str,
    symbol: str,
    expiration: str,
    chain: list[dict[str, Any]],
    *,
    underlying_price: float | None = None,
) -> Path:
    """Helper: write a minimal snapshot file to the expected directory."""
    file_dir = base / provider / date_str / symbol / expiration
    file_dir.mkdir(parents=True, exist_ok=True)
    filename = f"chain_120000_abc123.json"
    meta: dict[str, Any] = {
        "provider": provider,
        "symbol": symbol,
        "expiration": expiration,
        "underlying_price": underlying_price,
    }
    path = file_dir / filename
    path.write_text(json.dumps({"meta": meta, "raw": chain}, indent=2), encoding="utf-8")
    return path


class TestSnapshotChainSource:
    def test_loads_latest(self, tmp_path: Path) -> None:
        _write_snapshot(tmp_path, "tradier", "20260226", "SPY", "2026-03-20", GOLDEN_CHAIN)
        source = SnapshotChainSource(tmp_path)
        result = _run(source.get_chain("SPY", "2026-03-20"))
        assert len(result) == 2
        assert result[0]["strike"] == 500.0
        assert result[1]["option_type"] == "call"

    def test_loads_from_full_envelope(self, tmp_path: Path) -> None:
        """When raw is a full Tradier envelope, extraction works."""
        envelope = {"options": {"option": GOLDEN_CHAIN}}
        _write_snapshot(tmp_path, "tradier", "20260226", "SPY", "2026-03-20", envelope)
        source = SnapshotChainSource(tmp_path)
        result = _run(source.get_chain("SPY", "2026-03-20"))
        assert len(result) == 2

    def test_missing_snapshot_raises(self, tmp_path: Path) -> None:
        source = SnapshotChainSource(tmp_path)
        with pytest.raises(FileNotFoundError, match="No snapshot directory"):
            _run(source.get_chain("SPY", "2026-03-20"))

    def test_missing_symbol_raises(self, tmp_path: Path) -> None:
        _write_snapshot(tmp_path, "tradier", "20260226", "QQQ", "2026-03-20", GOLDEN_CHAIN)
        source = SnapshotChainSource(tmp_path)
        with pytest.raises(FileNotFoundError, match="No snapshot available for SPY"):
            _run(source.get_chain("SPY", "2026-03-20"))

    def test_get_available_expirations(self, tmp_path: Path) -> None:
        _write_snapshot(tmp_path, "tradier", "20260226", "SPY", "2026-03-20", GOLDEN_CHAIN)
        _write_snapshot(tmp_path, "tradier", "20260226", "SPY", "2026-03-27", GOLDEN_CHAIN)
        _write_snapshot(tmp_path, "tradier", "20260226", "SPY", "2026-04-03", GOLDEN_CHAIN)
        source = SnapshotChainSource(tmp_path)
        exps = source.get_available_expirations("SPY")
        assert exps == ["2026-03-20", "2026-03-27", "2026-04-03"]

    def test_get_available_expirations_empty(self, tmp_path: Path) -> None:
        source = SnapshotChainSource(tmp_path)
        assert source.get_available_expirations("SPY") == []

    def test_get_underlying_price(self, tmp_path: Path) -> None:
        _write_snapshot(
            tmp_path, "tradier", "20260226", "SPY", "2026-03-20",
            GOLDEN_CHAIN, underlying_price=513.42,
        )
        source = SnapshotChainSource(tmp_path)
        assert source.get_underlying_price("SPY") == 513.42

    def test_get_underlying_price_missing(self, tmp_path: Path) -> None:
        source = SnapshotChainSource(tmp_path)
        assert source.get_underlying_price("SPY") is None

    def test_load_from_explicit_path(self, tmp_path: Path) -> None:
        path = _write_snapshot(tmp_path, "tradier", "20260226", "SPY", "2026-03-20", GOLDEN_CHAIN)
        source = SnapshotChainSource(tmp_path)
        result = source.load_from_path(path)
        assert len(result) == 2

    def test_load_from_missing_path_raises(self, tmp_path: Path) -> None:
        source = SnapshotChainSource(tmp_path)
        with pytest.raises(FileNotFoundError):
            source.load_from_path(tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# Round-trip: capture → replay → normalize_chain produces same output
# ---------------------------------------------------------------------------
class TestRoundTrip:
    def test_capture_replay_matches(self, tmp_path: Path) -> None:
        """Snapshot capture → SnapshotChainSource load matches original."""
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        recorder.save_chain_response(
            GOLDEN_CHAIN,
            provider="tradier",
            symbol="SPY",
            expiration="2026-03-20",
            underlying_price=513.42,
        )

        source = SnapshotChainSource(tmp_path)
        loaded = source._load_latest("SPY", "2026-03-20")

        # Every field must match the original
        assert len(loaded) == len(GOLDEN_CHAIN)
        for original, replayed in zip(GOLDEN_CHAIN, loaded):
            assert original == replayed

    def test_normalize_chain_parity(self, tmp_path: Path) -> None:
        """normalize_chain() produces identical output for live and replayed data."""
        from app.services.base_data_service import BaseDataService

        # We can't construct a full BaseDataService without clients, so use
        # the static normalize_chain method via a minimal instance.
        # normalize_chain is a pure function on the contract list.
        class _MinimalService:
            """Use only the normalize_chain logic from BaseDataService."""
            _to_float = staticmethod(BaseDataService._to_float)
            _to_int = staticmethod(BaseDataService._to_int)
            _normalize_iv = staticmethod(BaseDataService._normalize_iv)
            _parse_expiration = staticmethod(BaseDataService._parse_expiration)
            normalize_chain = BaseDataService.normalize_chain
            _mark_validation_warning = lambda self, *a, **k: None  # noqa: E731

        svc = _MinimalService()
        live_result = svc.normalize_chain(GOLDEN_CHAIN)

        # Capture + replay
        recorder = SnapshotRecorder(tmp_path, enabled=True)
        recorder.save_chain_response(
            GOLDEN_CHAIN,
            provider="tradier",
            symbol="SPY",
            expiration="2026-03-20",
        )

        source = SnapshotChainSource(tmp_path)
        replayed = _run(source.get_chain("SPY", "2026-03-20"))
        replay_result = svc.normalize_chain(replayed)

        assert len(live_result) == len(replay_result)
        for live_c, replay_c in zip(live_result, replay_result):
            assert live_c.strike == replay_c.strike
            assert live_c.bid == replay_c.bid
            assert live_c.ask == replay_c.ask
            assert live_c.delta == replay_c.delta
            assert live_c.option_type == replay_c.option_type


# ---------------------------------------------------------------------------
# TradierChainSource protocol conformance
# ---------------------------------------------------------------------------
class TestTradierChainSource:
    def test_satisfies_protocol(self) -> None:
        class FakeClient:
            async def get_chain(self, symbol, expiration, greeks=True):
                return []

        source = TradierChainSource(FakeClient())
        assert isinstance(source, OptionChainSource)

    def test_delegates_to_client(self) -> None:
        class FakeClient:
            async def get_chain(self, symbol, expiration, greeks=True):
                return [{"strike": 100.0}]

        source = TradierChainSource(FakeClient())
        result = _run(source.get_chain("SPY", "2026-03-20"))
        assert result == [{"strike": 100.0}]
