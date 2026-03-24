#!/usr/bin/env python3
"""Build SUMMARY.json from diagnostics + pipeline output.

Reads:
  - results/diagnostics/options_pipeline_diag_*.json  (pipeline funnel counts)
  - results/diagnostics/options_diag_*.json           (per-strategy scan diagnostics)
  - results/diagnostics/narrow_diag_*.json            (chain narrowing diagnostics)
  - results/diagnostics/chain_diag_*.json             (chain fetch diagnostics)
  - data/workflows/options_opportunity/latest run output.json  (final 30 trades)

Writes:
  - results/diagnostics/SUMMARY.json

Ignores run1/ and run2/ subdirectories.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DIAG_DIR = BACKEND_ROOT / "results" / "diagnostics"
WORKFLOW_DIR = BACKEND_ROOT / "data" / "workflows" / "options_opportunity"
OUTPUT_PATH = DIAG_DIR / "SUMMARY.json"

SKIP_DIRS = {"run1", "run2"}


def _load_json(path: Path) -> dict | list | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"  WARN: Failed to read {path.name}: {exc}")
        return None


def _collect_diag_files() -> dict[str, list[Path]]:
    """Group diagnostics files by prefix, ignoring run1/run2."""
    buckets: dict[str, list[Path]] = defaultdict(list)
    for entry in sorted(DIAG_DIR.iterdir()):
        if entry.is_dir() and entry.name in SKIP_DIRS:
            continue
        if not entry.is_file() or not entry.suffix == ".json":
            continue
        if entry.name == "SUMMARY.json":
            continue
        name = entry.name
        if name.startswith("options_pipeline_diag"):
            buckets["pipeline"].append(entry)
        elif name.startswith("options_diag"):
            buckets["options_diag"].append(entry)
        elif name.startswith("narrow_diag"):
            buckets["narrow_diag"].append(entry)
        elif name.startswith("chain_diag"):
            buckets["chain_diag"].append(entry)
        else:
            buckets["other"].append(entry)
    return buckets


def _get_latest_run_output() -> dict | None:
    """Load the latest pipeline run's output.json."""
    latest_path = WORKFLOW_DIR / "latest.json"
    if not latest_path.exists():
        # Fall back to finding the most recent run directory
        run_dirs = sorted(
            [d for d in WORKFLOW_DIR.iterdir() if d.is_dir() and d.name.startswith("run_")],
            reverse=True,
        )
        if not run_dirs:
            return None
        output_file = run_dirs[0] / "output.json"
    else:
        latest = _load_json(latest_path)
        if latest and "run_id" in latest:
            output_file = WORKFLOW_DIR / latest["run_id"] / "output.json"
        else:
            return None

    if output_file.exists():
        return _load_json(output_file)
    return None


def _validate_trade(trade: dict) -> dict:
    """Run validity checks on a single enriched trade candidate.

    Checks:
    - Structural: legs present, strikes defined, side/type set
    - Math: max_loss > 0, EV present, POP in [0,1], width > 0
    - Quotes: bid/ask present and non-negative on all legs
    - Liquidity: OI > 0 on all legs
    - Pricing: debit or credit is positive
    - Risk/reward ratio sanity
    """
    issues: list[str] = []
    warnings: list[str] = []

    legs = trade.get("legs", [])
    math = trade.get("math", {})
    strategy = trade.get("strategy_id", "unknown")
    symbol = trade.get("symbol", "?")

    # ── Structural checks ──
    if not legs:
        issues.append("no_legs")
    for i, leg in enumerate(legs):
        if leg.get("strike") is None:
            issues.append(f"leg{i}_no_strike")
        if leg.get("side") not in ("long", "short"):
            issues.append(f"leg{i}_invalid_side")
        if leg.get("option_type") not in ("call", "put"):
            issues.append(f"leg{i}_invalid_option_type")

    # ── Quote integrity ──
    for i, leg in enumerate(legs):
        bid = leg.get("bid")
        ask = leg.get("ask")
        if bid is None:
            issues.append(f"leg{i}_missing_bid")
        elif bid < 0:
            issues.append(f"leg{i}_negative_bid")
        if ask is None:
            issues.append(f"leg{i}_missing_ask")
        elif ask < 0:
            issues.append(f"leg{i}_negative_ask")
        if bid is not None and ask is not None and ask < bid:
            issues.append(f"leg{i}_inverted_quote")

    # ── Liquidity ──
    for i, leg in enumerate(legs):
        oi = leg.get("open_interest", 0)
        if oi is None or oi <= 0:
            warnings.append(f"leg{i}_zero_oi")
        vol = leg.get("volume", 0)
        if vol is None or vol <= 0:
            warnings.append(f"leg{i}_zero_volume")

    # ── Math checks ──
    max_loss = math.get("max_loss")
    max_profit = math.get("max_profit")
    ev = math.get("ev")
    pop = math.get("pop")
    width = math.get("width")
    net_credit = math.get("net_credit")
    net_debit = math.get("net_debit")

    if max_loss is None:
        issues.append("missing_max_loss")
    elif max_loss <= 0:
        issues.append("non_positive_max_loss")

    if max_profit is None:
        issues.append("missing_max_profit")
    elif max_profit <= 0:
        issues.append("non_positive_max_profit")

    if ev is None:
        issues.append("missing_ev")

    if pop is not None:
        if pop < 0 or pop > 1:
            issues.append(f"pop_out_of_range_{pop}")
    else:
        issues.append("missing_pop")

    if width is not None and width <= 0:
        issues.append("non_positive_width")

    # Credit or debit should be present and positive
    has_credit = net_credit is not None and net_credit > 0
    has_debit = net_debit is not None and net_debit > 0
    if not has_credit and not has_debit:
        issues.append("no_positive_credit_or_debit")

    # ── Risk/reward ratio sanity ──
    if max_loss is not None and max_profit is not None and max_loss > 0:
        rr_ratio = max_profit / max_loss
        if rr_ratio > 100:
            warnings.append(f"extreme_reward_risk_ratio_{rr_ratio:.1f}")
        if rr_ratio < 0.1:
            warnings.append(f"poor_reward_risk_ratio_{rr_ratio:.3f}")

    # ── Underlying price vs strikes sanity ──
    underlying = trade.get("underlying_price")
    if underlying and legs:
        strikes = [l["strike"] for l in legs if l.get("strike") is not None]
        if strikes:
            max_strike_dist = max(abs(s - underlying) / underlying for s in strikes)
            if max_strike_dist > 0.50:
                warnings.append(f"strike_far_from_underlying_{max_strike_dist:.1%}")

    # ── DTE sanity ──
    dte = trade.get("dte")
    if dte is not None:
        if dte <= 0:
            issues.append("expired_or_zero_dte")
        elif dte > 365:
            warnings.append(f"very_long_dte_{dte}")

    valid = len(issues) == 0
    return {
        "valid": valid,
        "issues": issues,
        "warnings": warnings,
    }


def _summarize_trade(trade: dict, validation: dict) -> dict:
    """Extract a compact summary row for a trade."""
    math = trade.get("math", {})
    legs = trade.get("legs", [])

    strikes = "/".join(str(l.get("strike", "?")) for l in legs)
    leg_summary = " | ".join(
        f"{l.get('side','?')[0].upper()}{l.get('option_type','?')[0].upper()} {l.get('strike','?')}"
        for l in legs
    )

    return {
        "rank": trade.get("rank"),
        "candidate_id": trade.get("candidate_id"),
        "symbol": trade.get("symbol"),
        "strategy_id": trade.get("strategy_id"),
        "family": trade.get("family_key"),
        "expiration": trade.get("expiration"),
        "dte": trade.get("dte"),
        "dte_bucket": trade.get("dte_bucket"),
        "strikes": strikes,
        "leg_summary": leg_summary,
        "underlying_price": trade.get("underlying_price"),
        "net_credit": math.get("net_credit"),
        "net_debit": math.get("net_debit"),
        "max_profit": math.get("max_profit"),
        "max_loss": math.get("max_loss"),
        "width": math.get("width"),
        "pop": math.get("pop"),
        "ev": math.get("ev"),
        "ev_per_day": math.get("ev_per_day"),
        "ror": math.get("ror"),
        "kelly": math.get("kelly"),
        "breakeven": math.get("breakeven"),
        "regime_alignment": trade.get("regime_alignment"),
        "event_risk": trade.get("event_risk"),
        "passed_pipeline": trade.get("passed", False),
        "downstream_usable": trade.get("downstream_usable", False),
        "structural_ok": trade.get("structural_validation", {}).get("passed"),
        "math_ok": trade.get("math_validation", {}).get("passed"),
        "quote_sanity_ok": trade.get("hygiene", {}).get("quote_sanity_ok"),
        "liquidity_ok": trade.get("hygiene", {}).get("liquidity_ok"),
        "validation": validation,
    }


def build_summary() -> dict:
    print(f"Scanning diagnostics in: {DIAG_DIR}")
    buckets = _collect_diag_files()

    for prefix, files in sorted(buckets.items()):
        print(f"  {prefix}: {len(files)} files")

    # ── Pipeline funnel ──
    pipeline_funnel = {}
    for pf in buckets.get("pipeline", []):
        data = _load_json(pf)
        if data:
            pipeline_funnel = {
                "source_file": pf.name,
                "run_id": data.get("run_id"),
                "timestamp": data.get("timestamp"),
                "stage_2_scan": {
                    "total_raw_candidates": data.get("stage_2_scan", {}).get("total_raw_candidates"),
                    "total_rejected": data.get("stage_2_scan", {}).get("total_rejected"),
                    "total_passed": (
                        (data.get("stage_2_scan", {}).get("total_raw_candidates") or 0)
                        - (data.get("stage_2_scan", {}).get("total_rejected") or 0)
                    ),
                    "per_strategy_passed": data.get("stage_2_scan", {}).get("per_scanner_key_passed"),
                    "per_strategy_rejected": data.get("stage_2_scan", {}).get("per_scanner_key_rejected"),
                    "reject_reasons": data.get("stage_2_scan", {}).get("reject_reason_counts"),
                },
                "stage_3_validate": data.get("stage_3_validate"),
                "stage_4_enrich": data.get("stage_4_enrich"),
            }

    # ── Scan diagnostics summary (options_diag) ──
    scan_diag_summary = []
    for f in buckets.get("options_diag", []):
        data = _load_json(f)
        if not data:
            continue
        phase_b = data.get("phase_b", {})
        per_expiry = phase_b.get("per_expiry", [])
        total_constructed = sum(e.get("candidates_constructed", 0) for e in per_expiry)
        scan_diag_summary.append({
            "file": f.name,
            "symbol": data.get("symbol"),
            "strategy": data.get("strategy_id"),
            "total_expirations": data.get("phase_a", {}).get("total_expirations"),
            "total_contracts": data.get("phase_a", {}).get("total_contracts"),
            "candidates_constructed": total_constructed,
            "expirations_used": len(per_expiry),
        })

    # ── Chain diagnostics summary ──
    chain_diag_summary = []
    for f in buckets.get("chain_diag", []):
        data = _load_json(f)
        if not data:
            continue
        chain_diag_summary.append({
            "file": f.name,
            "symbol": data.get("symbol"),
            "strategy": data.get("scanner_key"),
            "data_source": data.get("data_source_class"),
            "total_contracts_fetched": data.get("total_contracts_fetched"),
            "expirations_with_contracts": data.get("expirations_with_contracts"),
        })

    # ── Narrowing diagnostics summary ──
    narrow_diag_summary = []
    for f in buckets.get("narrow_diag", []):
        data = _load_json(f)
        if not data:
            continue
        dq = data.get("data_quality", {})
        narrow_diag_summary.append({
            "file": f.name,
            "symbol": data.get("symbol"),
            "input_contracts": data.get("input_contract_count"),
            "after_expiry_filter": data.get("after_expiry_filter_count"),
            "after_strike_filter": data.get("after_strike_filter_count"),
            "contracts_final": data.get("contracts_final"),
            "data_quality_issues": sum(v for v in dq.values() if isinstance(v, int)),
        })

    # ── Load final trades from pipeline output ──
    print("\nLoading final pipeline output...")
    output_data = _get_latest_run_output()
    trade_summaries = []
    validation_counts = Counter()
    issue_counter = Counter()
    warning_counter = Counter()

    if output_data and "candidates" in output_data:
        candidates = output_data["candidates"]
        print(f"  Found {len(candidates)} candidates in output.json")

        for trade in candidates:
            val = _validate_trade(trade)
            summary = _summarize_trade(trade, val)
            trade_summaries.append(summary)

            if val["valid"]:
                validation_counts["valid"] += 1
            else:
                validation_counts["invalid"] += 1
            for iss in val["issues"]:
                issue_counter[iss] += 1
            for warn in val["warnings"]:
                warning_counter[warn] += 1
    else:
        print("  WARNING: No pipeline output found!")

    # ── Distribution analysis ──
    by_symbol = Counter(t["symbol"] for t in trade_summaries)
    by_strategy = Counter(t["strategy_id"] for t in trade_summaries)
    by_family = Counter(t["family"] for t in trade_summaries)
    by_dte_bucket = Counter(t["dte_bucket"] for t in trade_summaries)
    by_regime_alignment = Counter(t.get("regime_alignment") for t in trade_summaries)
    by_event_risk = Counter(t.get("event_risk") for t in trade_summaries)

    # ── EV / risk stats ──
    evs = [t["ev"] for t in trade_summaries if t.get("ev") is not None]
    pops = [t["pop"] for t in trade_summaries if t.get("pop") is not None]
    rors = [t["ror"] for t in trade_summaries if t.get("ror") is not None]
    max_losses = [t["max_loss"] for t in trade_summaries if t.get("max_loss") is not None]

    stats = {}
    if evs:
        stats["ev"] = {"min": min(evs), "max": max(evs), "mean": round(sum(evs) / len(evs), 2)}
    if pops:
        stats["pop"] = {"min": round(min(pops), 4), "max": round(max(pops), 4), "mean": round(sum(pops) / len(pops), 4)}
    if rors:
        stats["ror"] = {"min": round(min(rors), 2), "max": round(max(rors), 2), "mean": round(sum(rors) / len(rors), 2)}
    if max_losses:
        stats["max_loss"] = {"min": min(max_losses), "max": max(max_losses), "mean": round(sum(max_losses) / len(max_losses), 2)}

    # ── Assemble SUMMARY ──
    summary = {
        "_generated_at": datetime.now(timezone.utc).isoformat(),
        "_description": (
            "Trade validation summary for options pipeline output. "
            "Reviews the trades presented to the UI after passing all filters."
        ),
        "pipeline_funnel": pipeline_funnel,
        "diagnostics_file_counts": {
            prefix: len(files) for prefix, files in sorted(buckets.items())
        },
        "final_trades": {
            "total_presented": len(trade_summaries),
            "run_id": output_data.get("run_id") if output_data else None,
            "generated_at": output_data.get("generated_at") if output_data else None,
            "market_state_ref": output_data.get("market_state_ref") if output_data else None,
            "publication_status": output_data.get("publication", {}).get("status") if output_data else None,
            "validation_result": {
                "valid_count": validation_counts.get("valid", 0),
                "invalid_count": validation_counts.get("invalid", 0),
                "all_valid": validation_counts.get("invalid", 0) == 0,
                "issue_counts": dict(issue_counter.most_common()) if issue_counter else {},
                "warning_counts": dict(warning_counter.most_common()) if warning_counter else {},
            },
            "distribution": {
                "by_symbol": dict(by_symbol.most_common()),
                "by_strategy": dict(by_strategy.most_common()),
                "by_family": dict(by_family.most_common()),
                "by_dte_bucket": dict(by_dte_bucket.most_common()),
                "by_regime_alignment": dict(by_regime_alignment.most_common()),
                "by_event_risk": dict(by_event_risk.most_common()),
            },
            "stats": stats,
            "trades": trade_summaries,
        },
        "scan_diagnostics_summary": scan_diag_summary,
        "chain_diagnostics_summary": chain_diag_summary,
        "narrowing_diagnostics_summary": narrow_diag_summary,
    }

    # ── Verdict ──
    invalid = validation_counts.get("invalid", 0)
    total = len(trade_summaries)
    if total == 0:
        verdict = "NO_TRADES — pipeline produced zero candidates"
    elif invalid == 0:
        verdict = f"ALL_VALID — all {total} presented trades pass validation"
    else:
        verdict = f"ISSUES_FOUND — {invalid}/{total} trades have validation issues"

    summary["verdict"] = verdict

    return summary


def main():
    summary = build_summary()

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\n{'='*60}")
    print(f"SUMMARY written to: {OUTPUT_PATH}")
    print(f"Verdict: {summary['verdict']}")

    ft = summary["final_trades"]
    print(f"\nFinal trades: {ft['total_presented']}")
    vr = ft["validation_result"]
    print(f"  Valid: {vr['valid_count']}  Invalid: {vr['invalid_count']}")
    if vr["issue_counts"]:
        print(f"  Issues: {vr['issue_counts']}")
    if vr["warning_counts"]:
        print(f"  Warnings: {vr['warning_counts']}")

    dist = ft["distribution"]
    print(f"\n  By symbol:   {dict(dist['by_symbol'])}")
    print(f"  By strategy: {dict(dist['by_strategy'])}")
    print(f"  By DTE:      {dict(dist['by_dte_bucket'])}")

    stats = ft.get("stats", {})
    if stats.get("ev"):
        print(f"\n  EV range:       ${stats['ev']['min']:.0f} – ${stats['ev']['max']:.0f}  (mean ${stats['ev']['mean']:.0f})")
    if stats.get("pop"):
        print(f"  POP range:      {stats['pop']['min']:.1%} – {stats['pop']['max']:.1%}  (mean {stats['pop']['mean']:.1%})")
    if stats.get("ror"):
        print(f"  RoR range:      {stats['ror']['min']:.1f}x – {stats['ror']['max']:.1f}x  (mean {stats['ror']['mean']:.1f}x)")
    if stats.get("max_loss"):
        print(f"  Max loss range: ${stats['max_loss']['min']:.0f} – ${stats['max_loss']['max']:.0f}  (mean ${stats['max_loss']['mean']:.0f})")


if __name__ == "__main__":
    main()
