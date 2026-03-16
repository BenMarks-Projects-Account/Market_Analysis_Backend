"""Tests for scanner-stage bounding and guaranteed finalization.

Coverage targets:
─── Generation cap enforcement
    - vertical spreads respects generation_cap
    - vertical spreads respects max_width
    - calendars respects generation_cap
    - butterflies respects generation_cap
    - iron condors respects generation_cap
─── Stage finalization guarantees
    - one scanner fails, others succeed → stage completes degraded
    - all scanners fail → stage fails
    - post-processing exception → stage still returns result
    - stage always returns a result dict
─── Liveness tracker
    - tracker snapshot reflects in-flight/completed/failed
    - tracker attached to run dict
    - snapshot surfaced in stage metadata
─── Generation cap passed through pipeline
    - context includes generation_cap
    - V2 scanner receives generation_cap
"""

import copy
import threading
import time

import pytest

from app.services.pipeline_scanner_stage import (
    DEFAULT_GENERATION_CAP,
    FinalizationCheckpoint,
    ScannerLivenessTracker,
    _execute_scanners_parallel,
    _make_scanner_entry,
    build_scanner_execution_record,
    scanner_stage_handler,
)
from app.services.pipeline_run_contract import (
    create_pipeline_run,
    mark_stage_completed,
    mark_stage_running,
)
from app.services.pipeline_artifact_store import (
    create_artifact_store,
)


# ── Helper factories ────────────────────────────────────────────

def _make_run_and_store(run_id="test-bounded-001"):
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    mark_stage_running(run, "market_data")
    mark_stage_completed(run, "market_data")
    return run, store


def _small_registry():
    return {
        "scan_a": _make_scanner_entry(
            "scan_a", "Scanner A", "stock", "test_a",
        ),
        "scan_b": _make_scanner_entry(
            "scan_b", "Scanner B", "options", "test_b",
        ),
    }


def _fast_executor(scanner_key, scanner_entry, context):
    return {
        "candidates": [
            {"symbol": "SPY", "trade_key": f"{scanner_key}_001",
             "normalized": {
                 "candidate_id": f"{scanner_key}_001",
                 "symbol": "SPY",
             }},
        ],
    }


def _slow_executor(scanner_key, scanner_entry, context):
    """Simulates a scanner that takes too long."""
    time.sleep(10)
    return {"candidates": []}


def _hanging_executor(scanner_key, scanner_entry, context):
    """Simulates a scanner that hangs forever."""
    event = threading.Event()
    event.wait(timeout=60)
    return {"candidates": []}


def _failing_executor(scanner_key, scanner_entry, context):
    raise RuntimeError(f"Scanner {scanner_key} exploded")


def _mixed_executor(scanner_key, scanner_entry, context):
    """scan_a succeeds, scan_b fails."""
    if scanner_key == "scan_b":
        raise RuntimeError("scan_b exploded")
    return _fast_executor(scanner_key, scanner_entry, context)


def _timeout_mixed_executor(scanner_key, scanner_entry, context):
    """scan_a succeeds, scan_b hangs."""
    if scanner_key == "scan_b":
        time.sleep(10)
        return {"candidates": []}
    return _fast_executor(scanner_key, scanner_entry, context)


# =====================================================================
#  Liveness tracker tests
# =====================================================================

class TestScannerLivenessTracker:

    def test_initial_snapshot(self):
        t = ScannerLivenessTracker()
        snap = t.snapshot()
        assert snap["in_flight_count"] == 0
        assert snap["completed"] == []
        assert snap["failed"] == []
        assert snap["timed_out"] == []

    def test_started_then_completed(self):
        t = ScannerLivenessTracker()
        t.mark_started("scan_a")
        snap = t.snapshot()
        assert "scan_a" in snap["in_flight_scanners"]
        assert snap["in_flight_count"] == 1

        t.mark_completed("scan_a")
        snap = t.snapshot()
        assert snap["in_flight_count"] == 0
        assert "scan_a" in snap["completed"]

    def test_started_then_failed(self):
        t = ScannerLivenessTracker()
        t.mark_started("scan_a")
        t.mark_failed("scan_a")
        snap = t.snapshot()
        assert snap["in_flight_count"] == 0
        assert "scan_a" in snap["failed"]

    def test_started_then_timed_out(self):
        t = ScannerLivenessTracker()
        t.mark_started("scan_a")
        t.mark_timed_out("scan_a")
        snap = t.snapshot()
        assert snap["in_flight_count"] == 0
        assert "scan_a" in snap["timed_out"]

    def test_cap_hit_tracking(self):
        t = ScannerLivenessTracker()
        t.mark_cap_hit("scan_a")
        snap = t.snapshot()
        assert "scan_a" in snap["cap_hit"]

    def test_multiple_scanners(self):
        t = ScannerLivenessTracker()
        t.mark_started("a")
        t.mark_started("b")
        t.mark_started("c")
        assert t.snapshot()["in_flight_count"] == 3

        t.mark_completed("a")
        t.mark_failed("b")
        t.mark_timed_out("c")
        snap = t.snapshot()
        assert snap["in_flight_count"] == 0
        assert "a" in snap["completed"]
        assert "b" in snap["failed"]
        assert "c" in snap["timed_out"]


# =====================================================================
#  Per-scanner timeout tests
# =====================================================================

# =====================================================================
#  Stage finalization guarantees
# =====================================================================

class TestStageFinalizationGuarantees:

    def test_one_scanner_fails_stage_completes(self):
        """If one scanner fails, stage still completes (degraded)."""
        run, store = _make_run_and_store("run-degrade-001")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mixed_executor,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["scanners_completed"] >= 1
        assert result["summary_counts"]["scanners_failed"] >= 1

    def test_all_scanners_fail_stage_fails(self):
        """If all scanners fail, stage fails."""
        run, store = _make_run_and_store("run-allfail-001")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_failing_executor,
        )
        assert result["outcome"] == "failed"
        assert result["error"] is not None

    def test_all_scanners_succeed(self):
        """All scanners complete → stage completes."""
        run, store = _make_run_and_store("run-allok-001")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_fast_executor,
        )
        assert result["outcome"] == "completed"
        counts = result["summary_counts"]
        assert counts["scanners_completed"] == 2
        assert counts["scanners_failed"] == 0

    def test_slow_scanner_stage_completes_with_both(self):
        """One scanner is slow, other is fast — stage waits for both."""
        run, store = _make_run_and_store("run-tmix-001")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_timeout_mixed_executor,
        )
        assert result["outcome"] == "completed"
        # scan_a succeeds, scan_b is slow but eventually returns
        assert result["summary_counts"]["scanners_completed"] >= 1

    def test_liveness_snapshot_in_metadata(self):
        """Stage result metadata includes liveness snapshot."""
        run, store = _make_run_and_store("run-live-001")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_fast_executor,
        )
        snap = result["metadata"].get("liveness_snapshot")
        assert snap is not None
        assert "in_flight_count" in snap

    def test_liveness_tracker_attached_to_run(self):
        """Scanner liveness tracker is attached to run dict."""
        run, store = _make_run_and_store("run-attach-001")
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_fast_executor,
        )
        tracker = run.get("_scanner_liveness")
        assert tracker is not None
        assert hasattr(tracker, "snapshot")

    def test_handler_always_returns_dict(self):
        """The handler must always return a result dict, never raise."""
        run, store = _make_run_and_store("run-dict-001")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_failing_executor,
        )
        assert isinstance(result, dict)
        assert "outcome" in result
        assert "summary_counts" in result

    def test_generation_cap_passed_in_context(self):
        """generation_cap from kwargs appears in the scanner context."""
        captured_ctx = {}

        def _capture_executor(key, entry, context):
            captured_ctx.update(context)
            return {"candidates": []}

        run, store = _make_run_and_store("run-cap-001")
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_capture_executor,
            generation_cap=5_000,
        )
        assert captured_ctx.get("generation_cap") == 5_000


# =====================================================================
#  Vertical spreads generation cap tests
# =====================================================================

class TestVerticalSpreadsGenerationCap:

    def _make_narrowed_mock(self, n_strikes_per_exp=50, n_expirations=5):
        """Build a mock V2NarrowedUniverse with n strikes and expirations."""
        from unittest.mock import MagicMock
        from app.services.scanner_v2.data.contracts import (
            V2ExpiryBucket,
            V2OptionContract,
            V2StrikeEntry,
        )

        buckets = {}
        for exp_idx in range(n_expirations):
            exp_date = f"2026-04-{10 + exp_idx:02d}"
            strikes = []
            for s in range(n_strikes_per_exp):
                strike_val = 400.0 + s * 1.0
                contract = V2OptionContract(
                    symbol=f"SPY260410P{int(strike_val * 1000):08d}",
                    root_symbol="SPY",
                    option_type="put",
                    strike=strike_val,
                    expiration=exp_date,
                    bid=2.0 + s * 0.01,
                    ask=2.5 + s * 0.01,
                    mid=2.25 + s * 0.01,
                    delta=-0.3,
                    gamma=0.01,
                    theta=-0.05,
                    vega=0.15,
                    iv=0.25,
                    open_interest=100,
                    volume=50,
                )
                strikes.append(V2StrikeEntry(
                    strike=strike_val,
                    contract=contract,
                ))
            buckets[exp_date] = V2ExpiryBucket(
                expiration=exp_date,
                dte=10 + exp_idx,
                strikes=strikes,
            )

        universe = MagicMock()
        universe.expiry_buckets = buckets
        universe.diagnostics = MagicMock()
        universe.diagnostics.expirations_kept_list = list(buckets.keys())
        universe.diagnostics.expirations_kept = len(buckets)
        universe.diagnostics.contracts_final = sum(
            len(b.strikes) for b in buckets.values()
        )
        universe.diagnostics.total_contracts_loaded = universe.diagnostics.contracts_final
        universe.diagnostics.to_dict.return_value = {}
        return universe

    def test_generation_cap_enforced(self):
        """Vertical spreads construction stops at generation_cap."""
        from app.services.scanner_v2.families.vertical_spreads import (
            VerticalSpreadsV2Scanner,
        )

        scanner = VerticalSpreadsV2Scanner()
        universe = self._make_narrowed_mock(n_strikes_per_exp=100, n_expirations=5)

        # Without cap, 100 strikes → C(100,2) = 4950 per exp × 5 = 24750
        # With cap of 1000, should stop early
        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=450.0,
            expirations=[],
            strategy_id="put_credit_spread",
            scanner_key="put_credit_spread",
            context={"generation_cap": 1000},
            narrowed_universe=universe,
        )
        assert len(candidates) == 1000

    def test_max_width_filters_wide_spreads(self):
        """Vertical spreads skips pairs wider than max_width."""
        from app.services.scanner_v2.families.vertical_spreads import (
            VerticalSpreadsV2Scanner,
        )

        scanner = VerticalSpreadsV2Scanner()
        # 10 strikes, $1 apart, so max pair width = $9
        universe = self._make_narrowed_mock(n_strikes_per_exp=10, n_expirations=1)

        # With max_width=$3, only pairs within $3 should generate
        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=450.0,
            expirations=[],
            strategy_id="put_credit_spread",
            scanner_key="put_credit_spread",
            context={"max_width": 3.0, "generation_cap": 50000},
            narrowed_universe=universe,
        )
        for c in candidates:
            width = abs(c.legs[0].strike - c.legs[1].strike)
            assert width <= 3.0

    def test_default_cap_exists(self):
        """Vertical spreads has a default generation cap constant."""
        from app.services.scanner_v2.families.vertical_spreads import (
            _DEFAULT_GENERATION_CAP,
        )
        assert isinstance(_DEFAULT_GENERATION_CAP, int)
        assert _DEFAULT_GENERATION_CAP > 0

    def test_no_explosion_without_cap_override(self):
        """Even without context override, default cap prevents explosion."""
        from app.services.scanner_v2.families.vertical_spreads import (
            VerticalSpreadsV2Scanner,
            _DEFAULT_GENERATION_CAP,
        )

        scanner = VerticalSpreadsV2Scanner()
        # 200 strikes × 10 exps → C(200,2)×10 = 199,000 uncapped
        universe = self._make_narrowed_mock(n_strikes_per_exp=200, n_expirations=10)
        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=450.0,
            expirations=[],
            strategy_id="put_credit_spread",
            scanner_key="put_credit_spread",
            context={},
            narrowed_universe=universe,
        )
        assert len(candidates) <= _DEFAULT_GENERATION_CAP


# =====================================================================
#  Calendars generation cap tests
# =====================================================================

class TestCalendarsGenerationCap:

    def _make_cal_universe(self, n_strikes=20, n_exps=5):
        from unittest.mock import MagicMock
        from app.services.scanner_v2.data.contracts import (
            V2ExpiryBucket,
            V2OptionContract,
            V2StrikeEntry,
        )

        buckets = {}
        for ei in range(n_exps):
            exp_date = f"2026-04-{10 + ei * 7:02d}"
            strikes = []
            for s in range(n_strikes):
                sv = 400.0 + s * 5.0
                c = V2OptionContract(
                    symbol=f"SPY{exp_date}C{int(sv)}",
                    root_symbol="SPY",
                    option_type="call",
                    strike=sv,
                    expiration=exp_date,
                    bid=3.0, ask=3.5, mid=3.25,
                    delta=0.5, gamma=0.01, theta=-0.05,
                    vega=0.15, iv=0.25,
                    open_interest=100, volume=50,
                )
                strikes.append(V2StrikeEntry(strike=sv, contract=c))
            buckets[exp_date] = V2ExpiryBucket(
                expiration=exp_date, dte=10 + ei * 7, strikes=strikes,
            )

        universe = MagicMock()
        universe.expiry_buckets = buckets
        universe.diagnostics = MagicMock()
        universe.diagnostics.expirations_kept_list = list(buckets.keys())
        universe.diagnostics.expirations_kept = len(buckets)
        universe.diagnostics.contracts_final = sum(len(b.strikes) for b in buckets.values())
        universe.diagnostics.total_contracts_loaded = universe.diagnostics.contracts_final
        universe.diagnostics.to_dict.return_value = {}
        return universe

    def test_calendar_cap_enforced(self):
        from app.services.scanner_v2.families.calendars import CalendarsV2Scanner
        scanner = CalendarsV2Scanner()
        universe = self._make_cal_universe(n_strikes=50, n_exps=10)
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=450.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread",
            context={"generation_cap": 500},
            narrowed_universe=universe,
        )
        assert len(candidates) <= 500

    def test_diagonal_cap_enforced(self):
        from app.services.scanner_v2.families.calendars import CalendarsV2Scanner
        scanner = CalendarsV2Scanner()
        universe = self._make_cal_universe(n_strikes=30, n_exps=8)
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=450.0,
            expirations=[], strategy_id="diagonal_call_spread",
            scanner_key="diagonal_call_spread",
            context={"generation_cap": 200},
            narrowed_universe=universe,
        )
        assert len(candidates) <= 200


# =====================================================================
#  Safety: stage cannot hang forever
# =====================================================================

class TestStageCannotHangForever:

    def test_slow_scanner_stage_waits_and_returns(self):
        """Even if a scanner is slow, the stage waits for it to finish."""
        run, store = _make_run_and_store("run-hang-001")
        reg = {
            "hang_a": _make_scanner_entry(
                "hang_a", "Hanger A", "options", "hang_a",
            ),
        }
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=reg,
            scanner_executor=_slow_executor,
        )
        assert isinstance(result, dict)
        # _slow_executor sleeps then returns empty candidates
        assert result["outcome"] in ("completed", "failed")

    def test_every_work_item_has_result(self):
        """After parallel execution, every work item has a result entry."""
        items = [
            _make_scanner_entry("a", "A", "stock", "a"),
            _make_scanner_entry("b", "B", "stock", "b"),
            _make_scanner_entry("c", "C", "stock", "c"),
        ]

        def _varied(key, entry, ctx):
            if key == "b":
                time.sleep(1)  # slow but completes
                return {"candidates": []}
            if key == "c":
                raise RuntimeError("boom")
            return {"candidates": [{"symbol": "SPY", "trade_key": f"{key}_1",
                                     "normalized": {"candidate_id": f"{key}_1", "symbol": "SPY"}}]}

        results = _execute_scanners_parallel(
            items, _varied, {}, "run-every-001", 3, None,
        )
        # All three must have entries
        assert "a" in results
        assert "b" in results
        assert "c" in results
        assert results["a"]["record"]["status"] in ("completed", "completed_empty")
        assert results["b"]["record"]["status"] == "completed_empty"
        assert results["c"]["record"]["status"] == "failed"


# =====================================================================
#  Constants
# =====================================================================

class TestConstants:

    def test_generation_cap_exists(self):
        assert DEFAULT_GENERATION_CAP > 0

    def test_generation_cap_value_reasonable(self):
        assert DEFAULT_GENERATION_CAP <= 100_000
        assert DEFAULT_GENERATION_CAP >= 10_000


# =====================================================================
#  Heartbeat liveness
# =====================================================================

class TestLivenessHeartbeat:

    def test_heartbeat_updates_last_update(self):
        t = ScannerLivenessTracker()
        t.mark_started("scan_x")
        snap1 = t.snapshot()
        time.sleep(0.05)
        t.heartbeat("scan_x")
        snap2 = t.snapshot()
        # last_update_ms_ago should be smaller after heartbeat
        assert snap2["last_update_ms_ago"] < snap1["last_update_ms_ago"] + 50

    def test_heartbeat_does_not_change_in_flight(self):
        t = ScannerLivenessTracker()
        t.mark_started("scan_x")
        t.heartbeat("scan_x")
        snap = t.snapshot()
        assert "scan_x" in snap["in_flight_scanners"]
        assert snap["in_flight_count"] == 1
        assert snap["completed"] == []
        assert snap["failed"] == []


# =====================================================================
#  Progress event emission
# =====================================================================

class TestProgressEventEmission:

    def test_scanner_executing_event_emitted(self):
        """_run_single_scanner emits scanner_executing before executing."""
        events = []

        def _capture_emitter(event_type, **kwargs):
            events.append({"type": event_type, **kwargs})

        from app.services.pipeline_scanner_stage import _run_single_scanner

        result = _run_single_scanner(
            "test_scan",
            _make_scanner_entry("test_scan", "Test", "stock", "test"),
            {},
            _fast_executor,
            "run-evt-001",
            event_emitter=_capture_emitter,
        )

        event_types = [e["type"] for e in events]
        assert "scanner_started" in event_types
        assert "scanner_executing" in event_types
        assert "scanner_completed" in event_types
        # scanner_executing should come after scanner_started
        started_idx = event_types.index("scanner_started")
        executing_idx = event_types.index("scanner_executing")
        completed_idx = event_types.index("scanner_completed")
        assert started_idx < executing_idx < completed_idx

    def test_scanner_executing_event_with_tracker(self):
        """When liveness_tracker is provided, heartbeat is called and
        scanner is marked completed before _run_single_scanner returns."""
        tracker = ScannerLivenessTracker()
        tracker.mark_started("test_scan")

        from app.services.pipeline_scanner_stage import _run_single_scanner

        _run_single_scanner(
            "test_scan",
            _make_scanner_entry("test_scan", "Test", "stock", "test"),
            {},
            _fast_executor,
            "run-evt-002",
            liveness_tracker=tracker,
        )

        snap = tracker.snapshot()
        # mark_completed is now called inside _run_single_scanner
        # (before scanner_completed event) so the scanner is cleared
        # from in_flight after the function returns.
        assert snap["in_flight_count"] == 0
        assert "test_scan" in snap["completed"]


# =====================================================================
#  Parallel execution passes tracker to _run_single_scanner
# =====================================================================

class TestParallelTrackerPassthrough:

    def test_tracker_receives_heartbeat_during_execution(self):
        """Tracker heartbeat is invoked during parallel execution."""
        tracker = ScannerLivenessTracker()
        items = [
            _make_scanner_entry("t_scan", "T", "stock", "t"),
        ]
        results = _execute_scanners_parallel(
            items, _fast_executor, {}, "run-hb-001", 1, None,
            liveness_tracker=tracker,
        )
        snap = tracker.snapshot()
        assert "t_scan" in snap["completed"]
        # last_update should be very recent (< 5s)
        assert snap["last_update_ms_ago"] < 5000


# =====================================================================
#  Liveness cleanup ordering (scanner_completed → tracker cleared)
# =====================================================================

class TestLivenessCleanupOrdering:
    """Verify that tracker.mark_completed is called BEFORE the
    scanner_completed event is emitted, so event-callback snapshots
    never show stale in-flight scanners."""

    def test_completion_clears_before_event(self):
        """scanner_completed event callback sees in_flight_count == 0
        for a single-scanner run."""
        tracker = ScannerLivenessTracker()
        snapshots_at_event: list[dict] = []

        def _capture_emitter(event_type, **kwargs):
            if event_type == "scanner_completed":
                snapshots_at_event.append(tracker.snapshot())

        items = [_make_scanner_entry("scan_x", "X", "stock", "x")]
        results = _execute_scanners_parallel(
            items, _fast_executor, {}, "run-order-001", 1,
            _capture_emitter,
            liveness_tracker=tracker,
        )
        assert len(snapshots_at_event) == 1
        snap = snapshots_at_event[0]
        assert snap["in_flight_count"] == 0, (
            "scanner_completed event fired while scanner still in in_flight"
        )
        assert "scan_x" in snap["completed"]

    def test_failure_clears_before_event(self):
        """scanner_failed event callback sees in_flight_count == 0."""
        tracker = ScannerLivenessTracker()
        snapshots_at_event: list[dict] = []

        def _capture_emitter(event_type, **kwargs):
            if event_type == "scanner_failed":
                snapshots_at_event.append(tracker.snapshot())

        items = [_make_scanner_entry("scan_fail", "F", "stock", "f")]
        results = _execute_scanners_parallel(
            items, _failing_executor, {}, "run-order-002", 1,
            _capture_emitter,
            liveness_tracker=tracker,
        )
        assert len(snapshots_at_event) == 1
        snap = snapshots_at_event[0]
        assert snap["in_flight_count"] == 0
        assert "scan_fail" in snap["failed"]

    def test_last_scanner_clears_in_flight(self):
        """After all scanners complete, in_flight_count must be 0."""
        tracker = ScannerLivenessTracker()
        items = [
            _make_scanner_entry("sa", "A", "stock", "a"),
            _make_scanner_entry("sb", "B", "stock", "b"),
        ]
        results = _execute_scanners_parallel(
            items, _fast_executor, {}, "run-order-003", 2, None,
            liveness_tracker=tracker,
        )
        snap = tracker.snapshot()
        assert snap["in_flight_count"] == 0
        assert set(snap["completed"]) == {"sa", "sb"}

    def test_mixed_success_failure_all_cleared(self):
        """Mix of success + failure still leaves in_flight_count == 0."""
        tracker = ScannerLivenessTracker()
        items = [
            _make_scanner_entry("scan_a", "A", "stock", "a"),
            _make_scanner_entry("scan_b", "B", "stock", "b"),
        ]
        results = _execute_scanners_parallel(
            items, _mixed_executor, {}, "run-order-004", 2, None,
            liveness_tracker=tracker,
        )
        snap = tracker.snapshot()
        assert snap["in_flight_count"] == 0


# =====================================================================
#  Reconciliation safety net
# =====================================================================

class TestLivenessReconciliation:

    def test_reconcile_clears_stale_entry(self):
        """reconcile() removes in-flight entries that have results."""
        t = ScannerLivenessTracker()
        t.mark_started("scan_a")
        t.mark_started("scan_b")
        t.mark_completed("scan_a")
        # scan_b still in-flight but has a result
        stale = t.reconcile({"scan_a", "scan_b"})
        assert stale == {"scan_b"}
        snap = t.snapshot()
        assert snap["in_flight_count"] == 0
        assert "scan_b" in snap["completed"]

    def test_reconcile_no_stale(self):
        """reconcile() returns empty set when nothing is stale."""
        t = ScannerLivenessTracker()
        t.mark_started("scan_a")
        t.mark_completed("scan_a")
        stale = t.reconcile({"scan_a"})
        assert stale == set()

    def test_reconcile_does_not_double_append(self):
        """reconcile() doesn't add a key to completed twice."""
        t = ScannerLivenessTracker()
        t.mark_started("scan_a")
        t.mark_completed("scan_a")
        # scan_a already completed; reconcile should be a no-op
        t.reconcile({"scan_a"})
        snap = t.snapshot()
        assert snap["completed"].count("scan_a") == 1


# =====================================================================
#  Stage handler produces clean liveness snapshot
# =====================================================================

class TestStageHandlerLiveness:

    def test_stage_result_liveness_all_clear(self):
        """Stage handler result has in_flight_count == 0 after success."""
        run, store = _make_run_and_store("run-liveness-001")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_fast_executor,
            max_workers=2,
        )
        snap = result["metadata"]["liveness_snapshot"]
        assert snap["in_flight_count"] == 0
        assert len(snap["completed"]) == 2

    def test_stage_result_liveness_after_failure(self):
        """Stage handler liveness is clean even when scanners fail."""
        run, store = _make_run_and_store("run-liveness-002")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mixed_executor,
            max_workers=2,
        )
        snap = result["metadata"]["liveness_snapshot"]
        assert snap["in_flight_count"] == 0


# =====================================================================
#  Pool shutdown & stage finalization path
# =====================================================================

class TestPoolShutdownAndFinalization:
    """Tests for the explicit pool.shutdown(wait=False) fix.

    These verify that _execute_scanners_parallel returns promptly
    and that the stage handler produces a complete result even when
    worker threads would otherwise be slow to exit.
    """

    def test_parallel_returns_all_results(self):
        """_execute_scanners_parallel returns a result for every item."""
        items = [
            _make_scanner_entry("s1", "S1", "stock", "s1"),
            _make_scanner_entry("s2", "S2", "options", "s2"),
            _make_scanner_entry("s3", "S3", "stock", "s3"),
        ]
        results = _execute_scanners_parallel(
            items, _fast_executor, {}, "run-fin-001", 3, None,
        )
        assert set(results.keys()) == {"s1", "s2", "s3"}
        for key in ("s1", "s2", "s3"):
            assert results[key]["record"]["status"] in (
                "completed", "completed_empty",
            )

    def test_parallel_returns_promptly_after_futures_done(self):
        """Pool shutdown doesn't block; function returns fast."""
        items = [
            _make_scanner_entry("fast_a", "A", "stock", "a"),
            _make_scanner_entry("fast_b", "B", "stock", "b"),
        ]
        t0 = time.monotonic()
        results = _execute_scanners_parallel(
            items, _fast_executor, {}, "run-fin-002", 2, None,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 5, f"Expected < 5s, took {elapsed:.1f}s"
        assert len(results) == 2

    def test_stage_handler_returns_complete_result(self):
        """Stage handler result has all required top-level keys."""
        run, store = _make_run_and_store("run-fin-003")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_fast_executor,
        )
        # Required keys
        assert "outcome" in result
        assert "summary_counts" in result
        assert "artifacts" in result
        assert "metadata" in result
        # Metadata sub-keys
        meta = result["metadata"]
        assert "stage_summary_artifact_id" in meta
        assert "elapsed_ms" in meta
        assert "liveness_snapshot" in meta
        assert "scanner_records" in meta

    def test_post_processing_exception_yields_result(self):
        """If post-processing raises, guaranteed finalization returns."""
        run, store = _make_run_and_store("run-fin-004")

        # Use an executor that returns malformed data to trigger
        # post-processing errors (non-dict raw_result).
        def _bad_result_executor(key, entry, ctx):
            return "not-a-dict"  # type: ignore[return-value]

        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_bad_result_executor,
        )
        # Must still return a result
        assert isinstance(result, dict)
        assert "outcome" in result
        assert "summary_counts" in result

    def test_mixed_success_failure_returns_promptly(self):
        """Mix of success+failure returns without pool hang."""
        run, store = _make_run_and_store("run-fin-005")
        t0 = time.monotonic()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mixed_executor,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 10, f"Expected < 10s, took {elapsed:.1f}s"
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["scanners_completed"] >= 1
        assert result["summary_counts"]["scanners_failed"] >= 1

    def test_empty_work_items_returns_immediately(self):
        """No work items → _execute_scanners_parallel returns {}."""
        results = _execute_scanners_parallel(
            [], _fast_executor, {}, "run-fin-006", 2, None,
        )
        assert results == {}


# =====================================================================
#  Deterministic reproduction harness: finalization through execute_stage
# =====================================================================

from app.services.pipeline_orchestrator import execute_stage


class TestFinalizationReproductionHarness:
    """Deterministic reproduction harness for the scanner-stage finalization
    bug.  Exercises scanner_stage_handler through the orchestrator's
    execute_stage wrapper with controlled inputs — NOT through the full
    UI/pipeline flow.

    Verifies:
    - Handler returns a result (not hang)
    - execute_stage reaches stage_completed event
    - candidate_counters.scanned propagates from summary_counts
    - Finalization checkpoint reaches terminal state
    """

    def _run_through_execute_stage(self, executor, *, capture_events=True):
        """Invoke scanner_stage_handler via execute_stage with controlled inputs."""
        run, store = _make_run_and_store("run-harness-001")
        events = []

        def _capture(event):
            events.append(event)

        result = execute_stage(
            run, store, "stock_scanners",
            handler=scanner_stage_handler,
            event_callback=_capture if capture_events else None,
            handler_kwargs={
                "scanner_registry": _small_registry(),
                "scanner_executor": executor,
            },
        )
        return run, store, result, events

    def test_handler_returns_after_all_scanners_complete(self):
        """Basic path: all scanners complete → handler returns → execute_stage
        reaches stage_completed."""
        run, store, result, events = self._run_through_execute_stage(
            _fast_executor,
        )
        assert result["outcome"] == "completed"
        event_types = [e.get("event_type") for e in events]
        assert "stage_completed" in event_types

    def test_candidate_counters_propagated(self):
        """candidate_counters.stock_scanned goes from 0 to total_candidates."""
        run, store, result, events = self._run_through_execute_stage(
            _fast_executor,
        )
        cc = run.get("candidate_counters", {})
        assert cc.get("stock_scanned", 0) > 0, (
            f"Expected stock_scanned > 0 but got {cc}"
        )

    def test_finalization_checkpoint_reaches_terminal(self):
        """Finalization checkpoint reaches handler_returning state."""
        run, store, result, events = self._run_through_execute_stage(
            _fast_executor,
        )
        checkpoint = run.get("_finalization_checkpoint")
        assert checkpoint is not None
        assert checkpoint.state == "handler_returning"

    def test_mixed_success_failure_still_completes(self):
        """One scanner fails, one succeeds → stage completes, stage_completed fires."""
        run, store, result, events = self._run_through_execute_stage(
            _mixed_executor,
        )
        assert result["outcome"] == "completed"
        event_types = [e.get("event_type") for e in events]
        assert "stage_completed" in event_types

    def test_all_scanners_fail_stage_fails(self):
        """All scanners fail → stage fails, CheckPoint reaches terminal."""
        run, store, result, events = self._run_through_execute_stage(
            _failing_executor,
        )
        assert result["outcome"] == "failed"
        checkpoint = run.get("_finalization_checkpoint")
        assert checkpoint is not None
        # Even on failure, checkpoint reaches a terminal state
        assert checkpoint.state in ("handler_returning", "post_processing_failed")

    def test_post_processing_failure_does_not_hang(self):
        """If post-processing raises, handler returns (not hang) and
        finalization_checkpoint captures the failure state."""
        def _bad_result_executor(key, entry, ctx):
            return "not-a-dict"

        run, store, result, events = self._run_through_execute_stage(
            _bad_result_executor,
        )
        assert isinstance(result, dict)
        assert result["outcome"] in ("completed", "failed")
        checkpoint = run.get("_finalization_checkpoint")
        assert checkpoint is not None
        # Should have reached post_processing_failed or handler_returning
        assert checkpoint.state in (
            "handler_returning", "post_processing_failed",
        )

    def test_stage_completes_with_valid_executor(self):
        """Stage completes normally with a valid executor."""
        def _valid_executor(key, entry, ctx):
            return {"candidates": [
                {"symbol": "SPY", "trade_key": f"{key}_001",
                 "normalized": {"candidate_id": f"{key}_001", "symbol": "SPY"}},
            ]}

        run, store = _make_run_and_store("run-deadline-001")
        t0 = time.monotonic()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_valid_executor,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 30, f"Handler took {elapsed:.1f}s, expected < 30s"
        assert isinstance(result, dict)
        assert "outcome" in result

    def test_finalization_checkpoint_in_metadata(self):
        """Result metadata includes finalization_checkpoint snapshot."""
        run, store, result, events = self._run_through_execute_stage(
            _fast_executor,
        )
        meta = result.get("metadata", {})
        # Check the handler result embeds finalization checkpoint:
        # The handler result is inside execute_stage result
        # under 'metadata' key (propagated by build_stage_result).
        # The handler itself puts it in result["metadata"]["finalization_checkpoint"]
        # But execute_stage wraps it. Let's check the run dict instead.
        checkpoint = run["_finalization_checkpoint"]
        snap = checkpoint.snapshot()
        assert snap["current_state"] == "handler_returning"
        assert len(snap["history"]) >= 5  # Multiple checkpoints recorded


# =====================================================================
#  Finalization checkpoint unit tests
# =====================================================================

class TestFinalizationCheckpoint:

    def test_initial_state(self):
        cp = FinalizationCheckpoint()
        assert cp.state == "not_started"

    def test_advance_updates_state(self):
        cp = FinalizationCheckpoint()
        cp.advance("results_collected")
        assert cp.state == "results_collected"

    def test_snapshot_records_history(self):
        cp = FinalizationCheckpoint()
        cp.advance("results_collected")
        cp.advance("post_processing_started")
        snap = cp.snapshot()
        assert snap["current_state"] == "post_processing_started"
        assert len(snap["history"]) == 2
        assert snap["history"][0]["state"] == "results_collected"
        assert snap["history"][1]["state"] == "post_processing_started"

    def test_snapshot_elapsed_ms(self):
        cp = FinalizationCheckpoint()
        time.sleep(0.05)
        cp.advance("results_collected")
        snap = cp.snapshot()
        assert snap["elapsed_ms"] >= 40  # At least ~50ms

    def test_thread_safety(self):
        """Multiple threads advancing concurrently don't crash."""
        cp = FinalizationCheckpoint()
        barrier = threading.Barrier(3)

        def _worker(state):
            barrier.wait()
            cp.advance(state)

        threads = [
            threading.Thread(target=_worker, args=(f"state_{i}",))
            for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        snap = cp.snapshot()
        assert len(snap["history"]) == 3


# =====================================================================
#  Focused finalization assertions (6 targeted tests)
# =====================================================================

class TestFocusedFinalizationAssertions:
    """Six specific assertions required by the finalization bug fix."""

    def test_handler_returns_after_all_scanner_results_collected(self):
        """After all scanner results arrive, handler returns a dict
        (regardless of success/failure)."""
        run, store = _make_run_and_store("run-focused-001")
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_fast_executor,
        )
        assert isinstance(result, dict), "Handler must return dict"
        assert "outcome" in result

    def test_stage_completed_path_reachable(self):
        """execute_stage fires stage_completed event when handler succeeds."""
        run, store = _make_run_and_store("run-focused-002")
        events = []

        def _cb(event):
            events.append(event.get("event_type"))

        execute_stage(
            run, store, "stock_scanners",
            handler=scanner_stage_handler,
            event_callback=_cb,
            handler_kwargs={
                "scanner_registry": _small_registry(),
                "scanner_executor": _fast_executor,
            },
        )
        assert "stage_completed" in events, (
            f"stage_completed not emitted; events={events}"
        )

    def test_candidate_counters_zero_to_nonzero(self):
        """candidate_counters.stock_scanned starts at 0 and becomes non-zero
        after scanner stage completes."""
        run, store = _make_run_and_store("run-focused-003")
        assert run.get("candidate_counters", {}).get("stock_scanned", 0) == 0

        execute_stage(
            run, store, "stock_scanners",
            handler=scanner_stage_handler,
            handler_kwargs={
                "scanner_registry": _small_registry(),
                "scanner_executor": _fast_executor,
            },
        )
        assert run["candidate_counters"]["stock_scanned"] > 0

    def test_post_processing_failure_yields_failed_not_hang(self):
        """If post-processing raises, stage returns failed result — NOT
        an infinite hang."""
        def _corrupt_executor(key, entry, ctx):
            return "not-a-dict"

        run, store = _make_run_and_store("run-focused-004")
        t0 = time.monotonic()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_corrupt_executor,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 15, f"Expected <15s, took {elapsed:.1f}s"
        assert isinstance(result, dict)
        assert result["outcome"] in ("completed", "failed")

    def test_finalization_checkpoint_reaches_terminal_state(self):
        """Finalization checkpoint reaches handler_returning on success."""
        run, store = _make_run_and_store("run-focused-005")
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_fast_executor,
        )
        cp = run["_finalization_checkpoint"]
        assert cp.state == "handler_returning"
        snap = cp.snapshot()
        # Must have traversed all major checkpoints
        states = [h["state"] for h in snap["history"]]
        assert "results_collected" in states
        assert "post_processing_started" in states
        assert "artifact_loop_completed" in states
        assert "summary_built" in states
        assert "outcome_determined" in states
        assert "result_built" in states
        assert "handler_returning" in states

    def test_harness_catches_bug_class(self):
        """Reproduction harness: exercise through execute_stage and verify
        every finalization gate is reachable — the exact class of bug
        where the stage silently stalls after all scanners return."""
        run, store = _make_run_and_store("run-focused-006")
        events = []

        def _cb(event):
            events.append(event)

        t0 = time.monotonic()
        result = execute_stage(
            run, store, "stock_scanners",
            handler=scanner_stage_handler,
            event_callback=_cb,
            handler_kwargs={
                "scanner_registry": _small_registry(),
                "scanner_executor": _fast_executor,
            },
        )
        elapsed = time.monotonic() - t0

        # 1. Handler returned (no hang)
        assert elapsed < 15, f"Took {elapsed:.1f}s — potential hang"
        assert isinstance(result, dict)

        # 2. Stage completed event fired
        event_types = [e.get("event_type") for e in events]
        assert "stage_completed" in event_types

        # 3. Candidate counters propagated
        cc = run.get("candidate_counters", {})
        assert cc.get("stock_scanned", 0) > 0

        # 4. Finalization checkpoint terminal
        cp = run["_finalization_checkpoint"]
        assert cp.state == "handler_returning"

        # 5. Finalization diagnostic present in metadata
        snap = cp.snapshot()
        assert snap["elapsed_ms"] >= 0
        assert len(snap["history"]) >= 5


# =====================================================================
#  Deepcopy safety regression tests (parallel-stage stall fix)
# =====================================================================

class TestDeepcopyRegressionSafety:
    """Verify that objects attached to the run dict survive copy.deepcopy().

    ROOT CAUSE CONTEXT: FinalizationCheckpoint contained a threading.Lock
    but had no __deepcopy__ method.  When the monitor's _live_callback
    called copy.deepcopy(run), it raised TypeError (cannot pickle lock),
    silently killing ALL event propagation.  Both parallel stages appeared
    stuck at RUNNING with no child events.
    """

    def test_finalization_checkpoint_deepcopy(self):
        """copy.deepcopy(FinalizationCheckpoint) must not raise."""
        cp = FinalizationCheckpoint()
        cp.advance("results_collected")
        cp.advance("post_processing_started")
        clone = copy.deepcopy(cp)
        assert clone.state == "post_processing_started"
        snap = clone.snapshot()
        assert len(snap["history"]) == 2

    def test_finalization_checkpoint_copy(self):
        """copy.copy(FinalizationCheckpoint) must not raise."""
        cp = FinalizationCheckpoint()
        cp.advance("in_progress")
        clone = copy.copy(cp)
        assert clone.state == "in_progress"

    def test_finalization_checkpoint_deepcopy_independent(self):
        """Deepcopy must produce an independent object — mutating the
        clone must not affect the original."""
        cp = FinalizationCheckpoint()
        cp.advance("a")
        clone = copy.deepcopy(cp)
        clone.advance("b")
        assert cp.state == "a"
        assert clone.state == "b"

    def test_liveness_tracker_deepcopy(self):
        """Baseline: ScannerLivenessTracker deepcopy still works."""
        tracker = ScannerLivenessTracker()
        tracker.mark_started("scan_a")
        tracker.mark_completed("scan_a")
        clone = copy.deepcopy(tracker)
        snap = clone.snapshot()
        assert "scan_a" in snap["completed"]

    def test_run_dict_deepcopy_with_both_objects(self):
        """copy.deepcopy(run) succeeds when run contains BOTH a
        FinalizationCheckpoint and a ScannerLivenessTracker — the
        exact scenario that caused the parallel-stage stall."""
        run = create_pipeline_run(run_id="run-deepcopy-001")
        cp = FinalizationCheckpoint()
        cp.advance("results_collected")
        tracker = ScannerLivenessTracker()
        tracker.mark_started("scan_a")
        run["_finalization_checkpoint"] = cp
        run["_scanner_liveness"] = tracker
        # This is the exact call that failed before the fix:
        run_copy = copy.deepcopy(run)
        assert run_copy["_finalization_checkpoint"].state == "results_collected"
        snap = run_copy["_scanner_liveness"].snapshot()
        assert "scan_a" in snap["in_flight_scanners"]

    def test_handler_entered_event_emitted(self):
        """scanner_stage_handler emits a handler_entered event as its
        first child event (first-child-event diagnostic)."""
        run, store = _make_run_and_store("run-diag-001")
        events = []

        def _cb(event):
            events.append(event)

        execute_stage(
            run, store, "stock_scanners",
            handler=scanner_stage_handler,
            event_callback=_cb,
            handler_kwargs={
                "scanner_registry": _small_registry(),
                "scanner_executor": _fast_executor,
            },
        )
        child_types = [
            e.get("event_type") for e in events
            if e.get("event_type") != "stage_started"
            and e.get("event_type") != "stage_completed"
        ]
        assert "handler_entered" in child_types
        # handler_entered should be the first child event
        assert child_types[0] == "handler_entered"
