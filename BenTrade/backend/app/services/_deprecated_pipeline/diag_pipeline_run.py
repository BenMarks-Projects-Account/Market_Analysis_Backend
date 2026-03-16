"""Diagnostic: run the real pipeline end-to-end and report all stages."""
import json
import logging
import os
import sys
import traceback as tb_mod

# Suppress noisy log output — we only want our structured report
logging.disable(logging.CRITICAL)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

sys.path.insert(0, ".")
# Redirect stderr to devnull during pipeline execution to suppress
# httpx/provider noise that mixes with our report
_real_stderr = sys.stderr

from app.services.pipeline_orchestrator import run_pipeline
from app.services.pipeline_run_contract import PIPELINE_STAGES

print("=" * 60)
print("  LIVE PIPELINE DIAGNOSTIC RUN")
print("=" * 60)

try:
    sys.stderr = open(os.devnull, "w")
    result = run_pipeline(trigger_source="diag", requested_scope={"mode": "full"})
    sys.stderr = _real_stderr
except Exception as exc:
    sys.stderr = _real_stderr
    print(f"\n!!! Pipeline crashed: {type(exc).__name__}: {exc}")
    tb_mod.print_exc()
    sys.exit(1)

run = result["run"]
stage_results = {sr["stage_key"]: sr for sr in result.get("stage_results", []) if "stage_key" in sr}

print(f"\nPIPELINE STATUS: {run['status']}")
print(f"RUN ID: {run['run_id']}")
dur = run.get("duration_ms")
print(f"DURATION: {dur}ms" if dur else "DURATION: n/a")
print()

# Walk every stage
for idx, stage_key in enumerate(PIPELINE_STAGES):
    stage_state = run["stages"].get(stage_key, {})
    status = stage_state.get("status", "???")
    dur_ms = stage_state.get("duration_ms")
    icon = {"completed": "OK", "failed": "FAIL", "skipped": "SKIP", "pending": "PEND"}.get(status, "???")

    dur_str = f" ({dur_ms}ms)" if dur_ms else ""
    print(f"[{idx+1:2d}] {icon:4s}  {stage_key}{dur_str}")

    # Error detail
    err = stage_state.get("error")
    if err:
        print(f"       error: {err.get('code', '?')}: {str(err.get('message', ''))[:200]}")
        detail = err.get("detail", {})
        if isinstance(detail, dict):
            for dk, dv in detail.items():
                if dk == "traceback":
                    lines = dv if isinstance(dv, list) else [str(dv)]
                    for line in lines[-2:]:
                        print(f"         tb: {str(line).rstrip()}")
                else:
                    sv = str(dv)[:150]
                    print(f"         {dk}: {sv}")

    # Stage result metadata
    sr = stage_results.get(stage_key)
    if sr:
        sc = sr.get("summary_counts")
        if sc:
            print(f"       counts: {json.dumps(sc)}")
        meta = sr.get("metadata", {})

        # market_data: per-engine results
        if stage_key == "market_data":
            er = meta.get("engine_results", {})
            if isinstance(er, dict):
                for ek, erec in er.items():
                    es = erec.get("status", "?")
                    e_err = erec.get("error")
                    if e_err:
                        cat = (e_err.get("detail") or {}).get("failure_category", "")
                        print(f"         engine {ek}: {es} [{cat}] {str(e_err.get('message',''))[:100]}")
                    else:
                        score = (erec.get("summary") or {}).get("score")
                        print(f"         engine {ek}: {es} score={score}")

        # scanners: per-scanner results
        if stage_key == "scanners":
            sr_meta = meta.get("scanner_results", {})
            if isinstance(sr_meta, dict):
                for sk, srec in sr_meta.items():
                    rec = srec.get("record", srec) if isinstance(srec, dict) else {}
                    ss = rec.get("status", "?")
                    s_err = rec.get("error", {})
                    if s_err:
                        print(f"         scanner {sk}: {ss} — {str(s_err.get('message',''))[:100]}")
                    else:
                        cc = rec.get("candidate_count", "?")
                        print(f"         scanner {sk}: {ss} candidates={cc}")

        # market_model_analysis: per-engine analysis results
        if stage_key == "market_model_analysis":
            ar = meta.get("analysis_records", meta.get("analysis_results", {}))
            if isinstance(ar, dict):
                for ek, arec in ar.items():
                    rec = arec if isinstance(arec, dict) else {}
                    ast = rec.get("status", "?")
                    a_err = rec.get("error", {})
                    if a_err:
                        print(f"         analysis {ek}: {ast} — {str(a_err.get('message',''))[:200]}")
                    else:
                        du = rec.get("downstream_usable")
                        print(f"         analysis {ek}: {ast} usable={du}")

        # policy: show block reasons if available
        if stage_key == "policy":
            brc = meta.get("blocking_reason_counts", {})
            if brc:
                print(f"       block_reasons: {json.dumps(brc)}")
            crc = meta.get("caution_reason_counts", {})
            if crc:
                print(f"       caution_reasons: {json.dumps(crc)}")

        # Skip reason
        skip_reason = sr.get("skipped_reason")
        if skip_reason:
            print(f"       skipped: {skip_reason}")

print()

# Run-level errors
errs = run.get("errors", [])
if errs:
    print(f"RUN ERRORS ({len(errs)}):")
    for e in errs:
        print(f"  {e.get('code')}: {str(e.get('message', ''))[:200]}")
    print()

# Candidate counters
cc = run.get("candidate_counters", {})
print(f"CANDIDATE COUNTERS: {json.dumps(cc)}")

# Final
if run["status"] == "completed":
    print("\n*** PIPELINE COMPLETED SUCCESSFULLY ***")
else:
    print(f"\n*** PIPELINE DID NOT COMPLETE: {run['status']} ***")
    for stage_key in PIPELINE_STAGES:
        ss = run["stages"].get(stage_key, {})
        if ss.get("status") == "failed":
            print(f"    First failure: {stage_key}")
            break

# Other stages
print()
print("=== ALL STAGES ===")
for k, v in run["stages"].items():
    print(f"  {k}: {v.get('status')}")
