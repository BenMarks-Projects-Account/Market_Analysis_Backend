"""Quick script to analyze recent credit spread filter traces."""
import json
import glob
import os

files = sorted(glob.glob("results/credit_spread_analysis_*.json"), reverse=True)[:8]
for f in files:
    data = json.loads(open(f, encoding="utf-8").read())
    ft = data.get("filter_trace") or {}
    stages = ft.get("stages") or []
    gb = ft.get("gate_breakdown") or {}
    rr = ft.get("rejection_reasons") or {}
    mfc = ft.get("missing_field_counts") or {}
    preset = ft.get("preset_name", "?")
    dq_mode = ft.get("data_quality_mode", "?")
    trades = data.get("trades") or []

    print(f"=== {os.path.basename(f)} (preset={preset}, dq_mode={dq_mode}, accepted={len(trades)}) ===")
    for s in stages:
        label = s.get("label", s.get("name", "?"))
        inp = s.get("input_count", "?")
        out = s.get("output_count", "?")
        print(f"  {label}: {inp} -> {out}")
    if gb:
        sorted_gb = dict(sorted(gb.items(), key=lambda x: -x[1]))
        print(f"  Gate breakdown: {sorted_gb}")
    if rr:
        sorted_rr = dict(sorted(rr.items(), key=lambda x: -x[1])[:10])
        print(f"  Top rejections: {sorted_rr}")
    if mfc:
        print(f"  Missing fields: OI={mfc.get('open_interest', 0)} vol={mfc.get('volume', 0)} bid={mfc.get('bid', 0)} ask={mfc.get('ask', 0)} total={mfc.get('total_enriched', 0)}")
    thresholds = ft.get("resolved_thresholds", {})
    if thresholds:
        print(f"  Thresholds: {thresholds}")
    print()
