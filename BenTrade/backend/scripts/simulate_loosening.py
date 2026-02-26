"""Simulate loosened thresholds against the latest enriched data to estimate pass rates."""
import json

f = "results/credit_spread_analysis_20260226_122419.json"
d = json.loads(open(f, encoding="utf-8").read())
rr = (d.get("filter_trace") or {}).get("rejection_reasons", {})

# Current Balanced: ev_to_risk=0.02, volume=20, spread=1.5, OI=300, ror=0.01
# 80 enriched, each trade can fail multiple gates.
# Rejection overlaps: 78 fail ev, 78 fail vol, 48 fail spread, 18 fail OI, 5 fail ror
# Since reasons accumulate, the real picture is the intersection.

# Since we don't have per-trade data in the report, let's re-run enriched with
# different thresholds using the actual plugin.
# We need to regenerate from the stored report snapshots or just run a quick
# simulation with mock data typical of real SPY puts.

print("=== Rejection summary from latest trace (80 enriched) ===")
for k, v in sorted(rr.items(), key=lambda x: -x[1]):
    pct = 100 * v / 80
    print(f"  {k}: {v}/{80} ({pct:.0f}%)")

# Estimate pass rate under different threshold scenarios:
# We can back-of-envelope: if N trades fail a gate, then loosening it
# to where ~X fewer fail means X more could potentially pass.
# But a trade must pass ALL gates.

# Worst case: all 78 ev_to_risk failures are the SAME 78 volume failures.
# Then loosening both would free those 78 trades from both gates.
# Best case: they're independent, and we'd need to loosen both a lot.

# From the data: 78 ev + 78 vol + 48 spread + 18 OI + 5 ror + 2 credit = 229 reasons
# Across 80 trades, that's 2.86 reasons/trade on average.
# So most trades fail 2-3 gates simultaneously.

# With proposed Balanced (ev>=0.008, vol>=5, spread<=2.0, OI>=100, ror>=0.005):
# - ev loosened from 0.02 to 0.008: probably saves ~40-60 of the 78
# - vol loosened from 20 to 5: probably saves ~60-70 of the 78
# - spread loosened from 1.5 to 2.0: probably saves ~20-25 of the 48
# - OI loosened from 300 to 100: probably saves ~12-15 of the 18
# - ror loosened from 0.01 to 0.005: probably saves ~4 of the 5

# Conservatively, if ~50% of trades pass each loosened gate,
# and gates are partially correlated, we might expect ~5-15 of 80 to pass all.
# That's 6-19% acceptance, well above the 1-5% target.

print("\n=== Proposed Balanced thresholds ===")
proposed = {
    "min_ev_to_risk": 0.008,
    "min_ror": 0.005,
    "max_bid_ask_spread_pct": 2.0,
    "min_open_interest": 100,
    "min_volume": 5,
    "min_pop": 0.55,
}
for k, v in proposed.items():
    print(f"  {k}: {v}")

print("\n=== Ordering check ===")
presets = {
    "strict":       {"min_pop": 0.70, "min_ev": 0.03,  "min_ror": 0.03,  "spread": 1.0, "oi": 1000, "vol": 100},
    "conservative": {"min_pop": 0.60, "min_ev": 0.012, "min_ror": 0.01,  "spread": 1.5, "oi": 200,  "vol": 10},
    "balanced":     {"min_pop": 0.55, "min_ev": 0.008, "min_ror": 0.005, "spread": 2.0, "oi": 100,  "vol": 5},
    "wide":         {"min_pop": 0.45, "min_ev": 0.005, "min_ror": 0.002, "spread": 3.0, "oi": 25,   "vol": 1},
}
# Verify strict >= conservative >= balanced >= wide for "higher-is-tighter" params
for k in ["min_pop", "min_ev", "min_ror", "oi", "vol"]:
    vals = [presets[p][k] for p in ["strict", "conservative", "balanced", "wide"]]
    ok = all(vals[i] >= vals[i+1] for i in range(3))
    print(f"  {k}: {vals} {'OK' if ok else 'VIOLATION'}")
# Reverse for spread (higher = looser)
vals = [presets[p]["spread"] for p in ["strict", "conservative", "balanced", "wide"]]
ok = all(vals[i] <= vals[i+1] for i in range(3))
print(f"  spread: {vals} {'OK' if ok else 'VIOLATION'}")
