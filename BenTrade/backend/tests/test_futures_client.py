"""Targeted integration test for FuturesClient.

Tests all public methods against live Yahoo Finance data.
Run from backend/: python -m pytest tests/test_futures_client.py -v
"""
import asyncio
import pytest

from app.clients.futures_client import FuturesClient, _INSTRUMENTS
from app.config import Settings
from app.utils.cache import TTLCache


@pytest.fixture
def client():
    settings = Settings()
    cache = TTLCache()
    return FuturesClient(settings=settings, cache=cache)


@pytest.mark.asyncio
async def test_snapshot_es(client):
    """ES=F snapshot returns expected fields."""
    snap = await client.get_snapshot("es")
    assert snap is not None, "ES snapshot should not be None"
    assert snap["instrument"] == "es"
    assert snap["source"] == "yahoo_direct"
    assert snap["label"] == "S&P 500 Futures"
    assert isinstance(snap["last"], (int, float))
    assert snap["last"] > 0
    assert snap["prev_close"] is not None
    assert snap["change"] is not None
    assert snap["change_pct"] is not None
    assert snap["asset_class"] == "equity_index"
    assert snap["underlying"] == "SPY"


@pytest.mark.asyncio
async def test_snapshot_unknown_instrument(client):
    """Unknown instrument returns None."""
    snap = await client.get_snapshot("BOGUS")
    assert snap is None


@pytest.mark.asyncio
async def test_snapshot_vix(client):
    """VIX spot index returns valid data."""
    snap = await client.get_snapshot("vix")
    assert snap is not None
    assert snap["instrument"] == "vix"
    assert snap["last"] > 0
    assert snap["asset_class"] == "volatility"


@pytest.mark.asyncio
async def test_all_snapshots(client):
    """All instruments return something (None or valid dict)."""
    results = await client.get_all_snapshots()
    assert len(results) == len(_INSTRUMENTS)
    # At least equity index futures should have data
    for key in ("es", "nq", "rty", "ym"):
        snap = results.get(key)
        assert snap is not None, f"{key} should have data"
        assert snap["last"] > 0


@pytest.mark.asyncio
async def test_bars_es_hourly(client):
    """ES hourly bars return non-empty list."""
    bars = await client.get_bars("es", timeframe="1hour", days=5)
    assert isinstance(bars, list)
    assert len(bars) > 0
    bar = bars[0]
    assert "timestamp" in bar
    assert "open" in bar
    assert "high" in bar
    assert "low" in bar
    assert "close" in bar
    assert "volume" in bar


@pytest.mark.asyncio
async def test_bars_unknown_instrument(client):
    """Unknown instrument returns empty list."""
    bars = await client.get_bars("BOGUS")
    assert bars == []


@pytest.mark.asyncio
async def test_bars_daily(client):
    """Daily bars for VIX return data."""
    bars = await client.get_bars("vix", timeframe="1day", days=5)
    assert len(bars) >= 1


@pytest.mark.asyncio
async def test_vix_term_structure(client):
    """VIX term-structure returns expected shape."""
    ts = await client.get_vix_term_structure()
    assert ts is not None
    assert "spot" in ts
    assert "vxx_price" in ts
    assert "structure" in ts
    assert ts["structure"] in ("contango", "backwardation", "flat", "unknown")
    assert "contango_pct" in ts
    assert ts["source"] == "yahoo_direct"


@pytest.mark.asyncio
async def test_caching(client):
    """Second call uses cache (same object identity)."""
    snap1 = await client.get_snapshot("es")
    snap2 = await client.get_snapshot("es")
    # Should be the exact same object (cached)
    assert snap1 is snap2


@pytest.mark.asyncio
async def test_health(client):
    """Health check passes."""
    ok = await client.health()
    assert ok is True
