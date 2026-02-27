"""Deep analysis: what do rejected trades' actual metrics look like relative to thresholds?"""
import json

f = "results/credit_spread_analysis_20260226_122419.json"
d = json.loads(open(f, encoding="utf-8").read())
ft = d.get("filter_trace") or {}

# Get rejected examples if present
rex = ft.get("rejected_examples") or []
print(f"Rejected examples: {len(rex)}")
for ex in rex:
    print(f"  {ex.get('symbol')} {ex.get('short_strike')}/{ex.get('long_strike')} w={ex.get('width')} credit={ex.get('net_credit')} pop={ex.get('pop')} ev/r={ex.get('ev_to_risk')} ror={ex.get('ror')} oi={ex.get('open_interest')} vol={ex.get('volume')} reasons={ex.get('reasons')}")

# Also look at the raw report data for enriched/diagnostics info
diag = d.get("generation_diagnostics") or d.get("diagnostics") or {}
stats = d.get("report_stats") or {}
print(f"\nDiagnostics: {json.dumps(diag, indent=2)[:500]}")
print(f"\nStats: {json.dumps(stats, indent=2)[:500]}")

# Check thresholds
thresholds = ft.get("resolved_thresholds", {})
print(f"\nThresholds: {json.dumps(thresholds, indent=2)}")

# Rejection reasons with counts
rr = ft.get("rejection_reasons", {})
print(f"\nRejection reasons (sorted):")
for k, v in sorted(rr.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

# Gate breakdown
gb = ft.get("gate_breakdown", {})
print(f"\nGate breakdown (sorted):")
for k, v in sorted(gb.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

# Stage analysis
stages = ft.get("stages", [])
print(f"\nStages:")
for s in stages:
    inp = s.get("input_count", 0)
    out = s.get("output_count", 0)
    drop = inp - out if isinstance(inp, int) and isinstance(out, int) else "?"
    pct = f" ({100*out/inp:.1f}% pass)" if isinstance(inp, int) and inp > 0 else ""
    print(f"  {s.get('label', '?')}: {inp} -> {out} (dropped {drop}){pct}")
