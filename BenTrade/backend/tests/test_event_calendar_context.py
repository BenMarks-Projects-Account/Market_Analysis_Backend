"""
Tests for Event / Macro Calendar Context v1.1
===============================================

Covers: contract shape, macro event classification, company events,
candidate overlap, portfolio overlap, risk state derivation,
event windows, partial data, timing, source semantics, elapsed
event marking, timing honesty, and integration scenarios.
"""

import datetime as dt
import pytest

from app.services.event_calendar_context import (
    build_event_context,
    _classify_importance,
    _classify_category,
    _normalise_event_key,
    _hours_to_window,
    _derive_event_risk_state,
    _EVENT_CONTEXT_VERSION,
)


# ── Helpers / Factories ──────────────────────────────────────────────

_REF = dt.datetime(2026, 3, 10, 14, 0, 0, tzinfo=dt.timezone.utc)


def _macro(name: str, hours_ahead: float = 48.0, **kw) -> dict:
    """Create a macro event dict hours_ahead from _REF."""
    base = {
        "event_name": name,
        "event_type": "macro",
        "event_time": (_REF + dt.timedelta(hours=hours_ahead)).isoformat(),
    }
    base.update(kw)
    return base


def _earnings(symbol: str, hours_ahead: float = 48.0, **kw) -> dict:
    """Create an earnings event dict."""
    base = {
        "event_name": f"{symbol} Q1 Earnings",
        "event_type": "earnings",
        "event_time": (_REF + dt.timedelta(hours=hours_ahead)).isoformat(),
        "related_symbols": [symbol],
        "scope": "single_stock",
    }
    base.update(kw)
    return base


def _candidate(symbol: str = "SPY", dte: int = 30, **kw) -> dict:
    """Bare-minimum candidate dict for overlap tests."""
    base = {
        "symbol": symbol,
        "entry_context": {"dte": dte},
    }
    base.update(kw)
    return base


def _position(symbol: str) -> dict:
    """Bare-minimum position dict for portfolio tests."""
    return {"symbol": symbol}


# ═══════════════════════════════════════════════════════════════════
#  1. CONTRACT SHAPE
# ═══════════════════════════════════════════════════════════════════

class TestContractShape:
    """Output has required top-level keys."""

    REQUIRED_KEYS = {
        "event_context_version", "generated_at", "status", "summary",
        "event_risk_state",
        "upcoming_macro_events", "upcoming_company_events",
        "candidate_event_overlap", "portfolio_event_overlap",
        "event_windows", "risk_flags", "warning_flags",
        "evidence", "metadata",
    }

    def test_full_data_keys(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            company_events=[_earnings("AAPL", 48)],
            candidate=_candidate(),
            positions=[_position("AAPL")],
            reference_time=_REF,
        )
        assert self.REQUIRED_KEYS <= set(r.keys())

    def test_no_data_keys(self):
        r = build_event_context(reference_time=_REF)
        assert self.REQUIRED_KEYS <= set(r.keys())

    def test_version(self):
        r = build_event_context(reference_time=_REF)
        assert r["event_context_version"] == _EVENT_CONTEXT_VERSION

    def test_generated_at_iso(self):
        r = build_event_context(reference_time=_REF)
        dt.datetime.fromisoformat(r["generated_at"])

    def test_status_enum(self):
        r = build_event_context(reference_time=_REF)
        assert r["status"] in ("ok", "partial", "no_data")

    def test_event_risk_state_enum(self):
        r = build_event_context(macro_events=[], reference_time=_REF)
        assert r["event_risk_state"] in ("quiet", "elevated", "crowded", "unknown")

    def test_event_item_schema(self):
        r = build_event_context(
            macro_events=[_macro("FOMC", 36)],
            reference_time=_REF,
        )
        item = r["upcoming_macro_events"][0]
        assert "event_type" in item
        assert "event_name" in item
        assert "event_category" in item
        assert "event_time" in item
        assert "time_to_event" in item
        assert "importance" in item
        assert "scope" in item
        assert "related_symbols" in item
        assert "risk_window" in item
        assert "event_source" in item
        assert "is_elapsed" in item
        assert "notes" in item

    def test_candidate_overlap_shape(self):
        r = build_event_context(reference_time=_REF)
        co = r["candidate_event_overlap"]
        assert "candidate_symbol" in co
        assert "overlapping_events" in co
        assert "overlap_count" in co

    def test_portfolio_overlap_shape(self):
        r = build_event_context(reference_time=_REF)
        po = r["portfolio_event_overlap"]
        assert "positions_with_overlap" in po
        assert "symbols_with_overlap" in po
        assert "overlapping_events" in po
        assert "event_cluster_count" in po

    def test_event_windows_shape(self):
        r = build_event_context(reference_time=_REF)
        w = r["event_windows"]
        assert "within_24h" in w
        assert "within_3d" in w
        assert "within_7d" in w
        assert "beyond_7d" in w


# ═══════════════════════════════════════════════════════════════════
#  2. MACRO EVENT CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════

class TestMacroClassification:
    """Macro events classify correctly by importance and category."""

    @pytest.mark.parametrize("name,expected", [
        ("CPI Release", "high"),
        ("FOMC Decision", "high"),
        ("NFP", "high"),
        ("GDP", "high"),
        ("PCE", "high"),
        ("Core CPI", "high"),
    ])
    def test_high_importance(self, name, expected):
        assert _classify_importance(name, "macro") == expected

    @pytest.mark.parametrize("name,expected", [
        ("PPI", "medium"),
        ("ISM Manufacturing", "medium"),
        ("Retail Sales", "medium"),
        ("Initial Claims", "medium"),
        ("Fed Speak", "medium"),
    ])
    def test_medium_importance(self, name, expected):
        assert _classify_importance(name, "macro") == expected

    @pytest.mark.parametrize("name,expected", [
        ("Existing Home Sales", "low"),
        ("Trade Balance", "low"),
        ("Factory Orders", "low"),
    ])
    def test_low_importance(self, name, expected):
        assert _classify_importance(name, "macro") == expected

    def test_unknown_importance(self):
        assert _classify_importance("Unknown Survey XYZ", "macro") == "unknown"

    @pytest.mark.parametrize("name,expected", [
        ("CPI", "inflation"),
        ("PPI", "inflation"),
        ("PCE", "inflation"),
        ("FOMC", "monetary_policy"),
        ("Fed Speak", "monetary_policy"),
        ("NFP", "employment"),
        ("GDP", "growth"),
        ("Retail Sales", "growth"),
    ])
    def test_category_classification(self, name, expected):
        assert _classify_category(name, "macro") == expected

    def test_normalise_event_key(self):
        assert _normalise_event_key("CPI Release") == "cpi"
        assert _normalise_event_key("FOMC Decision") == "fomc_decision"
        assert _normalise_event_key("Non Farm Payrolls Report") == "non_farm_payrolls"
        assert _normalise_event_key("GDP") == "gdp"

    def test_macro_event_full_item(self):
        r = build_event_context(
            macro_events=[_macro("FOMC", 36)],
            reference_time=_REF,
        )
        item = r["upcoming_macro_events"][0]
        assert item["event_type"] == "macro"
        assert item["event_name"] == "FOMC"
        assert item["event_category"] == "monetary_policy"
        assert item["importance"] == "high"
        assert item["scope"] == "market_wide"
        assert item["time_to_event"]["hours"] == 36.0
        assert item["risk_window"] == "within_3d"

    def test_explicit_importance_overrides(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20, importance="low")],
            reference_time=_REF,
        )
        assert r["upcoming_macro_events"][0]["importance"] == "low"


# ═══════════════════════════════════════════════════════════════════
#  3. COMPANY / EARNINGS EVENTS
# ═══════════════════════════════════════════════════════════════════

class TestCompanyEvents:
    """Company/earnings event handling."""

    def test_earnings_classification(self):
        r = build_event_context(
            company_events=[_earnings("AAPL", 48)],
            reference_time=_REF,
        )
        item = r["upcoming_company_events"][0]
        assert item["event_type"] == "earnings"
        assert item["event_category"] == "earnings"
        assert item["importance"] == "medium"
        assert item["scope"] == "single_stock"
        assert "AAPL" in item["related_symbols"]

    def test_missing_company_coverage(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 48)],
            reference_time=_REF,
        )
        assert r["metadata"]["company_event_coverage"] == "none"
        assert "company_events_not_provided" in r["warning_flags"]

    def test_empty_company_list(self):
        r = build_event_context(
            company_events=[],
            reference_time=_REF,
        )
        assert r["metadata"]["company_event_coverage"] == "empty"

    def test_earnings_symbol_match(self):
        r = build_event_context(
            company_events=[_earnings("AAPL", 48)],
            candidate=_candidate("AAPL"),
            reference_time=_REF,
        )
        assert r["candidate_event_overlap"]["overlap_count"] == 1


# ═══════════════════════════════════════════════════════════════════
#  4. TIME-TO-EVENT AND RISK WINDOWS
# ═══════════════════════════════════════════════════════════════════

class TestTimeAndWindows:
    """Time-to-event and risk window computation."""

    def test_within_24h(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 12)],
            reference_time=_REF,
        )
        assert len(r["event_windows"]["within_24h"]) == 1
        item = r["upcoming_macro_events"][0]
        assert item["risk_window"] == "within_24h"
        assert item["time_to_event"]["hours"] == 12.0

    def test_within_3d(self):
        r = build_event_context(
            macro_events=[_macro("PPI", 50)],
            reference_time=_REF,
        )
        assert len(r["event_windows"]["within_3d"]) == 1
        assert r["upcoming_macro_events"][0]["risk_window"] == "within_3d"

    def test_within_7d(self):
        r = build_event_context(
            macro_events=[_macro("GDP", 100)],
            reference_time=_REF,
        )
        assert len(r["event_windows"]["within_7d"]) == 1
        assert r["upcoming_macro_events"][0]["risk_window"] == "within_7d"

    def test_beyond_7d(self):
        r = build_event_context(
            macro_events=[_macro("ISM Manufacturing", 200)],
            reference_time=_REF,
        )
        assert len(r["event_windows"]["beyond_7d"]) == 1
        assert r["upcoming_macro_events"][0]["risk_window"] == "beyond_7d"

    def test_hours_to_window_helper(self):
        assert _hours_to_window(0) == "within_24h"
        assert _hours_to_window(23.9) == "within_24h"
        assert _hours_to_window(24.0) == "within_24h"
        assert _hours_to_window(24.1) == "within_3d"
        assert _hours_to_window(72.0) == "within_3d"
        assert _hours_to_window(72.1) == "within_7d"
        assert _hours_to_window(168.0) == "within_7d"
        assert _hours_to_window(168.1) == "beyond_7d"

    def test_no_timing_data(self):
        """Event with no time → no risk_window."""
        r = build_event_context(
            macro_events=[{"event_name": "Mystery Event"}],
            reference_time=_REF,
        )
        item = r["upcoming_macro_events"][0]
        assert item["time_to_event"] is None
        assert item["risk_window"] is None

    def test_date_only_parsing(self):
        """Date-only string parsed as midnight UTC."""
        r = build_event_context(
            macro_events=[{"event_name": "CPI", "event_date": "2026-03-11"}],
            reference_time=_REF,
        )
        item = r["upcoming_macro_events"][0]
        assert item["time_to_event"] is not None
        # March 11 00:00 UTC - March 10 14:00 UTC = 10 hours
        assert item["time_to_event"]["hours"] == 10.0

    def test_datetime_object_input(self):
        """datetime.datetime input is accepted."""
        event_dt = _REF + dt.timedelta(hours=6)
        r = build_event_context(
            macro_events=[{"event_name": "FOMC", "event_time": event_dt}],
            reference_time=_REF,
        )
        assert r["upcoming_macro_events"][0]["time_to_event"]["hours"] == 6.0

    def test_date_object_input(self):
        """datetime.date input is accepted."""
        event_d = dt.date(2026, 3, 12)
        r = build_event_context(
            macro_events=[{"event_name": "PPI", "event_time": event_d}],
            reference_time=_REF,
        )
        assert r["upcoming_macro_events"][0]["time_to_event"] is not None

    def test_unparseable_time_degrades(self):
        """Unparseable time string preserved but no timing computed."""
        r = build_event_context(
            macro_events=[{"event_name": "X", "event_time": "next Tuesday"}],
            reference_time=_REF,
        )
        item = r["upcoming_macro_events"][0]
        assert item["event_time"] == "next Tuesday"
        assert item["time_to_event"] is None
        assert item["risk_window"] is None


# ═══════════════════════════════════════════════════════════════════
#  5. CANDIDATE OVERLAP
# ═══════════════════════════════════════════════════════════════════

class TestCandidateOverlap:
    """Candidate-event overlap detection."""

    def test_macro_overlaps_spy(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            candidate=_candidate("SPY"),
            reference_time=_REF,
        )
        assert r["candidate_event_overlap"]["overlap_count"] == 1
        assert r["candidate_event_overlap"]["candidate_symbol"] == "SPY"
        assert "candidate_overlaps_event" in r["risk_flags"]

    def test_macro_no_overlap_stock(self):
        """Macro event doesn't overlap non-index single stock."""
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            candidate=_candidate("AAPL"),
            reference_time=_REF,
        )
        assert r["candidate_event_overlap"]["overlap_count"] == 0

    def test_earnings_overlaps_same_symbol(self):
        r = build_event_context(
            company_events=[_earnings("AAPL", 48)],
            candidate=_candidate("AAPL"),
            reference_time=_REF,
        )
        assert r["candidate_event_overlap"]["overlap_count"] == 1

    def test_earnings_no_overlap_different_symbol(self):
        r = build_event_context(
            company_events=[_earnings("AAPL", 48)],
            candidate=_candidate("MSFT"),
            reference_time=_REF,
        )
        assert r["candidate_event_overlap"]["overlap_count"] == 0

    def test_no_candidate_no_crash(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            reference_time=_REF,
        )
        assert r["candidate_event_overlap"]["overlap_count"] == 0
        assert r["candidate_event_overlap"]["candidate_symbol"] is None

    def test_multiple_overlaps_flagged(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20), _macro("FOMC", 40)],
            candidate=_candidate("SPY"),
            reference_time=_REF,
        )
        assert r["candidate_event_overlap"]["overlap_count"] == 2
        assert "candidate_overlaps_multiple_events" in r["risk_flags"]

    def test_event_beyond_dte_not_overlap(self):
        """Event at 720h (30d) but candidate dte=2 (48h) → no overlap."""
        r = build_event_context(
            macro_events=[_macro("GDP", 720)],
            candidate=_candidate("SPY", dte=2),
            reference_time=_REF,
        )
        assert r["candidate_event_overlap"]["overlap_count"] == 0


# ═══════════════════════════════════════════════════════════════════
#  6. PORTFOLIO OVERLAP
# ═══════════════════════════════════════════════════════════════════

class TestPortfolioOverlap:
    """Portfolio-wide event clustering."""

    def test_portfolio_macro_overlap(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            positions=[_position("SPY"), _position("QQQ")],
            reference_time=_REF,
        )
        assert r["portfolio_event_overlap"]["event_cluster_count"] >= 1
        assert "SPY" in r["portfolio_event_overlap"]["symbols_with_overlap"]
        assert "QQQ" in r["portfolio_event_overlap"]["symbols_with_overlap"]

    def test_portfolio_earnings_overlap(self):
        r = build_event_context(
            company_events=[_earnings("AAPL", 48)],
            positions=[_position("AAPL"), _position("MSFT")],
            reference_time=_REF,
        )
        assert r["portfolio_event_overlap"]["event_cluster_count"] == 1
        assert "AAPL" in r["portfolio_event_overlap"]["symbols_with_overlap"]
        assert "MSFT" not in r["portfolio_event_overlap"]["symbols_with_overlap"]

    def test_empty_portfolio(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            positions=[],
            reference_time=_REF,
        )
        assert r["portfolio_event_overlap"]["event_cluster_count"] == 0

    def test_no_portfolio_no_crash(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            reference_time=_REF,
        )
        assert r["portfolio_event_overlap"]["event_cluster_count"] == 0

    def test_portfolio_clustering_flag(self):
        """Multiple events cluster → flag."""
        r = build_event_context(
            macro_events=[_macro("CPI", 20), _macro("FOMC", 60)],
            positions=[_position("SPY")],
            reference_time=_REF,
        )
        assert "portfolio_event_clustering" in r["risk_flags"]

    def test_many_positions_near_event(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            positions=[_position("SPY"), _position("QQQ"), _position("IWM")],
            reference_time=_REF,
        )
        assert "portfolio_many_positions_near_event" in r["risk_flags"]

    def test_far_event_no_portfolio_overlap(self):
        """Event beyond 7d doesn't count for portfolio overlap."""
        r = build_event_context(
            macro_events=[_macro("GDP", 200)],
            positions=[_position("SPY")],
            reference_time=_REF,
        )
        assert r["portfolio_event_overlap"]["event_cluster_count"] == 0


# ═══════════════════════════════════════════════════════════════════
#  7. EVENT RISK STATE
# ═══════════════════════════════════════════════════════════════════

class TestEventRiskState:
    """Risk state derivation."""

    def test_quiet_empty_list(self):
        r = build_event_context(macro_events=[], reference_time=_REF)
        assert r["event_risk_state"] == "quiet"

    def test_quiet_none(self):
        """No data provided → unknown."""
        r = build_event_context(reference_time=_REF)
        assert r["event_risk_state"] == "unknown"

    def test_unknown_no_timing(self):
        """Events with no parseable time → unknown."""
        r = build_event_context(
            macro_events=[{"event_name": "Mystery"}],
            reference_time=_REF,
        )
        assert r["event_risk_state"] == "unknown"

    def test_elevated_high_within_3d(self):
        """One high-importance event within 3d → elevated."""
        r = build_event_context(
            macro_events=[_macro("CPI", 50)],
            reference_time=_REF,
        )
        assert r["event_risk_state"] == "elevated"

    def test_elevated_multiple_within_24h(self):
        """Two events within 24h → elevated."""
        r = build_event_context(
            macro_events=[
                _macro("Initial Claims", 10, importance="low"),
                _macro("Existing Home Sales", 20, importance="low"),
            ],
            reference_time=_REF,
        )
        assert r["event_risk_state"] == "elevated"

    def test_crowded_two_high_within_3d(self):
        """Two high-importance events within 3d → crowded."""
        r = build_event_context(
            macro_events=[_macro("CPI", 20), _macro("FOMC", 50)],
            reference_time=_REF,
        )
        assert r["event_risk_state"] == "crowded"

    def test_crowded_three_events_within_3d(self):
        """Three events within 3d → crowded."""
        r = build_event_context(
            macro_events=[
                _macro("Initial Claims", 20, importance="low"),
                _macro("PPI", 40),
                _macro("Existing Home Sales", 60, importance="low"),
            ],
            reference_time=_REF,
        )
        assert r["event_risk_state"] == "crowded"

    def test_quiet_only_beyond_7d(self):
        """Only distant events → quiet."""
        r = build_event_context(
            macro_events=[_macro("GDP", 200, importance="low")],
            reference_time=_REF,
        )
        assert r["event_risk_state"] == "quiet"

    def test_quiet_low_importance_within_7d(self):
        """One low event within 7d only → quiet."""
        r = build_event_context(
            macro_events=[_macro("Factory Orders", 120, importance="low")],
            reference_time=_REF,
        )
        assert r["event_risk_state"] == "quiet"


# ═══════════════════════════════════════════════════════════════════
#  8. PARTIAL DATA / MISSING COVERAGE
# ═══════════════════════════════════════════════════════════════════

class TestPartialData:
    """Partial/missing data is handled honestly."""

    def test_no_data_status(self):
        r = build_event_context(reference_time=_REF)
        assert r["status"] == "no_data"
        assert r["metadata"]["macro_coverage"] == "none"
        assert r["metadata"]["company_event_coverage"] == "none"

    def test_partial_macro_only(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 48)],
            reference_time=_REF,
        )
        assert r["status"] == "partial"
        assert r["metadata"]["macro_coverage"] == "available"
        assert r["metadata"]["company_event_coverage"] == "none"

    def test_partial_company_only(self):
        r = build_event_context(
            company_events=[_earnings("AAPL", 48)],
            reference_time=_REF,
        )
        assert r["status"] == "partial"
        assert r["metadata"]["company_event_coverage"] == "available"
        assert r["metadata"]["macro_coverage"] == "none"

    def test_ok_both(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 48)],
            company_events=[_earnings("AAPL", 48)],
            reference_time=_REF,
        )
        assert r["status"] == "ok"

    def test_ok_empty_both(self):
        """Empty lists → quiet, ok (caller is asserting no events)."""
        r = build_event_context(
            macro_events=[], company_events=[],
            reference_time=_REF,
        )
        assert r["status"] == "ok"
        assert r["event_risk_state"] == "quiet"

    def test_warning_flags_missing_inputs(self):
        r = build_event_context(reference_time=_REF)
        assert "macro_events_not_provided" in r["warning_flags"]
        assert "company_events_not_provided" in r["warning_flags"]
        assert "candidate_not_provided" in r["warning_flags"]
        assert "positions_not_provided" in r["warning_flags"]

    def test_mixed_timed_and_untimed(self):
        """Some events with timing, some without → still processes."""
        r = build_event_context(
            macro_events=[
                _macro("CPI", 20),
                {"event_name": "Unknown Event"},  # no time
            ],
            reference_time=_REF,
        )
        assert len(r["upcoming_macro_events"]) == 2
        timed = [e for e in r["upcoming_macro_events"] if e["time_to_event"] is not None]
        untimed = [e for e in r["upcoming_macro_events"] if e["time_to_event"] is None]
        assert len(timed) == 1
        assert len(untimed) == 1

    def test_missing_event_name(self):
        """Event dict with no name → defaults to unknown_event."""
        r = build_event_context(
            macro_events=[{"event_time": (_REF + dt.timedelta(hours=10)).isoformat()}],
            reference_time=_REF,
        )
        assert r["upcoming_macro_events"][0]["event_name"] == "unknown_event"


# ═══════════════════════════════════════════════════════════════════
#  9. RISK FLAGS
# ═══════════════════════════════════════════════════════════════════

class TestRiskFlags:
    """Risk flag generation."""

    def test_high_within_24h(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 12)],
            reference_time=_REF,
        )
        assert "high_importance_event_within_24h" in r["risk_flags"]

    def test_high_within_3d(self):
        r = build_event_context(
            macro_events=[_macro("FOMC", 50)],
            reference_time=_REF,
        )
        assert "high_importance_event_within_3d" in r["risk_flags"]

    def test_multiple_within_24h(self):
        r = build_event_context(
            macro_events=[
                _macro("Initial Claims", 10, importance="low"),
                _macro("Existing Home Sales", 20, importance="low"),
            ],
            reference_time=_REF,
        )
        assert "multiple_events_within_24h" in r["risk_flags"]

    def test_no_flags_when_quiet(self):
        r = build_event_context(
            macro_events=[_macro("GDP", 200, importance="low")],
            reference_time=_REF,
        )
        assert r["risk_flags"] == []


# ═══════════════════════════════════════════════════════════════════
# 10. EVIDENCE AND METADATA
# ═══════════════════════════════════════════════════════════════════

class TestEvidenceMetadata:
    """Evidence and metadata blocks."""

    def test_evidence_counts(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 12), _macro("FOMC", 50)],
            company_events=[_earnings("AAPL", 48)],
            candidate=_candidate("SPY"),
            reference_time=_REF,
        )
        ev = r["evidence"]
        assert ev["macro_event_count"] == 2
        assert ev["company_event_count"] == 1
        assert ev["high_importance_count"] == 2  # CPI + FOMC
        assert ev["within_24h_count"] == 1  # CPI
        assert ev["within_3d_count"] == 2  # FOMC + AAPL earnings

    def test_metadata_coverage(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 48)],
            candidate=_candidate("SPY"),
            reference_time=_REF,
        )
        meta = r["metadata"]
        assert meta["macro_coverage"] == "available"
        assert meta["company_event_coverage"] == "none"
        assert meta["candidate_provided"] is True
        assert meta["positions_provided"] is False
        assert meta["total_events_processed"] == 1


# ═══════════════════════════════════════════════════════════════════
# 11. SUMMARY
# ═══════════════════════════════════════════════════════════════════

class TestSummary:
    """Summary text generation."""

    def test_no_data_summary(self):
        r = build_event_context(reference_time=_REF)
        assert "unavailable" in r["summary"].lower() or "no event data" in r["summary"].lower()

    def test_quiet_summary(self):
        r = build_event_context(macro_events=[], reference_time=_REF)
        assert "quiet" in r["summary"].lower() or "no upcoming" in r["summary"].lower()

    def test_elevated_summary(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            reference_time=_REF,
        )
        assert len(r["summary"]) > 10

    def test_overlap_in_summary(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            candidate=_candidate("SPY"),
            reference_time=_REF,
        )
        assert "overlap" in r["summary"].lower() or "SPY" in r["summary"]


# ═══════════════════════════════════════════════════════════════════
# 12. INTEGRATION SCENARIOS
# ═══════════════════════════════════════════════════════════════════

class TestIntegration:
    """End-to-end integration scenarios."""

    def test_macro_heavy_elevated(self):
        """FOMC + CPI in 3 days → elevated/crowded with full evidence."""
        r = build_event_context(
            macro_events=[
                _macro("FOMC Decision", 18),
                _macro("CPI Release", 50),
                _macro("Initial Claims", 60),
            ],
            company_events=[_earnings("AAPL", 72)],
            candidate=_candidate("SPY", dte=7),
            positions=[_position("SPY"), _position("QQQ"), _position("AAPL")],
            reference_time=_REF,
        )
        assert r["status"] == "ok"
        assert r["event_risk_state"] in ("elevated", "crowded")
        assert r["candidate_event_overlap"]["overlap_count"] >= 1
        assert r["portfolio_event_overlap"]["event_cluster_count"] >= 1
        assert len(r["risk_flags"]) >= 1
        assert r["evidence"]["high_importance_count"] >= 2

    def test_quiet_partial(self):
        """Distant low-importance events, no company events → quiet partial."""
        r = build_event_context(
            macro_events=[
                _macro("Factory Orders", 200, importance="low"),
                _macro("Trade Balance", 250, importance="low"),
            ],
            reference_time=_REF,
        )
        assert r["status"] == "partial"
        assert r["event_risk_state"] == "quiet"
        assert r["risk_flags"] == []
        assert "company_events_not_provided" in r["warning_flags"]

    def test_earnings_overlap_proof(self):
        """Candidate AAPL with AAPL earnings in 2 days → overlap detected."""
        r = build_event_context(
            company_events=[_earnings("AAPL", 48)],
            candidate=_candidate("AAPL", dte=30),
            positions=[_position("AAPL"), _position("MSFT"), _position("GOOGL")],
            reference_time=_REF,
        )
        co = r["candidate_event_overlap"]
        assert co["candidate_symbol"] == "AAPL"
        assert co["overlap_count"] == 1
        po = r["portfolio_event_overlap"]
        assert "AAPL" in po["symbols_with_overlap"]
        assert po["event_cluster_count"] == 1

    def test_no_events_clean(self):
        """No events at all → quiet, clean output."""
        r = build_event_context(
            macro_events=[], company_events=[],
            candidate=_candidate("SPY"),
            positions=[_position("SPY")],
            reference_time=_REF,
        )
        assert r["status"] == "ok"
        assert r["event_risk_state"] == "quiet"
        assert r["risk_flags"] == []
        assert r["candidate_event_overlap"]["overlap_count"] == 0
        assert r["portfolio_event_overlap"]["event_cluster_count"] == 0

    def test_related_symbols_as_string(self):
        """related_symbols as a single string is handled."""
        r = build_event_context(
            company_events=[{
                "event_name": "TSLA Earnings",
                "event_type": "earnings",
                "related_symbols": "TSLA",
                "event_time": (_REF + dt.timedelta(hours=24)).isoformat(),
            }],
            candidate=_candidate("TSLA"),
            reference_time=_REF,
        )
        item = r["upcoming_company_events"][0]
        assert item["related_symbols"] == ["TSLA"]
        assert r["candidate_event_overlap"]["overlap_count"] == 1


# ═══════════════════════════════════════════════════════════════════
# 13. SOURCE SEMANTICS (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestSourceSemantics:
    """v1.1: event_source field and metadata.event_sources_used."""

    def test_event_source_caller_provided(self):
        """All events from build_event_context get event_source='caller_provided'."""
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            company_events=[_earnings("AAPL", 48)],
            reference_time=_REF,
        )
        for item in r["upcoming_macro_events"]:
            assert item["event_source"] == "caller_provided"
        for item in r["upcoming_company_events"]:
            assert item["event_source"] == "caller_provided"

    def test_metadata_event_sources_used(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            reference_time=_REF,
        )
        assert r["metadata"]["event_sources_used"] == ["caller_provided"]

    def test_metadata_event_sources_empty_when_no_events(self):
        r = build_event_context(macro_events=[], reference_time=_REF)
        assert r["metadata"]["event_sources_used"] == []

    def test_metadata_event_sources_no_data(self):
        r = build_event_context(reference_time=_REF)
        assert r["metadata"]["event_sources_used"] == []


# ═══════════════════════════════════════════════════════════════════
# 14. ELAPSED EVENT MARKING (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestElapsedEvents:
    """v1.1: is_elapsed field and past-event handling."""

    def test_future_event_not_elapsed(self):
        r = build_event_context(
            macro_events=[_macro("CPI", 20)],
            reference_time=_REF,
        )
        item = r["upcoming_macro_events"][0]
        assert item["is_elapsed"] is False

    def test_past_event_marked_elapsed(self):
        """Event in the past gets is_elapsed=True."""
        r = build_event_context(
            macro_events=[_macro("CPI", -5)],  # 5 hours ago
            reference_time=_REF,
        )
        item = r["upcoming_macro_events"][0]
        assert item["is_elapsed"] is True
        assert item["time_to_event"]["hours"] == -5.0

    def test_elapsed_event_no_risk_window(self):
        """Past events get risk_window=None."""
        r = build_event_context(
            macro_events=[_macro("FOMC", -10)],
            reference_time=_REF,
        )
        item = r["upcoming_macro_events"][0]
        assert item["risk_window"] is None

    def test_elapsed_event_excluded_from_windows(self):
        """Past events don't appear in event_windows buckets."""
        r = build_event_context(
            macro_events=[_macro("CPI", -5), _macro("PPI", 20)],
            reference_time=_REF,
        )
        all_windowed = []
        for bucket in r["event_windows"].values():
            all_windowed.extend(bucket)
        names = [e["event_name"] for e in all_windowed]
        assert "CPI" not in names
        assert "PPI" in names

    def test_elapsed_event_excluded_from_candidate_overlap(self):
        """Past events don't count as candidate overlap."""
        r = build_event_context(
            macro_events=[_macro("CPI", -5)],
            candidate=_candidate("SPY"),
            reference_time=_REF,
        )
        assert r["candidate_event_overlap"]["overlap_count"] == 0

    def test_elapsed_event_excluded_from_portfolio_overlap(self):
        """Past events don't count as portfolio overlap."""
        r = build_event_context(
            macro_events=[_macro("CPI", -5)],
            positions=[_position("SPY")],
            reference_time=_REF,
        )
        assert r["portfolio_event_overlap"]["event_cluster_count"] == 0

    def test_elapsed_event_still_in_items_list(self):
        """Past events still appear in upcoming_macro_events."""
        r = build_event_context(
            macro_events=[_macro("CPI", -5)],
            reference_time=_REF,
        )
        assert len(r["upcoming_macro_events"]) == 1
        assert r["upcoming_macro_events"][0]["event_name"] == "CPI"

    def test_evidence_elapsed_count(self):
        """Evidence and metadata track elapsed event count."""
        r = build_event_context(
            macro_events=[_macro("CPI", -5), _macro("FOMC", 20)],
            reference_time=_REF,
        )
        assert r["evidence"]["elapsed_event_count"] == 1
        assert r["metadata"]["elapsed_event_count"] == 1

    def test_all_elapsed_risk_state_quiet(self):
        """If all events are elapsed and caller provided data → quiet."""
        r = build_event_context(
            macro_events=[_macro("CPI", -5), _macro("FOMC", -48)],
            reference_time=_REF,
        )
        assert r["event_risk_state"] == "quiet"

    def test_no_timing_not_elapsed(self):
        """Event with no parseable time → is_elapsed=False."""
        r = build_event_context(
            macro_events=[{"event_name": "Mystery"}],
            reference_time=_REF,
        )
        assert r["upcoming_macro_events"][0]["is_elapsed"] is False


# ═══════════════════════════════════════════════════════════════════
# 15. TIMING HONESTY (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestTimingHonesty:
    """v1.1: timing_method transparency in time_to_event and metadata."""

    def test_timing_method_in_time_to_event(self):
        """time_to_event includes timing_method field."""
        r = build_event_context(
            macro_events=[_macro("CPI", 48)],
            reference_time=_REF,
        )
        tte = r["upcoming_macro_events"][0]["time_to_event"]
        assert tte["timing_method"] == "calendar_heuristic"

    def test_timing_method_in_metadata(self):
        """Metadata documents the timing method."""
        r = build_event_context(
            macro_events=[_macro("CPI", 48)],
            reference_time=_REF,
        )
        assert r["metadata"]["timing_method"] == "calendar_heuristic"

    def test_timing_note_in_metadata(self):
        """Metadata includes human-readable timing caveat."""
        r = build_event_context(
            macro_events=[_macro("CPI", 48)],
            reference_time=_REF,
        )
        note = r["metadata"]["timing_note"]
        assert "holiday" in note.lower() or "approximate" in note.lower()

    def test_trading_days_still_present(self):
        """trading_days field still computed alongside timing_method."""
        r = build_event_context(
            macro_events=[_macro("CPI", 48)],
            reference_time=_REF,
        )
        tte = r["upcoming_macro_events"][0]["time_to_event"]
        assert "trading_days" in tte
        assert tte["trading_days"] > 0

    def test_elapsed_trading_days_zero(self):
        """Past events have trading_days clamped to 0."""
        r = build_event_context(
            macro_events=[_macro("CPI", -24)],
            reference_time=_REF,
        )
        tte = r["upcoming_macro_events"][0]["time_to_event"]
        assert tte["trading_days"] == 0.0
        assert tte["hours"] == -24.0
        assert tte["timing_method"] == "calendar_heuristic"

    def test_no_timing_no_method(self):
        """Events without parseable time → time_to_event is None."""
        r = build_event_context(
            macro_events=[{"event_name": "Mystery"}],
            reference_time=_REF,
        )
        assert r["upcoming_macro_events"][0]["time_to_event"] is None
