"""Quick V2 scaffolding smoke test."""
import sys
sys.path.insert(0, ".")

# 1. Contract imports
from app.services.scanner_v2 import (
    V2Candidate, V2Diagnostics, V2Leg, V2RecomputedMath,
    V2CheckResult, V2ScanResult, SCANNER_V2_CONTRACT_VERSION,
)
print(f"PASS contracts import (version={SCANNER_V2_CONTRACT_VERSION})")

# 2. Phase imports
from app.services.scanner_v2.phases import (
    phase_c_structural_validation,
    phase_d_quote_liquidity_sanity,
    phase_e_recomputed_math,
    phase_f_normalize,
)
print("PASS phases import")

# 3. Base scanner import
from app.services.scanner_v2.base_scanner import BaseV2Scanner
print("PASS base_scanner import")

# 4. Registry
from app.services.scanner_v2.registry import (
    list_v2_families, is_v2_supported, get_v2_family,
)
families = list_v2_families()
assert len(families) == 4, f"Expected 4 families, got {len(families)}"
assert is_v2_supported("put_credit_spread"), "Vertical spreads should be implemented"
print(f"PASS registry ({len(families)} families, vertical_spreads implemented)")

# 5. Migration
from app.services.scanner_v2.migration import (
    get_scanner_version, should_run_v2,
)
# All vertical spreads cut over to v2 (credit: Prompt 7, debit: Prompt 8)
assert get_scanner_version("put_credit_spread") == "v2"
assert should_run_v2("put_credit_spread")
assert get_scanner_version("call_credit_spread") == "v2"
assert should_run_v2("call_credit_spread")
assert get_scanner_version("put_debit") == "v2"
assert should_run_v2("put_debit")
assert get_scanner_version("call_debit") == "v2"
assert should_run_v2("call_debit")
# Non-vertical families remain at v1
assert get_scanner_version("iron_condor") == "v1"
assert not should_run_v2("iron_condor")
print("PASS migration (all verticals v2, others v1)")

# 6. Candidate construction + validation smoke
leg_short = V2Leg(index=0, side="short", strike=440, option_type="put",
                  expiration="2026-03-20", bid=1.20, ask=1.25, mid=1.225,
                  delta=-0.30, open_interest=5000, volume=200)
leg_long = V2Leg(index=1, side="long", strike=435, option_type="put",
                 expiration="2026-03-20", bid=0.80, ask=0.85, mid=0.825,
                 delta=-0.20, open_interest=3000, volume=100)
cand = V2Candidate(
    candidate_id="SPY|put_credit_spread|2026-03-20|440|435|0",
    scanner_key="put_credit_spread",
    strategy_id="put_credit_spread",
    family_key="vertical_spreads",
    symbol="SPY",
    underlying_price=450.0,
    expiration="2026-03-20",
    dte=9,
    legs=[leg_short, leg_long],
    math=V2RecomputedMath(
        net_credit=0.35,
        width=5.0,
    ),
)

# Run through phases
candidates = [cand]
candidates = phase_c_structural_validation(candidates)
assert not cand.diagnostics.reject_reasons, f"Unexpected rejects: {cand.diagnostics.reject_reasons}"
print("PASS phase C (structural validation)")

candidates = phase_d_quote_liquidity_sanity(candidates)
assert not cand.diagnostics.reject_reasons, f"Unexpected rejects: {cand.diagnostics.reject_reasons}"
print("PASS phase D (quote/liquidity)")

candidates = phase_e_recomputed_math(candidates)
assert not cand.diagnostics.reject_reasons, f"Unexpected rejects: {cand.diagnostics.reject_reasons}"
assert cand.math.max_profit is not None
assert cand.math.max_loss is not None
assert cand.math.pop is not None
assert cand.math.ev is not None
print(f"PASS phase E (math: credit={cand.math.net_credit}, profit={cand.math.max_profit}, loss={cand.math.max_loss}, pop={cand.math.pop}, ev={cand.math.ev})")

candidates = phase_f_normalize(candidates)
assert cand.passed
assert cand.downstream_usable
assert cand.generated_at
print("PASS phase F (normalization)")

# Serialize
d = cand.to_dict()
assert "_raw_construction" not in d
assert d["passed"] is True
print(f"PASS serialization ({len(d)} keys)")

# 7. Also test a reject case
bad_leg = V2Leg(index=0, side="short", strike=440, option_type="put",
                expiration="2026-03-20", bid=None, ask=None, mid=None)
bad_cand = V2Candidate(
    candidate_id="bad|1",
    scanner_key="put_credit_spread",
    strategy_id="put_credit_spread",
    family_key="vertical_spreads",
    symbol="SPY",
    legs=[bad_leg],
    math=V2RecomputedMath(width=5.0),
)
bad_list = phase_c_structural_validation([bad_cand])
# Only 1 leg → should fail valid_leg_count... actually it has 1 leg, that's not 0 legs
# but for vertical we'd need family checks. Let's just go through D:
bad_cand2 = V2Candidate(
    candidate_id="bad|2",
    scanner_key="put_credit_spread",
    strategy_id="put_credit_spread",
    family_key="vertical_spreads",
    symbol="SPY",
    expiration="2026-03-20",
    legs=[
        V2Leg(index=0, side="short", strike=440, option_type="put",
              expiration="2026-03-20", bid=None, ask=None, mid=None,
              open_interest=100, volume=50),
        V2Leg(index=1, side="long", strike=435, option_type="put",
              expiration="2026-03-20", bid=0.80, ask=0.85, mid=0.825,
              open_interest=100, volume=50),
    ],
    math=V2RecomputedMath(width=5.0, net_credit=0.3),
)
phase_c_structural_validation([bad_cand2])
phase_d_quote_liquidity_sanity([bad_cand2])
assert "v2_missing_quote" in bad_cand2.diagnostics.reject_reasons
print("PASS reject case (missing quote detected)")

print()
print("ALL V2 SCAFFOLDING SMOKE TESTS PASSED")
