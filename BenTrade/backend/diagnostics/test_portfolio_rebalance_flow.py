"""
Portfolio Rebalance Diagnostic — calls the rebalance workflow directly
and logs every stage. Does NOT modify any application code.

Usage: cd BenTrade/backend && python diagnostics/test_portfolio_rebalance_flow.py

Requires the backend to be running on localhost:5000.
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
OUTPUT_FILE = DIAG_DIR / "portfolio_rebalance_diagnostic.json"

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


# ══════════════════════════════════════════════════════════════════════
# DIAGNOSTICS
# ══════════════════════════════════════════════════════════════════════

async def run_diagnostic() -> dict:
    diag: dict = {
        "script": "test_portfolio_rebalance_flow.py",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend_url": BASE_URL,
        "steps": [],
    }

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=70.0) as client:

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

        # ── 1. Test A: Portfolio balance with EMPTY inputs ──────
        # This is the baseline — what happens with no pre-computed data.
        log("")
        log("═══════════════════════════════════════════════════════════")
        log("TEST A: Portfolio balance with EMPTY inputs (no pre-computed data)")
        log("═══════════════════════════════════════════════════════════")
        step_a: dict = {"step": "test_A_empty_inputs", "test": "A"}
        t_a = time.perf_counter()
        try:
            r = await client.post(
                "/api/tmc/workflows/portfolio-balance/run",
                json={
                    "account_mode": "paper",
                    "skip_model": True,
                    # No pre-computed results — all None
                },
            )
            dur_a = time.perf_counter() - t_a
            step_a["status_code"] = r.status_code
            step_a["duration_ms"] = round(dur_a * 1000, 1)
            data = safe_json(r)
            step_a["ok"] = data.get("ok")
            step_a["data_keys"] = list(data.keys())
            step_a["run_id"] = data.get("run_id")
            step_a["account_equity"] = data.get("account_equity")
            step_a["regime_label"] = data.get("regime_label")
            step_a["errors"] = data.get("errors", [])
            step_a["duration_reported_ms"] = data.get("duration_ms")
            step_a["stages"] = data.get("stages", {})

            log(f"  Response: HTTP {r.status_code} in {dur_a:.1f}s")
            log(f"  ok={data.get('ok')}, run_id={data.get('run_id')}")

            # Stage-by-stage analysis
            stages = data.get("stages", {})
            stage_order = [
                "account_state", "regime", "risk_policy",
                "active_trades", "stock_candidates", "options_candidates",
                "portfolio_state", "portfolio_balance",
            ]
            for stage_name in stage_order:
                s = stages.get(stage_name, {})
                status = s.get("status", "missing")
                dur = s.get("duration_ms", "?")
                extra_parts = []
                if stage_name == "regime":
                    extra_parts.append(f"label={s.get('regime_label')}")
                if stage_name == "active_trades":
                    extra_parts.append(f"count={s.get('count')}")
                if stage_name in ("stock_candidates", "options_candidates"):
                    extra_parts.append(f"count={s.get('count')}")
                extra = f" ({', '.join(extra_parts)})" if extra_parts else ""
                log(f"  [Stage: {stage_name}] status={status}, dur={dur}ms{extra}")

            # Account equity analysis (KEY diagnostic)
            equity = data.get("account_equity")
            log(f"\n  === ACCOUNT EQUITY: {equity} ===")
            if equity is None:
                log("  ⚠️  EQUITY IS NONE — Tradier API may not have returned balance data")
                step_a["equity_issue"] = "null"
            elif equity == 0 or equity == 0.0:
                log("  ⚠️  EQUITY IS ZERO — all downstream stages will produce empty/zero results")
                log("  Possible causes:")
                log("    - Wrong account_mode (live vs paper)")
                log("    - Paper account has no funded balance")
                log("    - Tradier API auth failure silently returned 0")
                step_a["equity_issue"] = "zero"
            else:
                log(f"  ✅ Account equity: ${equity:,.2f}")
                step_a["equity_issue"] = None

            # Rebalance plan analysis
            plan = data.get("rebalance_plan")
            if plan is None:
                log("  ⚠️  rebalance_plan is None — balancer failed or was skipped")
                step_a["has_plan"] = False
            else:
                step_a["has_plan"] = True
                plan_keys = list(plan.keys())
                log(f"  Rebalance plan keys: {plan_keys}")
                for key in ["close_actions", "hold_positions", "open_actions", "skip_actions"]:
                    items = plan.get(key, [])
                    log(f"    {key}: {len(items)}")

            # Errors analysis
            errs = data.get("errors", [])
            if errs:
                log(f"  ⚠️  ERRORS ({len(errs)}):")
                for e in errs:
                    log(f"    - {e}")
            else:
                log("  ✅ No errors")

            # Timing analysis
            reported = data.get("duration_ms", 0)
            if reported < 100:
                log(f"  ⚠️  SUSPICIOUS — workflow completed in {reported}ms "
                    "(< 100ms = not really running full stages)")
                step_a["suspicious_fast"] = True
            elif reported < 500:
                log(f"  ⚠️  FAST — workflow completed in {reported}ms "
                    "(< 500ms = might be skipping Tradier calls)")
                step_a["suspicious_fast"] = True
            else:
                log(f"  ✅ Workflow duration: {reported}ms — looks legitimate")

            # Save full response
            step_a["full_response"] = data

        except httpx.ReadTimeout:
            dur_a = time.perf_counter() - t_a
            step_a["error"] = "TIMEOUT after 70s"
            step_a["duration_ms"] = round(dur_a * 1000, 1)
            log(f"  ❌ TIMEOUT — workflow did not respond within 70s")
        except Exception as exc:
            step_a["error"] = str(exc)
            step_a["duration_ms"] = round((time.perf_counter() - t_a) * 1000, 1)
            log(f"  ❌ ERROR — {exc}")
        diag["steps"].append(step_a)

        # ── 2. First run active trades to get pre-computed data ─
        log("")
        log("═══════════════════════════════════════════════════════════")
        log("PREP: Running active trade pipeline to get pre-computed results…")
        log("═══════════════════════════════════════════════════════════")
        step_prep: dict = {"step": "prep_active_trades"}
        t_prep = time.perf_counter()
        active_results_for_b = None
        try:
            r = await client.post(
                "/api/active-trade-pipeline/run",
                params={"account_mode": "paper", "skip_model": "true"},
                timeout=200.0,
            )
            dur_prep = time.perf_counter() - t_prep
            step_prep["status_code"] = r.status_code
            step_prep["duration_ms"] = round(dur_prep * 1000, 1)
            data = safe_json(r)
            step_prep["ok"] = data.get("ok")
            step_prep["trade_count"] = data.get("trade_count")
            step_prep["recommendation_count"] = len(data.get("recommendations", []))
            log(f"  Active trade pipeline: ok={data.get('ok')}, "
                f"trades={data.get('trade_count')}, "
                f"recs={step_prep['recommendation_count']}, "
                f"duration={dur_prep:.1f}s")
            if data.get("ok"):
                active_results_for_b = data
        except Exception as exc:
            step_prep["error"] = str(exc)
            log(f"  ⚠️  Active trade pipeline failed — {exc}")
            log("       Test B will still run with empty active results")
        diag["steps"].append(step_prep)

        # ── 3. Test B: Portfolio balance WITH pre-computed data ──
        log("")
        log("═══════════════════════════════════════════════════════════")
        log("TEST B: Portfolio balance WITH pre-computed active trade results")
        log("═══════════════════════════════════════════════════════════")
        step_b: dict = {"step": "test_B_with_precomputed", "test": "B"}
        t_b = time.perf_counter()
        try:
            payload = {
                "account_mode": "paper",
                "skip_model": True,
                "active_trade_results": active_results_for_b or {"recommendations": [], "ok": True},
                # stock_results and options_results left as None
            }
            log(f"  Payload: active_recs={len((active_results_for_b or {}).get('recommendations', []))}, "
                f"stock=None, options=None")

            r = await client.post(
                "/api/tmc/workflows/portfolio-balance/run",
                json=payload,
            )
            dur_b = time.perf_counter() - t_b
            step_b["status_code"] = r.status_code
            step_b["duration_ms"] = round(dur_b * 1000, 1)
            data = safe_json(r)
            step_b["ok"] = data.get("ok")
            step_b["run_id"] = data.get("run_id")
            step_b["account_equity"] = data.get("account_equity")
            step_b["regime_label"] = data.get("regime_label")
            step_b["errors"] = data.get("errors", [])
            step_b["duration_reported_ms"] = data.get("duration_ms")
            step_b["stages"] = data.get("stages", {})

            log(f"  Response: HTTP {r.status_code} in {dur_b:.1f}s")
            log(f"  ok={data.get('ok')}, equity=${data.get('account_equity')}, "
                f"regime={data.get('regime_label')}")

            # Stage summary
            stages = data.get("stages", {})
            for stage_name in stage_order:
                s = stages.get(stage_name, {})
                log(f"  [Stage: {stage_name}] status={s.get('status', 'missing')}, "
                    f"dur={s.get('duration_ms', '?')}ms")

            # Rebalance plan
            plan = data.get("rebalance_plan")
            if plan:
                step_b["has_plan"] = True
                summary = data.get("active_trade_summary", {})
                log(f"  Rebalance plan summary: {json.dumps(summary)}")
                for key in ["close_actions", "hold_positions", "open_actions", "skip_actions"]:
                    items = plan.get(key, [])
                    log(f"    {key}: {len(items)}")
                    for item in items[:3]:
                        sym = item.get("symbol", "?")
                        act = item.get("action", item.get("recommendation", "?"))
                        reason = item.get("reason", "")[:80]
                        log(f"      → {sym}: {act} — {reason}")
            else:
                step_b["has_plan"] = False
                log("  ⚠️  rebalance_plan is None")

            errs = data.get("errors", [])
            if errs:
                log(f"  ⚠️  ERRORS ({len(errs)}):")
                for e in errs:
                    log(f"    - {e}")

            step_b["full_response"] = data

        except httpx.ReadTimeout:
            step_b["error"] = "TIMEOUT"
            step_b["duration_ms"] = round((time.perf_counter() - t_b) * 1000, 1)
            log(f"  ❌ TIMEOUT")
        except Exception as exc:
            step_b["error"] = str(exc)
            step_b["duration_ms"] = round((time.perf_counter() - t_b) * 1000, 1)
            log(f"  ❌ ERROR — {exc}")
        diag["steps"].append(step_b)

        # ── 4. Compare Test A vs Test B ─────────────────────────
        log("")
        log("═══════════════════════════════════════════════════════════")
        log("COMPARISON: Test A (empty) vs Test B (with active trades)")
        log("═══════════════════════════════════════════════════════════")
        comparison: dict = {"step": "comparison"}
        comparison["test_a_ok"] = step_a.get("ok")
        comparison["test_b_ok"] = step_b.get("ok")
        comparison["test_a_equity"] = step_a.get("account_equity")
        comparison["test_b_equity"] = step_b.get("account_equity")
        comparison["test_a_has_plan"] = step_a.get("has_plan")
        comparison["test_b_has_plan"] = step_b.get("has_plan")
        comparison["test_a_duration_ms"] = step_a.get("duration_ms")
        comparison["test_b_duration_ms"] = step_b.get("duration_ms")
        comparison["test_a_errors"] = step_a.get("errors", [])
        comparison["test_b_errors"] = step_b.get("errors", [])

        log(f"  Test A: ok={step_a.get('ok')}, equity={step_a.get('account_equity')}, "
            f"plan={step_a.get('has_plan')}, dur={step_a.get('duration_ms')}ms, "
            f"errs={len(step_a.get('errors', []))}")
        log(f"  Test B: ok={step_b.get('ok')}, equity={step_b.get('account_equity')}, "
            f"plan={step_b.get('has_plan')}, dur={step_b.get('duration_ms')}ms, "
            f"errs={len(step_b.get('errors', []))}")

        # Diagnose
        if step_a.get("equity_issue") in ("zero", "null") and step_b.get("account_equity") in (None, 0, 0.0):
            log("  ⚠️  BOTH tests have zero/null equity — Tradier account issue")
            comparison["diagnosis"] = "zero_equity_both"
        elif step_a.get("ok") and step_b.get("ok") and not step_a.get("has_plan") and not step_b.get("has_plan"):
            log("  ⚠️  BOTH tests ok but no plan — balancer may be returning None")
            comparison["diagnosis"] = "no_plan_both"
        elif step_b.get("ok") and step_b.get("has_plan"):
            log("  ✅ Test B produces a plan — workflow works when given data")
            comparison["diagnosis"] = "works_with_data"
        diag["steps"].append(comparison)

    # ── Summary ─────────────────────────────────────────────────
    log("")
    log("═══════════════════════════════════════════════════════════")
    log("SUMMARY")
    log("═══════════════════════════════════════════════════════════")

    equity_a = step_a.get("account_equity")
    has_plan_b = step_b.get("has_plan", False)
    errs_a = step_a.get("errors", [])
    errs_b = step_b.get("errors", [])
    dur_a_ms = step_a.get("duration_ms", 0)

    issues = []
    if equity_a is None or equity_a == 0:
        issues.append(f"Stage 1 (Account State) returned equity={equity_a} — "
                       "downstream stages operate on zero balance")
    if dur_a_ms < 100:
        issues.append(f"Workflow completed in {dur_a_ms}ms — suspiciously fast, "
                       "may be short-circuiting")
    if errs_a:
        issues.append(f"Test A had {len(errs_a)} errors: {errs_a}")
    if errs_b:
        issues.append(f"Test B had {len(errs_b)} errors: {errs_b}")
    if not has_plan_b:
        issues.append("Test B (with active trade data) still produced no rebalance plan")

    if issues:
        diag["conclusion"] = "ISSUES FOUND:\n" + "\n".join(f"  - {i}" for i in issues)
        for i in issues:
            log(f"  ⚠️  {i}")
    else:
        diag["conclusion"] = "Portfolio balance appears to work correctly."
        log("  ✅ Portfolio balance appears to work correctly")

    diag["completed_at"] = datetime.now(timezone.utc).isoformat()
    diag["total_duration_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    return diag


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    log("Starting portfolio rebalance diagnostic")
    log(f"Backend: {BASE_URL}")
    log(f"Output:  {OUTPUT_FILE}")
    log("")

    result = asyncio.run(run_diagnostic())

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    log(f"\nDiagnostic JSON saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
