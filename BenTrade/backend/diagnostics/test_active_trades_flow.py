"""
Active Trades Diagnostic — runs the active trade pipeline directly
and logs every step. Does NOT modify any application code.

Usage: cd BenTrade/backend && python diagnostics/test_active_trades_flow.py

Requires the backend to be running on localhost:5000.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Use HTTP calls only — no app imports (avoids requiring full server startup)
# ---------------------------------------------------------------------------
try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

BASE_URL = "http://127.0.0.1:5000"
DIAG_DIR = Path(__file__).resolve().parent.parent / "data" / "diagnostics"
DIAG_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = DIAG_DIR / "active_trades_diagnostic.json"

# Timing helper
_t0 = time.perf_counter()


def ts() -> str:
    """Return elapsed seconds since script start."""
    return f"[{time.perf_counter() - _t0:07.3f}s]"


def log(msg: str) -> None:
    print(f"{ts()} {msg}")


def safe_json(resp: httpx.Response) -> dict:
    """Parse JSON body, returning error dict on failure."""
    try:
        return resp.json()
    except Exception as exc:
        return {"_parse_error": str(exc), "_status": resp.status_code, "_text": resp.text[:500]}


# ══════════════════════════════════════════════════════════════════════
# DIAGNOSTICS
# ══════════════════════════════════════════════════════════════════════

async def run_diagnostic() -> dict:
    diag: dict = {
        "script": "test_active_trades_flow.py",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend_url": BASE_URL,
        "steps": [],
    }

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=200.0) as client:
        # ── 0. Health check ─────────────────────────────────────
        log("Step 0: Checking backend is reachable…")
        step0: dict = {"step": "health_check"}
        try:
            r = await client.get("/docs")
            step0["status"] = r.status_code
            step0["ok"] = r.status_code == 200
            log(f"  Backend reachable: HTTP {r.status_code}")
        except httpx.ConnectError as exc:
            step0["ok"] = False
            step0["error"] = f"Cannot connect to {BASE_URL}: {exc}"
            log(f"  ❌ CANNOT CONNECT — is the backend running? {exc}")
            diag["steps"].append(step0)
            diag["conclusion"] = "Backend unreachable — start the server first."
            return diag
        diag["steps"].append(step0)

        # ── 1. Get orchestrator status (baseline) ───────────────
        log("Step 1: Getting orchestrator status (baseline)…")
        step1: dict = {"step": "orchestrator_status_before"}
        t1 = time.perf_counter()
        try:
            r = await client.get("/api/orchestrator/status")
            step1["status_code"] = r.status_code
            step1["data"] = safe_json(r)
            step1["duration_ms"] = round((time.perf_counter() - t1) * 1000, 1)
            orch = step1["data"]
            log(f"  Orchestrator: running={orch.get('running')}, "
                f"stage={orch.get('current_stage')}, cycle={orch.get('cycle_count')}")
        except Exception as exc:
            step1["error"] = str(exc)
            log(f"  WARNING: orchestrator status failed — {exc}")
        diag["steps"].append(step1)

        # ── 2. Check for existing results (GET /results) ────────
        log("Step 2: Checking for existing pipeline results (GET /results)…")
        step2: dict = {"step": "existing_results"}
        t2 = time.perf_counter()
        try:
            r = await client.get("/api/active-trade-pipeline/results")
            step2["status_code"] = r.status_code
            data = safe_json(r)
            step2["data_keys"] = list(data.keys())
            step2["ok"] = data.get("ok")
            step2["run_id"] = data.get("run_id")
            step2["trade_count"] = data.get("trade_count")
            step2["recommendation_count"] = len(data.get("recommendations", []))
            step2["duration_ms"] = round((time.perf_counter() - t2) * 1000, 1)
            log(f"  Existing results: ok={data.get('ok')}, run_id={data.get('run_id')}, "
                f"recs={step2['recommendation_count']}")
            if data.get("ok") is False:
                err_msg = (data.get("error") or {}).get("message", "unknown")
                log(f"  ⚠️  Existing results have ok:false — {err_msg}")
        except Exception as exc:
            step2["error"] = str(exc)
            log(f"  WARNING: GET /results failed — {exc}")
        diag["steps"].append(step2)

        # ── 3. List existing runs ───────────────────────────────
        log("Step 3: Listing stored pipeline runs…")
        step3: dict = {"step": "list_runs"}
        try:
            r = await client.get("/api/active-trade-pipeline/runs")
            data = safe_json(r)
            runs = data.get("runs", [])
            step3["run_count"] = len(runs)
            step3["runs"] = runs[:5]  # keep top 5
            log(f"  Stored runs: {len(runs)}")
            for run in runs[:3]:
                log(f"    run_id={run.get('run_id')}, status={run.get('status')}, "
                    f"trades={run.get('trade_count')}, account={run.get('account_mode')}")
        except Exception as exc:
            step3["error"] = str(exc)
            log(f"  WARNING: /runs failed — {exc}")
        diag["steps"].append(step3)

        # ── 4. Path A: Direct pipeline run (paper mode) ─────────
        log("Step 4: PATH A — Direct pipeline run (POST /run?account_mode=paper)…")
        step4: dict = {"step": "direct_pipeline_run", "path": "A", "account_mode": "paper"}
        t4 = time.perf_counter()
        try:
            r = await client.post(
                "/api/active-trade-pipeline/run",
                params={"account_mode": "paper", "skip_model": "true"},
            )
            dur4 = time.perf_counter() - t4
            step4["status_code"] = r.status_code
            step4["duration_ms"] = round(dur4 * 1000, 1)
            data = safe_json(r)
            step4["ok"] = data.get("ok")
            step4["data_keys"] = list(data.keys())
            step4["run_id"] = data.get("run_id")
            step4["trade_count"] = data.get("trade_count")
            recs = data.get("recommendations", [])
            step4["recommendation_count"] = len(recs)
            step4["recommendation_counts"] = data.get("recommendation_counts")
            step4["stage_timings"] = data.get("stage_timings")
            step4["errors"] = data.get("errors", [])

            if data.get("ok"):
                log(f"  ✅ Pipeline returned ok:true in {dur4:.1f}s")
                log(f"     trades={step4['trade_count']}, recs={len(recs)}")
                log(f"     rec_counts={step4['recommendation_counts']}")
                if step4["stage_timings"]:
                    log(f"     stage_timings={json.dumps(step4['stage_timings'])}")
                if recs:
                    first = recs[0]
                    log(f"     First rec: symbol={first.get('symbol')}, "
                        f"strategy={first.get('strategy_type')}, "
                        f"action={first.get('recommendation')}, "
                        f"conviction={first.get('conviction')}")
                    step4["first_recommendation"] = {
                        "symbol": first.get("symbol"),
                        "strategy_type": first.get("strategy_type"),
                        "recommendation": first.get("recommendation"),
                        "conviction": first.get("conviction"),
                    }
            else:
                log(f"  ❌ Pipeline returned ok:false in {dur4:.1f}s")
                err = data.get("error", {})
                log(f"     error: {err}")
                step4["error_detail"] = err

            if dur4 < 0.5:
                log(f"  ⚠️  SUSPICIOUS — completed in {dur4*1000:.0f}ms (too fast; likely short-circuited)")
                step4["suspicious_fast"] = True
            elif dur4 > 120:
                log(f"  ⚠️  VERY SLOW — {dur4:.0f}s (near timeout territory)")
                step4["suspicious_slow"] = True

            if step4.get("errors"):
                log(f"  ⚠️  Errors in response: {step4['errors']}")

            # Store full response
            step4["full_response"] = data

        except httpx.ReadTimeout:
            step4["error"] = "TIMEOUT after 200s"
            step4["duration_ms"] = round((time.perf_counter() - t4) * 1000, 1)
            log(f"  ❌ TIMEOUT — pipeline did not respond within 200s")
        except Exception as exc:
            step4["error"] = str(exc)
            step4["duration_ms"] = round((time.perf_counter() - t4) * 1000, 1)
            log(f"  ❌ ERROR — {exc}")
        diag["steps"].append(step4)

        # ── 5. After run: check GET /results matches ────────────
        log("Step 5: Verifying GET /results returns the fresh data…")
        step5: dict = {"step": "verify_results_endpoint"}
        try:
            r = await client.get("/api/active-trade-pipeline/results")
            data = safe_json(r)
            step5["run_id"] = data.get("run_id")
            step5["matches_step4"] = data.get("run_id") == step4.get("run_id")
            step5["ok"] = data.get("ok")
            step5["recommendation_count"] = len(data.get("recommendations", []))
            log(f"  GET /results run_id={data.get('run_id')}, "
                f"matches_step4={step5['matches_step4']}, "
                f"recs={step5['recommendation_count']}")
            if not step5["matches_step4"]:
                log(f"  ⚠️  MISMATCH — step4 produced {step4.get('run_id')} but "
                    f"GET /results returned {data.get('run_id')}")
        except Exception as exc:
            step5["error"] = str(exc)
            log(f"  WARNING: verify failed — {exc}")
        diag["steps"].append(step5)

        # ── 6. Orchestrator status (after run) ──────────────────
        log("Step 6: Orchestrator status after pipeline run…")
        step6: dict = {"step": "orchestrator_status_after"}
        try:
            r = await client.get("/api/orchestrator/status")
            step6["data"] = safe_json(r)
            orch = step6["data"]
            log(f"  Orchestrator: running={orch.get('running')}, "
                f"stage={orch.get('current_stage')}, "
                f"last_cycle={orch.get('last_cycle_completed')}")
        except Exception as exc:
            step6["error"] = str(exc)
        diag["steps"].append(step6)

        # ── 7. Render guard analysis ────────────────────────────
        log("Step 7: Render guard timing analysis…")
        step7: dict = {"step": "render_guard_analysis"}
        pipeline_dur_ms = step4.get("duration_ms", 0)
        guard_ms = 30_000  # _MANUAL_RENDER_GUARD_MS from frontend
        step7["pipeline_duration_ms"] = pipeline_dur_ms
        step7["guard_window_ms"] = guard_ms
        step7["gap_ms"] = guard_ms - pipeline_dur_ms
        step7["protected"] = pipeline_dur_ms < guard_ms
        if pipeline_dur_ms < guard_ms:
            log(f"  ✅ Pipeline ({pipeline_dur_ms:.0f}ms) < guard ({guard_ms}ms) — "
                f"results protected for {guard_ms - pipeline_dur_ms:.0f}ms")
        else:
            log(f"  ⚠️  Pipeline ({pipeline_dur_ms:.0f}ms) > guard ({guard_ms}ms) — "
                f"orchestrator poll could overwrite results!")
        diag["steps"].append(step7)

        # ── 8. Simulate orchestrator poll window ────────────────
        log("Step 8: Simulating orchestrator poll (5s later)…")
        step8: dict = {"step": "simulated_orchestrator_poll"}
        await asyncio.sleep(2)  # Shorter than 5s for diagnostic speed
        try:
            r = await client.get("/api/active-trade-pipeline/results")
            data = safe_json(r)
            step8["run_id"] = data.get("run_id")
            step8["still_matches_step4"] = data.get("run_id") == step4.get("run_id")
            step8["ok"] = data.get("ok")
            step8["recommendation_count"] = len(data.get("recommendations", []))
            log(f"  After 2s: run_id={data.get('run_id')}, "
                f"still_matches={step8['still_matches_step4']}, "
                f"recs={step8['recommendation_count']}")
            if not step8["still_matches_step4"]:
                log(f"  ⚠️  RESULTS CHANGED — orchestrator overwrote the manual run!")
        except Exception as exc:
            step8["error"] = str(exc)
        diag["steps"].append(step8)

    # ── Summary ─────────────────────────────────────────────────
    log("")
    log("═══════════════════════════════════════════════════════════")
    log("SUMMARY")
    log("═══════════════════════════════════════════════════════════")

    pipeline_ok = step4.get("ok", False)
    pipeline_recs = step4.get("recommendation_count", 0)
    pipeline_ms = step4.get("duration_ms", 0)
    pipeline_suspicious = step4.get("suspicious_fast", False)

    if pipeline_ok and pipeline_recs > 0:
        diag["conclusion"] = (
            f"Pipeline works (ok:true, {pipeline_recs} recs in {pipeline_ms:.0f}ms). "
            "Problem is likely frontend rendering or timing."
        )
        log(f"  ✅ Pipeline works — {pipeline_recs} recommendations in {pipeline_ms:.0f}ms")
        log(f"     → Problem is likely frontend rendering or orchestrator timing")
    elif pipeline_ok and pipeline_recs == 0:
        diag["conclusion"] = (
            f"Pipeline returns ok:true but 0 recommendations ({pipeline_ms:.0f}ms). "
            "Either no positions or pipeline short-circuited."
        )
        log(f"  ⚠️  Pipeline ok:true but 0 recommendations — {pipeline_ms:.0f}ms")
        if pipeline_suspicious:
            log(f"     → SUSPICIOUS: too fast, likely short-circuited")
        else:
            log(f"     → May be no positions in paper account")
    else:
        diag["conclusion"] = (
            f"Pipeline FAILED (ok={step4.get('ok')}). "
            f"Error: {step4.get('error_detail', step4.get('error', 'unknown'))}"
        )
        log(f"  ❌ Pipeline FAILED — {step4.get('error_detail', step4.get('error'))}")

    diag["completed_at"] = datetime.now(timezone.utc).isoformat()
    diag["total_duration_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    return diag


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    log("Starting active trade pipeline diagnostic")
    log(f"Backend: {BASE_URL}")
    log(f"Output:  {OUTPUT_FILE}")
    log("")

    result = asyncio.run(run_diagnostic())

    # Write diagnostic JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    log(f"\nDiagnostic JSON saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
