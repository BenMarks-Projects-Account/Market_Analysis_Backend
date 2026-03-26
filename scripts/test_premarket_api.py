"""Test the pre-market API endpoints against the running backend."""
import httpx
import json

BASE = "http://127.0.0.1:5001"

print("=== TEST 1: /api/pre-market/briefing ===")
try:
    resp = httpx.get(f"{BASE}/api/pre-market/briefing", timeout=30)
    print(f"Status: {resp.status_code}")
    data = resp.json()

    # Snapshots
    snaps = data.get("snapshots", {})
    print(f"\nSnapshots: {len(snaps)} instruments")
    for k, v in snaps.items():
        if v:
            print(f"  {k}: last={v.get('last')}, prev_close={v.get('prev_close')}, source={v.get('source')}")
        else:
            print(f"  {k}: None (no data)")

    # Overnight signal
    sig = data.get("overnight_signal", {})
    print(f"\nOvernight signal: {sig.get('signal')}, conviction={sig.get('conviction')}, "
          f"direction_score={sig.get('direction_score')}")

    # Cross asset
    ca = data.get("cross_asset", {})
    print(f"Cross-asset: oil_pct={ca.get('oil_change_pct')}, dollar_pct={ca.get('dollar_change_pct')}, "
          f"bond_pct={ca.get('bond_change_pct')}")

    # VIX term structure
    vix = data.get("vix_term_structure", {})
    print(f"VIX: spot={vix.get('spot')}, vxx={vix.get('vxx_price')}, structure={vix.get('structure')}")

    # Gap analysis
    gaps = data.get("gap_analysis", {})
    print(f"\nGap analysis:")
    for k, v in gaps.items():
        print(f"  {k}: classification={v.get('classification')}, gap_pct={v.get('gap_pct')}")

except Exception as e:
    print(f"ERROR: {e}")

print("\n=== TEST 2: /api/pre-market/bars/es?timeframe=5min&days=1 ===")
try:
    resp = httpx.get(f"{BASE}/api/pre-market/bars/es", params={"timeframe": "5min", "days": 1}, timeout=30)
    print(f"Status: {resp.status_code}")
    bars = resp.json()
    print(f"Bars returned: {len(bars)}")
    if bars:
        print(f"  First: {bars[0]}")
        print(f"  Last:  {bars[-1]}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== TEST 3: /api/pre-market/health ===")
try:
    resp = httpx.get(f"{BASE}/api/pre-market/health", timeout=30)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== TEST 4: /api/pre-market/vix-term-structure ===")
try:
    resp = httpx.get(f"{BASE}/api/pre-market/vix-term-structure", timeout=30)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}")
except Exception as e:
    print(f"ERROR: {e}")

print("\nDone.")
