"""Diagnostic: run the real pipeline and report what happens."""
import json
import sys
sys.path.insert(0, ".")

from app.services.pipeline_orchestrator import run_pipeline

result = run_pipeline(trigger_source="diag", requested_scope={"mode": "full"})
run = result["run"]
print("=== PIPELINE STATUS:", run["status"])
print()

# Show market_data stage detail
md = run["stages"].get("market_data", {})
print("=== MARKET_DATA STAGE ===")
print("  status:", md.get("status"))
err = md.get("error")
if err:
    print("  error code:", err.get("code"))
    print("  error msg:", str(err.get("message", ""))[:300])
print()

# Show stage results for market_data
for sr in result.get("stage_results", []):
    if sr.get("stage_key") == "market_data":
        print("  outcome:", sr.get("outcome"))
        print("  summary_counts:", json.dumps(sr.get("summary_counts", {}), indent=2))
        meta = sr.get("metadata", {})
        print("  metadata keys:", list(meta.keys()))
        er = meta.get("engine_results", [])
        if er and isinstance(er, list):
            print()
            print("  === PER-ENGINE RESULTS ===")
            for e in er:
                ek = e.get("engine_key", "?")
                st = e.get("status", "?")
                e_err = e.get("error")
                if e_err:
                    print(f"    {ek}: {st}")
                    print(f"      code: {e_err.get('code')}")
                    msg = str(e_err.get("message", ""))
                    print(f"      msg: {msg[:300]}")
                    detail = e_err.get("detail", {})
                    tb = detail.get("traceback", [])
                    if tb:
                        # last 3 lines of traceback
                        for line in tb[-3:]:
                            print(f"      tb: {line.rstrip()}")
                else:
                    print(f"    {ek}: {st}")
        break

# Run-level errors
errs = run.get("errors", [])
if errs:
    print()
    print("=== RUN ERRORS ===")
    for e in errs:
        print(f"  {e.get('code')}: {str(e.get('message', ''))[:200]}")

# Other stages
print()
print("=== ALL STAGES ===")
for k, v in run["stages"].items():
    print(f"  {k}: {v.get('status')}")
