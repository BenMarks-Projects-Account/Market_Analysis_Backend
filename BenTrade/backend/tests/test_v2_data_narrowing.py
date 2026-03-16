"""Tests for the V2 data-narrowing framework.

Covers:
- Chain normalization (Tradier format → V2OptionContract)
- Expiry narrowing (DTE windows, multi-expiry)
- Strike narrowing (distance, moneyness, option-type, dedup)
- Full pipeline orchestrator (narrow_chain)
- Data-quality diagnostics
- Edge cases (empty chain, invalid data, etc.)
"""

import sys
from datetime import date

import pytest

sys.path.insert(0, ".")

from app.services.scanner_v2.data.chain import (
    extract_options_list,
    normalize_chain,
    normalize_contract,
)
from app.services.scanner_v2.data.contracts import (
    V2ExpiryBucket,
    V2NarrowedUniverse,
    V2NarrowingDiagnostics,
    V2NarrowingRequest,
    V2OptionContract,
    V2StrikeEntry,
    V2UnderlyingSnapshot,
)
from app.services.scanner_v2.data.expiry import (
    narrow_expirations,
    narrow_expirations_multi,
)
from app.services.scanner_v2.data.narrow import narrow_chain
from app.services.scanner_v2.data.strikes import narrow_strikes


# ═══════════════════════════════════════════════════════════════════
#  Fixtures / helpers
# ═══════════════════════════════════════════════════════════════════

def _raw_contract(
    symbol: str = "SPY260320P00590000",
    root_symbol: str = "SPY",
    option_type: str = "put",
    expiration_date: str = "2026-03-20",
    strike: float = 590.0,
    bid: float = 2.50,
    ask: float = 2.70,
    delta: float = -0.30,
    iv: float = 0.22,
    open_interest: int = 5000,
    volume: int = 300,
    **overrides,
) -> dict:
    """Create a raw Tradier-format contract dict."""
    d = {
        "symbol": symbol,
        "root_symbol": root_symbol,
        "option_type": option_type,
        "expiration_date": expiration_date,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "greeks": {
            "delta": delta,
            "gamma": 0.05,
            "theta": -0.08,
            "vega": 0.15,
            "mid_iv": iv,
        },
        "open_interest": open_interest,
        "volume": volume,
    }
    d.update(overrides)
    return d


def _make_chain(contracts: list[dict]) -> dict:
    """Wrap contracts in Tradier chain format."""
    return {"options": {"option": contracts}}


def _build_contracts(
    strikes: list[float],
    expiration: str = "2026-03-20",
    option_type: str = "put",
    root: str = "SPY",
) -> list[V2OptionContract]:
    """Build a list of normalized V2OptionContracts for testing."""
    contracts = []
    for s in strikes:
        contracts.append(V2OptionContract(
            symbol=f"{root}|{option_type}|{expiration}|{s}",
            root_symbol=root,
            strike=s,
            option_type=option_type,
            expiration=expiration,
            bid=round(max(0.05, (600 - s) * 0.02), 2) if option_type == "put" else 0.50,
            ask=round(max(0.10, (600 - s) * 0.02 + 0.10), 2) if option_type == "put" else 0.60,
            mid=round(max(0.075, (600 - s) * 0.02 + 0.05), 4) if option_type == "put" else 0.55,
            delta=-0.30 if option_type == "put" else 0.30,
            iv=0.22,
            open_interest=1000,
            volume=100,
        ))
    return contracts


# ── Frozen "today" for deterministic DTE calculations ───────────
FIXED_TODAY = date(2026, 3, 11)


# ═══════════════════════════════════════════════════════════════════
#  1. Chain normalization tests
# ═══════════════════════════════════════════════════════════════════

class TestExtractOptionsList:
    """Test Tradier chain format extraction."""

    def test_standard_tradier_format(self):
        raw = [_raw_contract()]
        chain = _make_chain(raw)
        result = extract_options_list(chain)
        assert len(result) == 1

    def test_flat_list(self):
        raw = [_raw_contract()]
        result = extract_options_list(raw)
        assert len(result) == 1

    def test_single_contract_dict(self):
        chain = {"options": {"option": _raw_contract()}}
        result = extract_options_list(chain)
        assert len(result) == 1

    def test_options_as_list(self):
        raw = [_raw_contract()]
        chain = {"options": raw}
        result = extract_options_list(chain)
        assert len(result) == 1

    def test_empty_chain(self):
        assert extract_options_list({}) == []
        assert extract_options_list({"options": {}}) == []
        assert extract_options_list({"options": {"option": []}}) == []

    def test_non_dict_input(self):
        assert extract_options_list("bad") == []


class TestNormalizeContract:
    """Test single contract normalization."""

    def test_full_contract(self):
        raw = _raw_contract()
        c = normalize_contract(raw)
        assert c is not None
        assert c.symbol == "SPY260320P00590000"
        assert c.root_symbol == "SPY"
        assert c.strike == 590.0
        assert c.option_type == "put"
        assert c.expiration == "2026-03-20"
        assert c.bid == 2.50
        assert c.ask == 2.70
        assert c.mid == pytest.approx(2.60, abs=0.01)
        assert c.delta == -0.30
        assert c.iv == 0.22
        assert c.open_interest == 5000
        assert c.volume == 300
        assert c.quote_valid is True

    def test_missing_required_fields_returns_none(self):
        assert normalize_contract({}) is None
        assert normalize_contract({"symbol": "X"}) is None
        assert normalize_contract({"symbol": "X", "strike": 100}) is None

    def test_missing_bid_ask_flags_invalid(self):
        raw = _raw_contract(bid=None, ask=None)
        c = normalize_contract(raw)
        assert c is not None
        assert c.quote_valid is False
        assert c.mid is None

    def test_inverted_quote_flags_invalid(self):
        raw = _raw_contract(bid=3.00, ask=2.50)
        c = normalize_contract(raw)
        assert c is not None
        assert c.quote_valid is False

    def test_greeks_from_nested_dict(self):
        raw = _raw_contract()
        c = normalize_contract(raw)
        assert c.gamma == 0.05
        assert c.theta == -0.08
        assert c.vega == 0.15

    def test_greeks_from_top_level(self):
        raw = _raw_contract()
        del raw["greeks"]
        raw["delta"] = -0.25
        raw["iv"] = 0.18
        c = normalize_contract(raw)
        assert c.delta == -0.25
        assert c.iv == 0.18

    def test_option_type_normalized_to_lowercase(self):
        raw = _raw_contract(option_type="PUT")
        c = normalize_contract(raw)
        assert c.option_type == "put"


class TestNormalizeChain:
    """Test full chain normalization with diagnostics."""

    def test_basic_normalization(self):
        chain = _make_chain([
            _raw_contract(strike=590),
            _raw_contract(strike=585),
            _raw_contract(strike=580),
        ])
        diag = V2NarrowingDiagnostics()
        contracts = normalize_chain(chain, diag=diag)
        assert len(contracts) == 3
        assert diag.total_contracts_loaded == 3

    def test_quality_tallying(self):
        chain = _make_chain([
            _raw_contract(strike=590, bid=None, ask=None),
            _raw_contract(strike=585, delta=None),
        ])
        # Remove delta from second contract
        raw_list = extract_options_list(chain)
        del raw_list[1]["greeks"]["delta"]
        diag = V2NarrowingDiagnostics()
        contracts = normalize_chain(raw_list, diag=diag)
        assert len(contracts) == 2
        assert diag.contracts_missing_bid >= 1
        assert diag.contracts_missing_ask >= 1

    def test_unparseable_contracts_warning(self):
        chain = [_raw_contract(), {}]  # second is unparseable
        diag = V2NarrowingDiagnostics()
        contracts = normalize_chain(chain, diag=diag)
        assert len(contracts) == 1
        assert any("could not be parsed" in w for w in diag.warnings)


# ═══════════════════════════════════════════════════════════════════
#  2. Expiry narrowing tests
# ═══════════════════════════════════════════════════════════════════

class TestExpiryNarrowing:
    """Test expiration filtering by DTE window."""

    def _contracts_with_expirations(self, exps: list[str]) -> list[V2OptionContract]:
        result = []
        for exp in exps:
            result.append(V2OptionContract(
                symbol=f"SPY|put|{exp}|590",
                root_symbol="SPY",
                strike=590.0,
                option_type="put",
                expiration=exp,
                bid=1.0,
                ask=1.2,
                mid=1.1,
            ))
        return result

    def test_basic_dte_window(self):
        # Today = 2026-03-11
        # 2026-03-18 = 7 DTE (in window)
        # 2026-03-25 = 14 DTE (in window)
        # 2026-06-20 = 101 DTE (out)
        # 2026-03-12 = 1 DTE (in window, edge)
        contracts = self._contracts_with_expirations([
            "2026-03-18", "2026-03-25", "2026-06-20", "2026-03-12",
        ])
        req = V2NarrowingRequest(dte_min=7, dte_max=45)
        diag = V2NarrowingDiagnostics()
        kept = narrow_expirations(contracts, req, diag=diag, today=FIXED_TODAY)

        assert len(kept) == 2  # 7 DTE and 14 DTE
        assert diag.expirations_kept == 2
        assert diag.expirations_dropped == 2
        assert "dte_below_min" in diag.expiry_drop_reasons
        assert "dte_above_max" in diag.expiry_drop_reasons

    def test_all_expirations_dropped(self):
        contracts = self._contracts_with_expirations(["2026-03-12"])  # 1 DTE
        req = V2NarrowingRequest(dte_min=7, dte_max=45)
        kept = narrow_expirations(contracts, req, today=FIXED_TODAY)
        assert len(kept) == 0

    def test_invalid_expiration_date(self):
        contracts = [V2OptionContract(
            symbol="SPY|put|bad|590",
            root_symbol="SPY",
            strike=590.0,
            option_type="put",
            expiration="not-a-date",
        )]
        req = V2NarrowingRequest()
        diag = V2NarrowingDiagnostics()
        kept = narrow_expirations(contracts, req, diag=diag, today=FIXED_TODAY)
        assert len(kept) == 0
        assert diag.expiry_drop_reasons.get("dte_invalid", 0) == 1

    def test_diagnostics_lists_populated(self):
        contracts = self._contracts_with_expirations([
            "2026-03-18", "2026-06-20",
        ])
        req = V2NarrowingRequest(dte_min=5, dte_max=30)
        diag = V2NarrowingDiagnostics()
        narrow_expirations(contracts, req, diag=diag, today=FIXED_TODAY)
        assert "2026-03-18" in diag.expirations_kept_list
        assert "2026-06-20" in diag.expirations_dropped_list


class TestMultiExpiryNarrowing:
    """Test multi-expiry narrowing for calendars."""

    def _contracts_with_expirations(self, exps: list[str]) -> list[V2OptionContract]:
        result = []
        for exp in exps:
            result.append(V2OptionContract(
                symbol=f"SPY|put|{exp}|590",
                root_symbol="SPY",
                strike=590.0,
                option_type="put",
                expiration=exp,
                bid=1.0,
                ask=1.2,
                mid=1.1,
            ))
        return result

    def test_near_far_split(self):
        # Today = 2026-03-11
        # 2026-03-18 =  7 DTE → near
        # 2026-03-25 = 14 DTE → near
        # 2026-04-10 = 30 DTE → far
        # 2026-04-24 = 44 DTE → far
        contracts = self._contracts_with_expirations([
            "2026-03-18", "2026-03-25", "2026-04-10", "2026-04-24",
        ])
        req = V2NarrowingRequest(
            multi_expiry=True,
            near_dte_min=5,
            near_dte_max=20,
            far_dte_min=25,
            far_dte_max=50,
        )
        diag = V2NarrowingDiagnostics()
        near, far = narrow_expirations_multi(
            contracts, req, diag=diag, today=FIXED_TODAY,
        )
        assert len(near) == 2
        assert len(far) == 2
        assert diag.expirations_kept == 4

    def test_gap_between_windows_dropped(self):
        # 2026-03-18 = 7 DTE → near window
        # 2026-04-01 = 21 DTE → between
        # 2026-04-10 = 30 DTE → far window
        contracts = self._contracts_with_expirations([
            "2026-03-18", "2026-04-01", "2026-04-10",
        ])
        req = V2NarrowingRequest(
            multi_expiry=True,
            near_dte_min=5,
            near_dte_max=10,
            far_dte_min=25,
            far_dte_max=50,
        )
        diag = V2NarrowingDiagnostics()
        near, far = narrow_expirations_multi(
            contracts, req, diag=diag, today=FIXED_TODAY,
        )
        assert len(near) == 1  # only 7 DTE
        assert len(far) == 1   # only 30 DTE
        assert "dte_between_windows" in diag.expiry_drop_reasons

    def test_overlapping_windows_dual_role(self):
        """When near/far DTE windows overlap, contracts in the overlap
        must appear in BOTH near and far lists (dual-role)."""
        # 2026-03-18 =  7 DTE → near only
        # 2026-03-25 = 14 DTE → overlap (near AND far)
        # 2026-04-10 = 30 DTE → far only
        contracts = self._contracts_with_expirations([
            "2026-03-18", "2026-03-25", "2026-04-10",
        ])
        req = V2NarrowingRequest(
            multi_expiry=True,
            near_dte_min=5,
            near_dte_max=20,
            far_dte_min=10,
            far_dte_max=40,
        )
        diag = V2NarrowingDiagnostics()
        near, far = narrow_expirations_multi(
            contracts, req, diag=diag, today=FIXED_TODAY,
        )
        # 7 DTE → near only; 14 DTE → both; 30 DTE → far only
        assert len(near) == 2  # 7 DTE + 14 DTE
        assert len(far) == 2   # 14 DTE + 30 DTE
        # The 14 DTE contract is the dual-role one
        near_exps = [c.expiration for c in near]
        far_exps = [c.expiration for c in far]
        assert "2026-03-25" in near_exps
        assert "2026-03-25" in far_exps
        # Diagnostics should track dual-role count
        assert diag.expiry_drop_reasons.get("dual_role_contracts", 0) == 1


# ═══════════════════════════════════════════════════════════════════
#  3. Strike narrowing tests
# ═══════════════════════════════════════════════════════════════════

class TestStrikeNarrowing:
    """Test strike-window narrowing."""

    SPOT = 600.0

    def test_option_type_filter(self):
        puts = _build_contracts([590, 585, 580], option_type="put")
        calls = _build_contracts([610, 615, 620], option_type="call")
        all_contracts = puts + calls

        req = V2NarrowingRequest(option_types=["put"])
        diag = V2NarrowingDiagnostics()
        buckets = narrow_strikes(all_contracts, req, self.SPOT, diag=diag)
        total = sum(b.strike_count for b in buckets.values())
        assert total == 3  # only puts

    def test_moneyness_otm(self):
        # OTM puts have strike < spot
        contracts = _build_contracts(
            [590, 595, 600, 605, 610],
            option_type="put",
        )
        req = V2NarrowingRequest(moneyness="otm")
        buckets = narrow_strikes(contracts, req, self.SPOT)
        strikes = []
        for b in buckets.values():
            strikes.extend(b.get_strikes_list())
        # 590, 595 are OTM puts (strike < 600)
        assert 590.0 in strikes
        assert 595.0 in strikes
        assert 605.0 not in strikes
        assert 610.0 not in strikes

    def test_distance_window(self):
        # spot=600, 1-5% OTM → strikes 570-594
        contracts = _build_contracts(
            [560, 570, 580, 590, 594, 595, 596, 598],
            option_type="put",
        )
        req = V2NarrowingRequest(
            distance_min_pct=0.01,  # 1% = 6pt
            distance_max_pct=0.05,  # 5% = 30pt
        )
        buckets = narrow_strikes(contracts, req, self.SPOT)

        kept_strikes = set()
        for b in buckets.values():
            kept_strikes.update(b.get_strikes_list())

        # Distance calculations:
        # 560: |560-600|/600 = 0.0667 → >5% → dropped
        # 570: |570-600|/600 = 0.05   → =5% → kept
        # 580: |580-600|/600 = 0.0333 → in window → kept
        # 590: |590-600|/600 = 0.0167 → in window → kept
        # 594: |594-600|/600 = 0.01   → =1% → kept
        # 595: |595-600|/600 = 0.0083 → <1% → dropped
        # 596: |596-600|/600 = 0.0067 → <1% → dropped
        # 598: |598-600|/600 = 0.0033 → <1% → dropped
        assert 570.0 in kept_strikes
        assert 580.0 in kept_strikes
        assert 590.0 in kept_strikes
        assert 594.0 in kept_strikes
        assert 560.0 not in kept_strikes
        assert 595.0 not in kept_strikes
        assert 598.0 not in kept_strikes

    def test_deduplication_keeps_higher_oi(self):
        c1 = V2OptionContract(
            symbol="SPY|put|2026-03-20|590|A",
            root_symbol="SPY",
            strike=590.0,
            option_type="put",
            expiration="2026-03-20",
            bid=1.0, ask=1.2, mid=1.1,
            open_interest=500,
        )
        c2 = V2OptionContract(
            symbol="SPY|put|2026-03-20|590|B",
            root_symbol="SPY",
            strike=590.0,
            option_type="put",
            expiration="2026-03-20",
            bid=1.1, ask=1.3, mid=1.2,
            open_interest=2000,
        )
        req = V2NarrowingRequest()
        diag = V2NarrowingDiagnostics()
        buckets = narrow_strikes([c1, c2], req, self.SPOT, diag=diag)
        bucket = buckets["2026-03-20"]
        assert bucket.strike_count == 1
        assert bucket.strikes[0].contract.open_interest == 2000
        assert diag.duplicate_contracts_dropped == 1

    def test_expiry_bucket_structure(self):
        contracts = _build_contracts([590, 585, 580])
        req = V2NarrowingRequest()
        buckets = narrow_strikes(contracts, req, self.SPOT)
        assert "2026-03-20" in buckets
        bucket = buckets["2026-03-20"]
        assert bucket.expiration == "2026-03-20"
        assert bucket.strike_count == 3
        assert bucket.option_type == "put"
        # Strikes should be sorted
        strike_list = bucket.get_strikes_list()
        assert strike_list == sorted(strike_list)

    def test_empty_after_filtering(self):
        contracts = _build_contracts([590])
        req = V2NarrowingRequest(option_types=["call"])  # only calls
        buckets = narrow_strikes(contracts, req, self.SPOT)
        assert len(buckets) == 0

    def test_median_iv_computed(self):
        contracts = _build_contracts([590, 585, 580])
        for c in contracts:
            c.iv = 0.20
        contracts[1].iv = 0.30
        # IVs: [0.20, 0.30, 0.20] → median = 0.20
        req = V2NarrowingRequest()
        buckets = narrow_strikes(contracts, req, self.SPOT)
        bucket = buckets["2026-03-20"]
        assert bucket.median_iv == pytest.approx(0.20, abs=0.001)


# ═══════════════════════════════════════════════════════════════════
#  4. V2OptionContract tests
# ═══════════════════════════════════════════════════════════════════

class TestV2OptionContract:
    """Test V2OptionContract helper methods."""

    def test_distance_pct(self):
        c = V2OptionContract(
            symbol="X", root_symbol="SPY", strike=590.0,
            option_type="put", expiration="2026-03-20",
        )
        dist = c.distance_pct(600.0)
        assert dist == pytest.approx(10.0 / 600.0, abs=0.0001)

    def test_distance_pct_zero_underlying(self):
        c = V2OptionContract(
            symbol="X", root_symbol="SPY", strike=590.0,
            option_type="put", expiration="2026-03-20",
        )
        assert c.distance_pct(0.0) is None

    def test_is_otm_put(self):
        c = V2OptionContract(
            symbol="X", root_symbol="SPY", strike=590.0,
            option_type="put", expiration="2026-03-20",
        )
        assert c.is_otm(600.0) is True   # put below spot → OTM
        assert c.is_otm(580.0) is False   # put above spot → ITM

    def test_is_otm_call(self):
        c = V2OptionContract(
            symbol="X", root_symbol="SPY", strike=610.0,
            option_type="call", expiration="2026-03-20",
        )
        assert c.is_otm(600.0) is True   # call above spot → OTM
        assert c.is_otm(620.0) is False   # call below spot → ITM


# ═══════════════════════════════════════════════════════════════════
#  5. V2ExpiryBucket tests
# ═══════════════════════════════════════════════════════════════════

class TestV2ExpiryBucket:
    """Test bucket helper methods."""

    def _make_bucket(self) -> V2ExpiryBucket:
        entries = []
        for s in [580.0, 585.0, 590.0, 595.0]:
            entries.append(V2StrikeEntry(
                strike=s,
                contract=V2OptionContract(
                    symbol=f"SPY|put|2026-03-20|{s}",
                    root_symbol="SPY",
                    strike=s,
                    option_type="put",
                    expiration="2026-03-20",
                ),
            ))
        return V2ExpiryBucket(
            expiration="2026-03-20",
            dte=9,
            strikes=entries,
            strike_count=len(entries),
        )

    def test_get_strike_map(self):
        bucket = self._make_bucket()
        m = bucket.get_strike_map()
        assert 590.0 in m
        assert m[590.0].strike == 590.0

    def test_find_nearest_strike(self):
        bucket = self._make_bucket()
        entry = bucket.find_nearest_strike(587.0)
        assert entry is not None
        assert entry.strike == 585.0

    def test_find_nearest_with_exclude(self):
        bucket = self._make_bucket()
        entry = bucket.find_nearest_strike(587.0, exclude={585.0})
        assert entry is not None
        assert entry.strike == 590.0

    def test_find_nearest_empty(self):
        bucket = V2ExpiryBucket(expiration="2026-03-20", dte=9)
        assert bucket.find_nearest_strike(590.0) is None


# ═══════════════════════════════════════════════════════════════════
#  6. Full pipeline (narrow_chain) tests
# ═══════════════════════════════════════════════════════════════════

class TestNarrowChain:
    """Test the full narrowing orchestrator."""

    SPOT = 600.0

    def _make_test_chain(self) -> dict:
        """Create a chain with mixed expirations, strikes, and types."""
        contracts = []
        for exp in ["2026-03-18", "2026-03-25", "2026-06-20"]:
            for strike in [570, 580, 590, 595, 600, 605, 610, 620]:
                for opt_type in ["put", "call"]:
                    contracts.append(_raw_contract(
                        symbol=f"SPY|{opt_type}|{exp}|{strike}",
                        strike=strike,
                        expiration_date=exp,
                        option_type=opt_type,
                        bid=max(0.05, abs(600 - strike) * 0.03),
                        ask=max(0.10, abs(600 - strike) * 0.03 + 0.10),
                    ))
        return _make_chain(contracts)

    def test_basic_pipeline(self):
        chain = self._make_test_chain()
        universe = narrow_chain(
            chain=chain,
            symbol="SPY",
            underlying_price=self.SPOT,
            dte_min=5,
            dte_max=30,
            option_types=["put"],
            today=FIXED_TODAY,
        )
        assert isinstance(universe, V2NarrowedUniverse)
        assert not universe.is_empty
        assert universe.underlying.symbol == "SPY"
        assert universe.underlying.price == 600.0
        # Should have 2 expirations (03-18=7dte, 03-25=14dte; 06-20=101dte dropped)
        assert len(universe.expiry_buckets) == 2
        # All contracts should be puts
        for bucket in universe.expiry_buckets.values():
            assert bucket.option_type == "put"

    def test_diagnostics_populated(self):
        chain = self._make_test_chain()
        universe = narrow_chain(
            chain=chain,
            symbol="SPY",
            underlying_price=self.SPOT,
            dte_min=5,
            dte_max=30,
            today=FIXED_TODAY,
        )
        d = universe.diagnostics
        assert d.total_contracts_loaded > 0
        assert d.expirations_kept > 0
        assert d.total_expirations_loaded > 0
        assert d.contracts_final > 0

    def test_empty_chain(self):
        universe = narrow_chain(
            chain={},
            symbol="SPY",
            underlying_price=600.0,
        )
        assert universe.is_empty
        assert universe.total_strikes == 0
        assert universe.diagnostics.total_contracts_loaded == 0

    def test_distance_filtering(self):
        chain = self._make_test_chain()
        universe = narrow_chain(
            chain=chain,
            symbol="SPY",
            underlying_price=self.SPOT,
            dte_min=5,
            dte_max=30,
            distance_min_pct=0.01,
            distance_max_pct=0.05,
            option_types=["put"],
            today=FIXED_TODAY,
        )
        # Check kept strikes are within distance window
        for bucket in universe.expiry_buckets.values():
            for entry in bucket.strikes:
                dist = abs(entry.strike - self.SPOT) / self.SPOT
                assert 0.01 <= dist <= 0.05 + 0.001

    def test_moneyness_filter(self):
        chain = self._make_test_chain()
        universe = narrow_chain(
            chain=chain,
            symbol="SPY",
            underlying_price=self.SPOT,
            dte_min=5,
            dte_max=30,
            option_types=["put"],
            moneyness="otm",
            today=FIXED_TODAY,
        )
        for bucket in universe.expiry_buckets.values():
            for entry in bucket.strikes:
                assert entry.strike < self.SPOT  # OTM puts below spot

    def test_multi_expiry_mode(self):
        chain = self._make_test_chain()
        universe = narrow_chain(
            chain=chain,
            symbol="SPY",
            underlying_price=self.SPOT,
            multi_expiry=True,
            near_dte_min=5,
            near_dte_max=10,
            far_dte_min=12,
            far_dte_max=20,
            option_types=["put"],
            today=FIXED_TODAY,
        )
        # 2026-03-18 = 7 DTE → near
        # 2026-03-25 = 14 DTE → far
        assert not universe.is_empty
        # Should have both near and far expirations
        exps = sorted(universe.expiry_buckets.keys())
        assert len(exps) == 2

    def test_request_object_passed_through(self):
        req = V2NarrowingRequest(
            dte_min=10,
            dte_max=30,
            option_types=["call"],
        )
        chain = self._make_test_chain()
        universe = narrow_chain(
            chain=chain,
            symbol="SPY",
            underlying_price=self.SPOT,
            request=req,
            today=FIXED_TODAY,
        )
        assert universe.request.dte_min == 10
        assert universe.request.option_types == ["call"]

    def test_kwargs_override_request(self):
        req = V2NarrowingRequest(dte_min=10, dte_max=30)
        chain = self._make_test_chain()
        universe = narrow_chain(
            chain=chain,
            symbol="SPY",
            underlying_price=self.SPOT,
            request=req,
            dte_min=5,  # override
            today=FIXED_TODAY,
        )
        assert universe.request.dte_min == 5

    def test_get_single_expiry_bucket(self):
        chain = self._make_test_chain()
        universe = narrow_chain(
            chain=chain,
            symbol="SPY",
            underlying_price=self.SPOT,
            dte_min=7,
            dte_max=7,  # Only the exact 7-DTE expiration
            today=FIXED_TODAY,
        )
        bucket = universe.get_single_expiry_bucket()
        assert bucket is not None
        assert bucket.dte == 7

    def test_diagnostics_to_dict(self):
        chain = self._make_test_chain()
        universe = narrow_chain(
            chain=chain,
            symbol="SPY",
            underlying_price=self.SPOT,
            dte_min=5,
            dte_max=30,
            today=FIXED_TODAY,
        )
        d = universe.diagnostics.to_dict()
        assert isinstance(d, dict)
        assert "total_contracts_loaded" in d
        assert "expirations_kept" in d


# ═══════════════════════════════════════════════════════════════════
#  7. Underlying snapshot tests
# ═══════════════════════════════════════════════════════════════════

class TestV2UnderlyingSnapshot:
    """Test underlying snapshot data shape."""

    def test_basic(self):
        snap = V2UnderlyingSnapshot(symbol="SPY", price=600.0)
        assert snap.price_source == "provided"
        assert snap.is_stale is False
        assert snap.warnings == []


# ═══════════════════════════════════════════════════════════════════
#  8. Import tests (data module public API)
# ═══════════════════════════════════════════════════════════════════

class TestDataImports:
    """Verify the data module public API is importable."""

    def test_all_imports(self):
        from app.services.scanner_v2.data import (
            V2ExpiryBucket,
            V2NarrowedUniverse,
            V2NarrowingDiagnostics,
            V2NarrowingRequest,
            V2OptionContract,
            V2StrikeEntry,
            V2UnderlyingSnapshot,
            narrow_chain,
        )
        # All should be importable
        assert V2NarrowingRequest is not None
        assert narrow_chain is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
