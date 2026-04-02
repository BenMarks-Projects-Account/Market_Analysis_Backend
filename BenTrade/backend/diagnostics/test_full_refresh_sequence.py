"""
Full Refresh Sequence Diagnostic — calls the same API endpoints in
the same order as handleFullRefresh() in trade_management_center.js.

Usage: cd BenTrade/backend && python diagnostics/test_full_refresh_sequence.py

Requires the backend to be running on localhost:5000.

Mirrors the exact sequence from handleFullRefresh():
  1. POST /api/tmc/workflows/stock/run       (parallel)
  2. POST /api/tmc/workflows/options/run      (parallel)
  3. POST /api/active-trade-pipeline/run      (parallel)
  — wait for all 3 —
  4. GET /api/tmc/workflows/stock/latest
  5. GET /api/tmc/workflows/options/latest
  6. POST /api/tmc/workflows/portfolio-balance/run
  7. GET /api/orchestrator/status             (simulate poll)
  8. GET /api/active-trade-pipeline/results   (simulate poll)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

BASE_URL = "http://127.0.0.1:5000"
DIAG_DIR = Path(__file__).resolve().parent.parent / "data" / "diagnostics"
DIAG_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = DIAG_DIR / "full_refresh_diagnostic.json"

_t0 = time.perf_counter()


def ts() -> str:
    return f"[{time.perf_counter() - _t0:07.3f}s]"


def log(msg: str) -> None:
    print(f"{ts()} {msg}")


def safe_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception as exc:
        return {"_parse_error": str(exc), "_status": resp.status_code, "_text": resp.text[:500]}


def compact_summary(data: dict, kind: str) -> dict:
    """Build a compact summary of a response for logging."""
    summary: dict = {"ok": data.get("ok"), "status": data.get("status")}
    if kind == "stock_trigger":
        summary["run_id"] = data.get("run_id")
        summary["candidate_count"] = data.get("candidate_count")
    elif kind == "options_trigger":
        summary["run_id"] = data.get("run_id")
        summary["candidate_count"] = data.get("candidate_count")
    elif kind == "stock_latest":
        candidates = (data.get("data") or data).get("candidates", [])
        summary["candidate_count"] = len(candidates)
        summary["run_id"] = (data.get("data") or data).get("run_id")
    elif kind == "options_latest":
        candidates = (data.get("data") or data).get("candidates", [])
        summary["candidate_count"] = len(candidates)
        summary["run_id"] = (data.get("data") or data).get("run_id")
    elif kind == "active":
        summary["ok"] = data.get("ok")
        summary["trade_count"] = data.get("trade_count")
        summary["recommendation_count"] = len(data.get("recommendations", []))
        summary["run_id"] = data.get("run_id")
    elif kind == "balance":
        summary["ok"] = data.get("ok")
        summary["account_equity"] = data.get("account_equity")
        summary["has_plan"] = data.get("rebalance_plan") is not None
        summary["errors"] = data.get("errors", [])
    return summary


# ══════════════════════════════════════════════════════════════════════
# PARALLEL STAGE (mirrors handleFullRefresh Promise.allSettled)
# ══════════════════════════════════════════════════════════════════════

async def call_stock(client: httpx.AsyncClient) -> dict:
    """Stock: trigger → fetch latest (same as handleFullRefresh)."""
    result: dict = {"name": "stock", "stages": []}

    # Trigger
    t = time.perf_counter()
    try:
        r = await client.post(
            "/api/tmc/workflows/stock/run",
            json={},
            timeout=120.0,
        )
        dur = time.perf_counter() - t
        data = safe_json(r)
        result["stages"].append({
            "action": "POST /api/tmc/workflows/stock/run",
            "status_code": r.status_code,
            "duration_ms": round(dur * 1000, 1),
            "summary": compact_summary(data, "stock_trigger"),
        })
        log(f"  [STOCK] Trigger: HTTP {r.status_code} in {dur:.1f}s — "
            f"run_id={data.get('run_id')}, candidates={data.get('candidate_count')}")
    except Exception as exc:
        result["stages"].append({"action": "POST stock/run", "error": str(exc)})
        log(f"  [STOCK] Trigger FAILED: {exc}")
        result["error"] = str(exc)
        return result

    # Fetch latest
    t = time.perf_counter()
    try:
        r = await client.get(
            "/api/tmc/workflows/stock/latest",
            params={"_t": str(int(time.time() * 1000))},
            timeout=10.0,
        )
        dur = time.perf_counter() - t
        data = safe_json(r)
        summary = compact_summary(data, "stock_latest")
        result["stages"].append({
            "action": "GET /api/tmc/workflows/stock/latest",
            "status_code": r.status_code,
            "duration_ms": round(dur * 1000, 1),
            "summary": summary,
        })
        result["latest_data"] = data
        log(f"  [STOCK] Latest: HTTP {r.status_code} in {dur:.1f}s — "
            f"candidates={summary.get('candidate_count')}")
    except Exception as exc:
        result["stages"].append({"action": "GET stock/latest", "error": str(exc)})
        log(f"  [STOCK] Latest FAILED: {exc}")

    result["ok"] = True
    return result


async def call_options(client: httpx.AsyncClient) -> dict:
    """Options: trigger → fetch latest (same as handleFullRefresh)."""
    result: dict = {"name": "options", "stages": []}

    # Trigger
    t = time.perf_counter()
    try:
        r = await client.post(
            "/api/tmc/workflows/options/run",
            json={},
            timeout=180.0,
        )
        dur = time.perf_counter() - t
        data = safe_json(r)
        result["stages"].append({
            "action": "POST /api/tmc/workflows/options/run",
            "status_code": r.status_code,
            "duration_ms": round(dur * 1000, 1),
            "summary": compact_summary(data, "options_trigger"),
        })
        log(f"  [OPTIONS] Trigger: HTTP {r.status_code} in {dur:.1f}s — "
            f"run_id={data.get('run_id')}, candidates={data.get('candidate_count')}")
    except Exception as exc:
        result["stages"].append({"action": "POST options/run", "error": str(exc)})
        log(f"  [OPTIONS] Trigger FAILED: {exc}")
        result["error"] = str(exc)
        return result

    # Fetch latest
    t = time.perf_counter()
    try:
        r = await client.get(
            "/api/tmc/workflows/options/latest",
            params={"_t": str(int(time.time() * 1000))},
            timeout=10.0,
        )
        dur = time.perf_counter() - t
        data = safe_json(r)
        summary = compact_summary(data, "options_latest")
        result["stages"].append({
            "action": "GET /api/tmc/workflows/options/latest",
            "status_code": r.status_code,
            "duration_ms": round(dur * 1000, 1),
            "summary": summary,
        })
        result["latest_data"] = data
        log(f"  [OPTIONS] Latest: HTTP {r.status_code} in {dur:.1f}s — "
            f"candidates={summary.get('candidate_count')}")
    except Exception as exc:
        result["stages"].append({"action": "GET options/latest", "error": str(exc)})
        log(f"  [OPTIONS] Latest FAILED: {exc}")

    result["ok"] = True
    return result


async def call_active(client: httpx.AsyncClient, account_mode: str) -> dict:
    """Active trades pipeline (same as handleFullRefresh)."""
    result: dict = {"name": "active_trades", "stages": []}

    t = time.perf_counter()
    try:
        r = await client.post(
            "/api/active-trade-pipeline/run",
            params={"account_mode": account_mode, "skip_model": "true"},
            timeout=200.0,
        )
        dur = time.perf_counter() - t
        data = safe_json(r)
        summary = compact_summary(data, "active")
        result["stages"].append({
            "action": f"POST /api/active-trade-pipeline/run?account_mode={account_mode}&skip_model=true",
            "status_code": r.status_code,
            "duration_ms": round(dur * 1000, 1),
            "summary": summary,
        })
        result["pipeline_data"] = data
        log(f"  [ACTIVE] Pipeline: HTTP {r.status_code} in {dur:.1f}s — "
            f"ok={data.get('ok')}, trades={data.get('trade_count')}, "
            f"recs={len(data.get('recommendations', []))}")
        if data.get("ok") is False:
            err = (data.get("error") or {}).get("message", str(data.get("error")))
            log(f"  [ACTIVE] ⚠️  ok:false — {err}")
    except httpx.ReadTimeout:
        result["stages"].append({"action": "POST active-trade-pipeline/run", "error": "TIMEOUT 200s"})
        log(f"  [ACTIVE] ❌ TIMEOUT after 200s")
    except Exception as exc:
        result["stages"].append({"action": "POST active-trade-pipeline/run", "error": str(exc)})
        log(f"  [ACTIVE] ❌ FAILED: {exc}")

    result["ok"] = result.get("pipeline_data", {}).get("ok", False)
    return result


# ══════════════════════════════════════════════════════════════════════
# MAIN DIAGNOSTIC
# ══════════════════════════════════════════════════════════════════════

async def run_diagnostic() -> dict:
    diag: dict = {
        "script": "test_full_refresh_sequence.py",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend_url": BASE_URL,
        "steps": [],
        "sequence_description": (
            "Mirrors handleFullRefresh(): parallel(stock, options, active) "
            "→ allSettled → portfolio-balance → orchestrator poll"
        ),
    }

    account_mode = "paper"

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=200.0) as client:

        # ── 0. Health + Orchestrator baseline ───────────────────
        log("Step 0: Health check + orchestrator baseline…")
        step0: dict = {"step": "init"}
        try:
            r = await client.get("/docs")
            step0["backend_reachable"] = r.status_code == 200
            log(f"  Backend: HTTP {r.status_code}")
        except httpx.ConnectError as exc:
            step0["backend_reachable"] = False
            step0["error"] = str(exc)
            log(f"  ❌ CANNOT CONNECT — {exc}")
            diag["steps"].append(step0)
            diag["conclusion"] = "Backend unreachable."
            return diag

        try:
            r = await client.get("/api/orchestrator/status")
            step0["orchestrator_before"] = safe_json(r)
            o = step0["orchestrator_before"]
            log(f"  Orchestrator: running={o.get('running')}, "
                f"stage={o.get('current_stage')}, cycle={o.get('cycle_count')}")
        except Exception as exc:
            step0["orchestrator_error"] = str(exc)
            log(f"  Orchestrator status unavailable: {exc}")
        diag["steps"].append(step0)

        # ── 1. Parallel dispatch (stock, options, active) ───────
        log("")
        log("═══════════════════════════════════════════════════════════")
        log("PHASE 1: Parallel dispatch (stock + options + active)")
        log("═══════════════════════════════════════════════════════════")
        phase1: dict = {"step": "parallel_dispatch"}
        t1 = time.perf_counter()

        log("  Dispatching 3 parallel requests…")
        stock_task = asyncio.create_task(call_stock(client))
        options_task = asyncio.create_task(call_options(client))
        active_task = asyncio.create_task(call_active(client, account_mode))

        results = await asyncio.gather(
            stock_task, options_task, active_task,
            return_exceptions=True,
        )

        dur1 = time.perf_counter() - t1
        phase1["duration_ms"] = round(dur1 * 1000, 1)

        stock_result = results[0] if not isinstance(results[0], Exception) else {"name": "stock", "error": str(results[0])}
        options_result = results[1] if not isinstance(results[1], Exception) else {"name": "options", "error": str(results[1])}
        active_result = results[2] if not isinstance(results[2], Exception) else {"name": "active_trades", "error": str(results[2])}

        phase1["stock"] = {k: v for k, v in stock_result.items() if k != "latest_data"}
        phase1["options"] = {k: v for k, v in options_result.items() if k != "latest_data"}
        phase1["active"] = {k: v for k, v in active_result.items() if k != "pipeline_data"}

        log(f"\n  Phase 1 complete in {dur1:.1f}s")
        log(f"    Stock:   ok={stock_result.get('ok', False)}")
        log(f"    Options: ok={options_result.get('ok', False)}")
        log(f"    Active:  ok={active_result.get('ok', False)}")
        diag["steps"].append(phase1)

        # ── 2. Portfolio Balance (mirrors step 4 in handleFullRefresh) ─
        log("")
        log("═══════════════════════════════════════════════════════════")
        log("PHASE 2: Portfolio Balance (with pre-computed results)")
        log("═══════════════════════════════════════════════════════════")
        phase2: dict = {"step": "portfolio_balance"}
        t2 = time.perf_counter()

        active_data = active_result.get("pipeline_data", {})
        active_ok = active_result.get("ok", False) and active_data.get("ok") is not False

        # Build payload exactly as frontend does
        stock_latest = stock_result.get("latest_data")
        options_latest = options_result.get("latest_data")

        balance_payload = {
            "account_mode": account_mode,
            "skip_model": True,
            "active_trade_results": active_data if active_ok else {"recommendations": [], "ok": True, "_provided": True},
            "stock_results": (stock_latest.get("data") if stock_latest and isinstance(stock_latest, dict) else None) if stock_latest else None,
            "options_results": (options_latest.get("data") if options_latest and isinstance(options_latest, dict) else None) if options_latest else None,
        }

        log(f"  Payload: active_ok={active_ok}, "
            f"active_recs={len((balance_payload.get('active_trade_results') or {}).get('recommendations', []))}, "
            f"has_stock={balance_payload.get('stock_results') is not None}, "
            f"has_options={balance_payload.get('options_results') is not None}")

        try:
            r = await client.post(
                "/api/tmc/workflows/portfolio-balance/run",
                json=balance_payload,
                timeout=70.0,
            )
            dur2 = time.perf_counter() - t2
            data = safe_json(r)
            phase2["status_code"] = r.status_code
            phase2["duration_ms"] = round(dur2 * 1000, 1)
            phase2["ok"] = data.get("ok")
            phase2["account_equity"] = data.get("account_equity")
            phase2["regime_label"] = data.get("regime_label")
            phase2["has_plan"] = data.get("rebalance_plan") is not None
            phase2["errors"] = data.get("errors", [])
            phase2["stages"] = data.get("stages", {})
            phase2["active_trade_summary"] = data.get("active_trade_summary")

            log(f"  Response: HTTP {r.status_code} in {dur2:.1f}s")
            log(f"  ok={data.get('ok')}, equity={data.get('account_equity')}, "
                f"plan={phase2['has_plan']}")
            if data.get("errors"):
                log(f"  ⚠️  Errors: {data['errors']}")

            plan = data.get("rebalance_plan")
            if plan:
                for key in ["close_actions", "hold_positions", "open_actions", "skip_actions"]:
                    log(f"    {key}: {len(plan.get(key, []))}")

            phase2["full_response"] = data
        except Exception as exc:
            phase2["error"] = str(exc)
            phase2["duration_ms"] = round((time.perf_counter() - t2) * 1000, 1)
            log(f"  ❌ ERROR — {exc}")
        diag["steps"].append(phase2)

        # ── 3. Orchestrator status after (simulate poll) ────────
        log("")
        log("═══════════════════════════════════════════════════════════")
        log("PHASE 3: Post-refresh — orchestrator poll simulation")
        log("═══════════════════════════════════════════════════════════")
        phase3: dict = {"step": "orchestrator_poll_simulation"}

        try:
            r = await client.get("/api/orchestrator/status")
            phase3["orchestrator_after"] = safe_json(r)
            o = phase3["orchestrator_after"]
            log(f"  Orchestrator (after): running={o.get('running')}, "
                f"stage={o.get('current_stage')}, "
                f"last_cycle={o.get('last_cycle_completed')}")
        except Exception as exc:
            phase3["orchestrator_error"] = str(exc)

        # Simulate the poll that happens 5s later
        log("  Waiting 3s to simulate orchestrator poll interval…")
        await asyncio.sleep(3)

        try:
            r = await client.get("/api/active-trade-pipeline/results")
            data = safe_json(r)
            phase3["poll_results"] = {
                "run_id": data.get("run_id"),
                "ok": data.get("ok"),
                "recommendation_count": len(data.get("recommendations", [])),
                "matches_phase1_run": data.get("run_id") == active_data.get("run_id"),
            }
            log(f"  Poll GET /results: run_id={data.get('run_id')}, "
                f"matches={phase3['poll_results']['matches_phase1_run']}, "
                f"recs={phase3['poll_results']['recommendation_count']}")
            if not phase3["poll_results"]["matches_phase1_run"]:
                log("  ⚠️  RUN ID CHANGED — orchestrator may have overwritten results!")
        except Exception as exc:
            phase3["poll_error"] = str(exc)
            log(f"  Poll failed: {exc}")
        diag["steps"].append(phase3)

        # ── 4. Render guard timing analysis ─────────────────────
        log("")
        log("═══════════════════════════════════════════════════════════")
        log("TIMING ANALYSIS")
        log("═══════════════════════════════════════════════════════════")
        timing: dict = {"step": "timing_analysis"}

        total_parallel_ms = phase1.get("duration_ms", 0)
        balance_ms = phase2.get("duration_ms", 0)
        total_refresh_ms = round((time.perf_counter() - t1) * 1000, 1)
        guard_ms = 30_000

        timing["total_parallel_ms"] = total_parallel_ms
        timing["balance_ms"] = balance_ms
        timing["total_refresh_ms"] = total_refresh_ms
        timing["guard_window_ms"] = guard_ms
        timing["guard_remaining_ms"] = max(0, guard_ms - total_refresh_ms)
        timing["guard_covers_refresh"] = total_refresh_ms < guard_ms

        log(f"  Parallel phase:   {total_parallel_ms:.0f}ms")
        log(f"  Portfolio balance: {balance_ms:.0f}ms")
        log(f"  Total refresh:    {total_refresh_ms:.0f}ms")
        log(f"  Guard window:     {guard_ms}ms")
        log(f"  Guard remaining:  {timing['guard_remaining_ms']:.0f}ms")

        if timing["guard_covers_refresh"]:
            log(f"  ✅ Guard protects results ({timing['guard_remaining_ms']:.0f}ms margin)")
        else:
            log(f"  ⚠️  Refresh ({total_refresh_ms:.0f}ms) EXCEEDS guard ({guard_ms}ms) — "
                "orchestrator poll may overwrite")

        # Stock and options individual timings
        stock_stages = stock_result.get("stages", [])
        options_stages = options_result.get("stages", [])
        active_stages = active_result.get("stages", [])
        if stock_stages:
            log(f"\n  Stock breakdown:")
            for s in stock_stages:
                log(f"    {s.get('action')}: {s.get('duration_ms', '?')}ms — {json.dumps(s.get('summary', {}))}")
        if options_stages:
            log(f"  Options breakdown:")
            for s in options_stages:
                log(f"    {s.get('action')}: {s.get('duration_ms', '?')}ms — {json.dumps(s.get('summary', {}))}")
        if active_stages:
            log(f"  Active breakdown:")
            for s in active_stages:
                log(f"    {s.get('action')}: {s.get('duration_ms', '?')}ms — {json.dumps(s.get('summary', {}))}")

        diag["steps"].append(timing)

    # ── Summary ─────────────────────────────────────────────────
    log("")
    log("═══════════════════════════════════════════════════════════")
    log("SUMMARY")
    log("═══════════════════════════════════════════════════════════")

    issues = []
    if not stock_result.get("ok"):
        issues.append(f"Stock scan failed: {stock_result.get('error', 'unknown')}")
    if not options_result.get("ok"):
        issues.append(f"Options scan failed: {options_result.get('error', 'unknown')}")
    if not active_result.get("ok"):
        issues.append(f"Active trade pipeline failed: "
                       f"{active_result.get('error', active_data.get('error', 'unknown'))}")
    if not phase2.get("ok"):
        issues.append(f"Portfolio balance failed: {phase2.get('error', phase2.get('errors', 'unknown'))}")
    if phase2.get("account_equity") in (None, 0, 0.0):
        issues.append(f"Account equity is {phase2.get('account_equity')} — rebalance will be empty")
    if not phase2.get("has_plan"):
        issues.append("No rebalance plan produced")
    if not timing.get("guard_covers_refresh"):
        issues.append("Refresh exceeds guard window — orchestrator may overwrite")

    if issues:
        diag["conclusion"] = "ISSUES FOUND:\n" + "\n".join(f"  - {i}" for i in issues)
        for i in issues:
            log(f"  ⚠️  {i}")
    else:
        diag["conclusion"] = "Full Refresh sequence completed successfully — all endpoints working."
        log("  ✅ Full Refresh sequence completed successfully")

    diag["completed_at"] = datetime.now(timezone.utc).isoformat()
    diag["total_duration_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    return diag


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    log("Starting Full Refresh sequence diagnostic")
    log(f"Backend: {BASE_URL}")
    log(f"Output:  {OUTPUT_FILE}")
    log(f"Account: paper")
    log("")

    result = asyncio.run(run_diagnostic())

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    log(f"\nDiagnostic JSON saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
