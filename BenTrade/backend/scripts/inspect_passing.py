"""Inspect the one successful credit spread result to understand passing trade metrics."""
import json

f = "results/credit_spread_analysis_20260218_120000.json"
d = json.loads(open(f, encoding="utf-8").read())
ft = d.get("filter_trace") or {}
print("Filter trace:", json.dumps(ft, indent=2)[:2000] if ft else "None")
print()
for t in d.get("trades", []):
    print(f"  {t.get('symbol','?')} {t.get('short_strike')}/{t.get('long_strike')} w={t.get('width')} credit={t.get('net_credit')} pop={t.get('p_win_used') or t.get('pop_delta_approx')} ev/r={t.get('ev_to_risk')} ror={t.get('return_on_risk')} oi={t.get('open_interest')} vol={t.get('volume')} spread_pct={t.get('bid_ask_spread_pct')}")
