"""Sanity-check tests for the Stock Engine endpoint and ranking logic.

Verifies:
  1. Engine returns max 9 candidates (TOP_N).
  2. Sorting is stable and deterministic across repeated runs.
  3. Partial scanner failure still returns results from healthy scanners.
"""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure the backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.stock_engine_service import (
    StockEngineService,
    _sort_key,
    get_scanner_score,
    TOP_N,
)


# Helper to run async in sync tests
def _run(coro):
    return asyncio.run(coro)


# ── Helpers ────────────────────────────────────────────────────────

def _make_candidate(
    symbol: str,
    strategy_id: str,
    composite_score: float,
    avg_dollar_volume: float = 5_000_000,
    recommendation: str | None = None,
) -> dict[str, Any]:
    """Build a minimal stock candidate dict for testing."""
    candidate: dict[str, Any] = {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "composite_score": composite_score,
        "metrics": {"avg_dollar_volume": avg_dollar_volume},
    }
    if recommendation:
        candidate["model_evaluation"] = {"recommendation": recommendation}
    return candidate


class _FakeScanner:
    """A stub scanner that returns a canned response from .scan()."""

    def __init__(self, candidates: list[dict] | None = None, *, should_fail: bool = False):
        self._candidates = candidates or []
        self._should_fail = should_fail

    async def scan(self) -> dict:
        if self._should_fail:
            raise RuntimeError("Simulated scanner failure")
        return {
            "strategy_id": "fake_scanner",
            "status": "ok",
            "candidates": copy.deepcopy(self._candidates),
        }


# ── Tests ──────────────────────────────────────────────────────────

class TestStockEngineTopN:
    """Engine always returns at most TOP_N (9) candidates."""

    def test_exactly_top_n(self):
        # 15 candidates → engine must return only 9
        candidates = [
            _make_candidate(f"SYM{i}", "stock_pullback_swing", 80.0 - i)
            for i in range(15)
        ]
        scanner = _FakeScanner(candidates)
        engine = StockEngineService(
            pullback_swing_service=scanner,
            momentum_breakout_service=None,
            mean_reversion_service=None,
            volatility_expansion_service=None,
        )
        result = _run(engine.scan())
        assert result["engine"] == "stock_engine"
        assert len(result["candidates"]) == TOP_N
        assert result["total_candidates"] == 15

    def test_fewer_than_top_n(self):
        candidates = [_make_candidate("AAPL", "stock_pullback_swing", 75.0)]
        scanner = _FakeScanner(candidates)
        engine = StockEngineService(
            pullback_swing_service=scanner,
            momentum_breakout_service=None,
            mean_reversion_service=None,
            volatility_expansion_service=None,
        )
        result = _run(engine.scan())
        assert len(result["candidates"]) == 1

    def test_empty_results(self):
        scanner = _FakeScanner([])
        engine = StockEngineService(
            pullback_swing_service=scanner,
            momentum_breakout_service=None,
            mean_reversion_service=None,
            volatility_expansion_service=None,
        )
        result = _run(engine.scan())
        assert result["candidates"] == []
        # Status is "partial" because 3 services are None (skipped),
        # which generates warnings.  Only "ok" if all scanners succeed.
        assert result["status"] in ("ok", "partial")


class TestStockEngineSortStability:
    """Sorting is stable and deterministic."""

    def test_sort_by_score_descending(self):
        candidates = [
            _make_candidate("LOW", "s1", 40.0),
            _make_candidate("MID", "s1", 60.0),
            _make_candidate("HIGH", "s1", 90.0),
        ]
        sorted_candidates = sorted(candidates, key=_sort_key)
        symbols = [c["symbol"] for c in sorted_candidates]
        assert symbols == ["HIGH", "MID", "LOW"]

    def test_recommendation_tie_break(self):
        # Same score, different recommendations → BUY wins over HOLD
        candidates = [
            _make_candidate("HOLD_SYM", "s1", 80.0, recommendation="HOLD"),
            _make_candidate("BUY_SYM", "s1", 80.0, recommendation="BUY"),
        ]
        sorted_candidates = sorted(candidates, key=_sort_key)
        assert sorted_candidates[0]["symbol"] == "BUY_SYM"
        assert sorted_candidates[1]["symbol"] == "HOLD_SYM"

    def test_volume_tie_break(self):
        # Same score, same rec → higher avg_dollar_volume wins
        candidates = [
            _make_candidate("LOW_VOL", "s1", 80.0, avg_dollar_volume=1_000_000),
            _make_candidate("HIGH_VOL", "s1", 80.0, avg_dollar_volume=50_000_000),
        ]
        sorted_candidates = sorted(candidates, key=_sort_key)
        assert sorted_candidates[0]["symbol"] == "HIGH_VOL"

    def test_deterministic_across_runs(self):
        candidates = [
            _make_candidate("MSFT", "s1", 72.5, avg_dollar_volume=30e6),
            _make_candidate("AAPL", "s1", 72.5, avg_dollar_volume=30e6),
            _make_candidate("GOOG", "s1", 72.5, avg_dollar_volume=30e6),
        ]
        result1 = sorted(candidates, key=_sort_key)
        result2 = sorted(candidates, key=_sort_key)
        # Must be identical across runs
        symbols1 = [c["symbol"] for c in result1]
        symbols2 = [c["symbol"] for c in result2]
        assert symbols1 == symbols2
        # Alphabetical tie-break: AAPL < GOOG < MSFT
        assert symbols1 == ["AAPL", "GOOG", "MSFT"]


class TestStockEnginePartialFailure:
    """If one scanner fails, results from other scanners still return."""

    def test_one_scanner_fails(self):
        good_candidates = [
            _make_candidate("AAPL", "stock_pullback_swing", 85.0),
            _make_candidate("MSFT", "stock_pullback_swing", 78.0),
        ]
        good_scanner = _FakeScanner(good_candidates)
        bad_scanner = _FakeScanner(should_fail=True)

        engine = StockEngineService(
            pullback_swing_service=good_scanner,
            momentum_breakout_service=bad_scanner,
            mean_reversion_service=None,
            volatility_expansion_service=None,
        )
        result = _run(engine.scan())

        # Should still have candidates from the good scanner
        assert len(result["candidates"]) == 2
        # Should report partial status with warnings
        assert result["status"] == "partial"
        assert len(result["warnings"]) >= 1

    def test_all_scanners_fail(self):
        bad1 = _FakeScanner(should_fail=True)
        bad2 = _FakeScanner(should_fail=True)

        engine = StockEngineService(
            pullback_swing_service=bad1,
            momentum_breakout_service=bad2,
            mean_reversion_service=None,
            volatility_expansion_service=None,
        )
        result = _run(engine.scan())

        assert result["candidates"] == []
        assert result["status"] == "partial"
        assert len(result["warnings"]) >= 2

    def test_multi_scanner_aggregation(self):
        """Results from multiple scanners are combined and ranked together."""
        scan1 = _FakeScanner([
            _make_candidate("AAPL", "stock_pullback_swing", 90.0),
            _make_candidate("GOOG", "stock_pullback_swing", 70.0),
        ])
        scan2 = _FakeScanner([
            _make_candidate("MSFT", "stock_momentum_breakout", 85.0),
            _make_candidate("AMZN", "stock_momentum_breakout", 75.0),
        ])
        scan3 = _FakeScanner([
            _make_candidate("NVDA", "stock_mean_reversion", 95.0),
        ])

        engine = StockEngineService(
            pullback_swing_service=scan1,
            momentum_breakout_service=scan2,
            mean_reversion_service=scan3,
            volatility_expansion_service=None,
        )
        result = _run(engine.scan())

        assert result["total_candidates"] == 5
        assert len(result["candidates"]) == 5  # all 5, since < TOP_N
        # Highest score should be first
        symbols = [c["symbol"] for c in result["candidates"]]
        assert symbols[0] == "NVDA"  # 95.0
        assert symbols[1] == "AAPL"  # 90.0
        assert symbols[2] == "MSFT"  # 85.0


class TestStockEngineResponseShape:
    """Verify the response JSON shape."""

    def test_response_fields(self):
        scanner = _FakeScanner([_make_candidate("SPY", "s1", 80.0)])
        engine = StockEngineService(
            pullback_swing_service=scanner,
            momentum_breakout_service=None,
            mean_reversion_service=None,
            volatility_expansion_service=None,
        )
        result = _run(engine.scan())

        assert "engine" in result
        assert result["engine"] == "stock_engine"
        assert "status" in result
        assert "as_of" in result
        assert "top_n" in result
        assert result["top_n"] == TOP_N
        assert "total_candidates" in result
        assert "candidates" in result
        assert "scanners" in result
        assert "warnings" in result
        assert "scan_time_seconds" in result
        assert isinstance(result["scan_time_seconds"], float)

    def test_scanner_meta_includes_max_score(self):
        """Each scanner entry in scanners[] must include max_composite_score."""
        candidates = [
            _make_candidate("AAPL", "stock_pullback_swing", 85.0),
            _make_candidate("MSFT", "stock_pullback_swing", 72.0),
        ]
        scanner = _FakeScanner(candidates)
        engine = StockEngineService(
            pullback_swing_service=scanner,
            momentum_breakout_service=None,
            mean_reversion_service=None,
            volatility_expansion_service=None,
        )
        result = _run(engine.scan())
        # Find the pullback scanner entry
        ps_meta = [s for s in result["scanners"] if s["status"] == "ok"]
        assert len(ps_meta) >= 1
        assert ps_meta[0]["max_composite_score"] == 85.0
        assert ps_meta[0]["candidates_count"] == 2


class TestGetScannerScore:
    """Canonical score helper — same field as 'Score XX%' on the card."""

    def test_numeric_score(self):
        assert get_scanner_score({"composite_score": 85.0}) == 85.0

    def test_string_score(self):
        """String '85.0' must be converted to float 85.0, not sorted lexicographically."""
        assert get_scanner_score({"composite_score": "85.0"}) == 85.0

    def test_none_score(self):
        assert get_scanner_score({"composite_score": None}) == 0.0

    def test_missing_score(self):
        assert get_scanner_score({}) == 0.0

    def test_zero_score(self):
        assert get_scanner_score({"composite_score": 0}) == 0.0

    def test_string_vs_number_ranking(self):
        """Ensure '9' does not sort above 80 (lexicographic bug guard)."""
        c_nine = {"composite_score": "9"}
        c_eighty = {"composite_score": 80}
        assert get_scanner_score(c_eighty) > get_scanner_score(c_nine)


class TestPullbackSwingBeatsLowerScore:
    """Critical scenario: a pullback swing candidate at 85 must rank
    above candidates at 67 from other scanners — the user's reported bug.
    """

    def test_pullback_85_ranks_above_others_67(self):
        """If pullback swing produces Score 85.0% and other scanners produce
        67.0%, the top-9 must include the 85 and rank it first."""
        scan_ps = _FakeScanner([
            _make_candidate("SBUX", "stock_pullback_swing", 85.0),
            _make_candidate("COST", "stock_pullback_swing", 72.0),
        ])
        scan_mb = _FakeScanner([
            _make_candidate("NVDA", "stock_momentum_breakout", 67.0),
            _make_candidate("AMZN", "stock_momentum_breakout", 65.0),
        ])
        scan_mr = _FakeScanner([
            _make_candidate("META", "stock_mean_reversion", 63.0),
        ])
        scan_ve = _FakeScanner([
            _make_candidate("TSLA", "stock_volatility_expansion", 60.0),
        ])

        engine = StockEngineService(
            pullback_swing_service=scan_ps,
            momentum_breakout_service=scan_mb,
            mean_reversion_service=scan_mr,
            volatility_expansion_service=scan_ve,
        )
        result = _run(engine.scan())

        assert result["total_candidates"] == 6
        top = result["candidates"]
        # SBUX (85) must be first
        assert top[0]["symbol"] == "SBUX"
        assert top[0]["composite_score"] == 85.0
        # COST (72) second
        assert top[1]["symbol"] == "COST"
        # Order must be strictly descending by composite_score
        scores = [c["composite_score"] for c in top]
        assert scores == sorted(scores, reverse=True)

    def test_all_scanners_contribute(self):
        """When all 4 scanners return results, candidates from every
        scanner should appear in the aggregated pool."""
        scan_ps = _FakeScanner([_make_candidate("A", "stock_pullback_swing", 90.0)])
        scan_mb = _FakeScanner([_make_candidate("B", "stock_momentum_breakout", 80.0)])
        scan_mr = _FakeScanner([_make_candidate("C", "stock_mean_reversion", 70.0)])
        scan_ve = _FakeScanner([_make_candidate("D", "stock_volatility_expansion", 60.0)])

        engine = StockEngineService(
            pullback_swing_service=scan_ps,
            momentum_breakout_service=scan_mb,
            mean_reversion_service=scan_mr,
            volatility_expansion_service=scan_ve,
        )
        result = _run(engine.scan())

        assert result["total_candidates"] == 4
        assert result["status"] == "ok"
        symbols = {c["symbol"] for c in result["candidates"]}
        assert symbols == {"A", "B", "C", "D"}
        # Descending order by score
        assert [c["symbol"] for c in result["candidates"]] == ["A", "B", "C", "D"]

    def test_sort_then_slice_not_slice_then_sort(self):
        """Regression guard: sorting must happen BEFORE slicing to top_n.
        If we sliced first, lower-scoring items could appear instead of
        higher-scoring ones from later scanners."""
        # Scanner 1: 9 candidates at score 50
        low_candidates = [
            _make_candidate(f"LOW{i}", "stock_momentum_breakout", 50.0)
            for i in range(9)
        ]
        # Scanner 2: 1 candidate at score 95 (must not be excluded)
        high_candidate = [
            _make_candidate("HIGH", "stock_pullback_swing", 95.0),
        ]
        scan_low = _FakeScanner(low_candidates)
        scan_high = _FakeScanner(high_candidate)

        engine = StockEngineService(
            pullback_swing_service=scan_high,
            momentum_breakout_service=scan_low,
            mean_reversion_service=None,
            volatility_expansion_service=None,
        )
        result = _run(engine.scan())

        assert result["total_candidates"] == 10
        assert len(result["candidates"]) == TOP_N  # 9
        # HIGH (95) must be the first result
        assert result["candidates"][0]["symbol"] == "HIGH"
        assert result["candidates"][0]["composite_score"] == 95.0

    def test_normalization_0_to_1_vs_0_to_100(self):
        """Guard: if a score is accidentally on 0-1 scale (0.85),
        it should rank below a properly-scaled 67.0 score.
        get_scanner_score returns raw value; normalization is NOT applied
        on the backend (it's a frontend display concern)."""
        c_low_scale = _make_candidate("BAD", "s1", 0.85)   # 0-1 scale = actually 0.85
        c_normal = _make_candidate("GOOD", "s1", 67.0)     # 0-100 scale = 67.0
        sorted_c = sorted([c_low_scale, c_normal], key=_sort_key)
        # 67.0 > 0.85, so GOOD ranks first
        assert sorted_c[0]["symbol"] == "GOOD"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
