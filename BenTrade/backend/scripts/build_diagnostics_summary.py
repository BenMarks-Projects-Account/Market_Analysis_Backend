"""
Build a single SUMMARY.json from all diagnostic JSON files in results/diagnostics/.

Reads chain_diag_*, narrow_diag_*, options_diag_*, and options_pipeline_diag_* files,
classifies them, extracts key fields, computes a pipeline funnel summary, and writes
results/diagnostics/SUMMARY.json.

Usage:
    python scripts/build_diagnostics_summary.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


DIAG_DIR = Path(__file__).resolve().parent.parent / "results" / "diagnostics"
OUTPUT_FILE = DIAG_DIR / "SUMMARY.json"


def _safe_get(d: dict, *keys, default=None):
    """Nested safe-get."""
    obj = d
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k, default)
    return obj


def _truncate_list(lst, n=5):
    """Return first n items of a list (or the full list if shorter)."""
    if not isinstance(lst, list):
        return lst
    return lst[:n]


# ── Collectors ────────────────────────────────────────────────────────────────

def collect_json_files(diag_dir: Path) -> list[Path]:
    """Recursively find all .json files (skip SUMMARY.json)."""
    files = []
    for root, _dirs, filenames in os.walk(diag_dir):
        for fn in sorted(filenames):
            if fn.endswith(".json") and fn != "SUMMARY.json":
                files.append(Path(root) / fn)
    return files


def classify_file(name: str) -> str:
    """Return category based on filename prefix."""
    if name.startswith("chain_diag_"):
        return "chain"
    if name.startswith("narrow_diag_"):
        return "narrow"
    if name.startswith("options_pipeline_diag_"):
        return "pipeline"
    if name.startswith("options_diag_"):
        return "scanner"
    return "unknown"


# ── Per-category parsers ──────────────────────────────────────────────────────

def parse_chain(data: dict, filename: str) -> dict:
    symbol = data.get("symbol", "UNKNOWN")
    return {
        "file": filename,
        "symbol": symbol,
        "data_source_class": data.get("data_source_class"),
        "underlying_price": data.get("underlying_price"),
        "expirations_returned": data.get("expirations_returned", 0),
        "expiration_list": data.get("expiration_list", []),
        "total_contracts_fetched": data.get("total_contracts_fetched", 0),
        "per_expiration_contract_counts": data.get("per_expiration_contract_counts", {}),
        "sample_contracts": _truncate_list(data.get("sample_contracts", []), 3),
    }


def parse_narrow(data: dict, filename: str) -> dict:
    return {
        "file": filename,
        "symbol": data.get("symbol", "UNKNOWN"),
        "underlying_price": data.get("underlying_price"),
        "dte_min": data.get("dte_min"),
        "dte_max": data.get("dte_max"),
        "option_types": data.get("option_types", []),
        "multi_expiry": data.get("multi_expiry"),
        "input_contract_count": data.get("input_contract_count", 0),
        "after_normalize_count": data.get("after_normalize_count", 0),
        "unique_expirations_after_normalize": data.get("unique_expirations_after_normalize", 0),
        "sample_expirations": _truncate_list(data.get("sample_expirations", []), 10),
        "after_expiry_filter_count": data.get("after_expiry_filter_count", 0),
        "expirations_kept": data.get("expirations_kept", 0),
        "expirations_dropped": data.get("expirations_dropped", 0),
        "expirations_kept_list": data.get("expirations_kept_list", []),
        "expiry_drop_reasons": data.get("expiry_drop_reasons", {}),
        "after_strike_filter_count": data.get("after_strike_filter_count", 0),
        "contracts_final": data.get("contracts_final", 0),
        "strike_drop_reasons": data.get("strike_drop_reasons", {}),
        "data_quality": data.get("data_quality", {}),
    }


def _truncate_delta_values(per_expiry_list: list) -> list:
    """For each expiry in phase_b.per_expiry, keep only first 5 delta_values."""
    result = []
    for entry in (per_expiry_list or []):
        if not isinstance(entry, dict):
            result.append(entry)
            continue
        trimmed = dict(entry)
        if "delta_values" in trimmed:
            trimmed["delta_values"] = _truncate_list(trimmed["delta_values"], 5)
        result.append(trimmed)
    return result


def parse_scanner(data: dict, filename: str) -> dict:
    scanner_key = data.get("scanner_key", "unknown")
    phase_a = data.get("phase_a", {})
    phase_b_raw = data.get("phase_b", {})

    # Build phase_b summary with truncated per_expiry delta_values
    phase_b = dict(phase_b_raw)
    if "per_expiry" in phase_b:
        phase_b["per_expiry"] = _truncate_delta_values(phase_b["per_expiry"])

    return {
        "file": filename,
        "scanner_key": scanner_key,
        "symbol": data.get("symbol", "UNKNOWN"),
        "data_source_class": data.get("data_source_class"),
        "underlying_price": _safe_get(phase_a, "underlying_price"),
        "phase_a": {
            "total_expirations": phase_a.get("total_expirations", 0),
            "expiration_dates": phase_a.get("expiration_dates", []),
            "total_contracts": phase_a.get("total_contracts", 0),
            "contracts_per_expiry": phase_a.get("contracts_per_expiry", {}),
        },
        "phase_b": phase_b,
        "config": data.get("config"),
    }


def parse_pipeline(data: dict, filename: str) -> dict:
    stage2 = data.get("stage_2_scan", {})
    stage3 = data.get("stage_3_validate", {})
    stage4 = data.get("stage_4_enrich", {})

    result = {
        "file": filename,
        "run_id": data.get("run_id"),
        "timestamp": data.get("timestamp"),
        "stage_2_scan": {
            "total_raw_candidates": stage2.get("total_raw_candidates", 0),
            "total_rejected": stage2.get("total_rejected", 0),
            "per_scanner_key_passed": stage2.get("per_scanner_key_passed", {}),
            "per_scanner_key_rejected": stage2.get("per_scanner_key_rejected", {}),
            "reject_reason_counts": stage2.get("reject_reason_counts", {}),
        },
        "stage_3_validate": {
            "validated_count": stage3.get("validated_count", 0),
            "filtered_count": stage3.get("filtered_count", 0),
            "filter_reasons": stage3.get("filter_reasons", {}),
        },
        "stage_4_enrich": {
            "enriched_count": stage4.get("enriched_count", 0),
            "credibility_filter": stage4.get("credibility_filter", {}),
            "market_state_ref": stage4.get("market_state_ref"),
        },
    }

    # Include any extra stage keys present in the data
    for key in data:
        if key.startswith("stage_5") or key.startswith("stage_6"):
            result[key] = data[key]

    return result


# ── Funnel summary ────────────────────────────────────────────────────────────

def compute_funnel(chain_entries, narrow_entries, scanner_entries, pipeline_entries) -> dict:
    chain_total = sum(e.get("total_contracts_fetched", 0) for e in chain_entries)
    narrow_total = sum(e.get("contracts_final", 0) for e in narrow_entries)
    constructed_total = 0
    for e in scanner_entries:
        constructed_total += _safe_get(e, "phase_b", "total_constructed", default=0)

    # Use the latest pipeline entry (highest timestamp) as the authoritative one
    pipeline_raw = 0
    pipeline_validated = 0
    pipeline_credibility_passed = 0
    pipeline_final = 0
    pipeline_enriched = 0

    if pipeline_entries:
        # Sort by timestamp and take the last one
        latest = sorted(pipeline_entries, key=lambda p: p.get("timestamp", ""))[-1]
        pipeline_raw = _safe_get(latest, "stage_2_scan", "total_raw_candidates", default=0)
        pipeline_validated = _safe_get(latest, "stage_3_validate", "validated_count", default=0)
        pipeline_credibility_passed = _safe_get(
            latest, "stage_4_enrich", "credibility_filter", "passed_count", default=0
        )
        pipeline_enriched = _safe_get(latest, "stage_4_enrich", "enriched_count", default=0)
        pipeline_final = pipeline_enriched if pipeline_enriched else pipeline_credibility_passed

    # Determine verdict
    verdict = _compute_verdict(
        chain_total, narrow_total, constructed_total,
        pipeline_raw, pipeline_validated,
        pipeline_credibility_passed, pipeline_enriched,
    )

    return {
        "chain_contracts_fetched": chain_total,
        "after_narrowing": narrow_total,
        "candidates_constructed": constructed_total,
        "pipeline_raw_candidates": pipeline_raw,
        "pipeline_after_validation": pipeline_validated,
        "pipeline_after_credibility": pipeline_credibility_passed,
        "pipeline_enriched_count": pipeline_enriched,
        "pipeline_final_selected": pipeline_final,
        "verdict": verdict,
    }


def _compute_verdict(
    chain_total, narrow_total, constructed_total,
    pipeline_raw, pipeline_validated,
    pipeline_cred_passed, pipeline_enriched,
) -> str:
    if chain_total == 0:
        return ("No data flowing through pipeline at all — "
                "check data source wiring and API connectivity")
    if chain_total < 100:
        return ("Chain fetch returned insufficient data — "
                "check Tradier API connection and credentials")
    if narrow_total < chain_total * 0.01:
        return ("Phase A narrowing is filtering too aggressively — "
                "check DTE window and strike filters")
    if constructed_total == 0 and narrow_total > 0:
        return ("Phase B construction produced zero candidates — "
                "check delta filtering and width parameters")
    if pipeline_raw == 0 and constructed_total > 0:
        return ("Candidates constructed but not reaching pipeline — "
                "check how scanner results flow to the workflow runner")
    if pipeline_cred_passed == 0 and pipeline_raw > 0:
        return ("Credibility gate rejecting all candidates — "
                "check minimum premium, POP, and bid requirements")
    if pipeline_enriched == 0 and pipeline_cred_passed > 0:
        return ("Credibility gate passes candidates but enrichment produces zero — "
                "check _stage_enrich_evaluate for config/runtime errors")
    if pipeline_enriched > 0:
        return "Pipeline produced enriched candidates — check downstream selection/output"

    return "Unable to determine bottleneck — review individual stage data"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not DIAG_DIR.exists():
        print(f"ERROR: diagnostics directory not found: {DIAG_DIR}", file=sys.stderr)
        sys.exit(1)

    all_files = collect_json_files(DIAG_DIR)
    if not all_files:
        print("ERROR: no .json files found in diagnostics directory", file=sys.stderr)
        sys.exit(1)

    chain_entries: list[dict] = []
    narrow_entries: list[dict] = []
    scanner_entries: list[dict] = []
    pipeline_entries: list[dict] = []
    file_list: list[str] = []
    errors: list[dict] = []

    for fp in all_files:
        rel = fp.relative_to(DIAG_DIR).as_posix()
        file_list.append(rel)
        category = classify_file(fp.name)

        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            errors.append({"file": rel, "error": str(exc)})
            continue

        if category == "chain":
            chain_entries.append(parse_chain(data, rel))
        elif category == "narrow":
            narrow_entries.append(parse_narrow(data, rel))
        elif category == "scanner":
            scanner_entries.append(parse_scanner(data, rel))
        elif category == "pipeline":
            pipeline_entries.append(parse_pipeline(data, rel))
        # skip unknown

    # Build chain_diagnostics grouped per_symbol
    chain_per_symbol: dict[str, dict] = {}
    for entry in chain_entries:
        sym = entry["symbol"]
        if sym not in chain_per_symbol:
            chain_per_symbol[sym] = {
                "data_source_class": entry["data_source_class"],
                "underlying_price": entry["underlying_price"],
                "expirations_returned": entry["expirations_returned"],
                "expiration_list": entry["expiration_list"],
                "total_contracts_fetched": entry["total_contracts_fetched"],
                "per_expiration_contract_counts": entry["per_expiration_contract_counts"],
                "sample_contracts": entry["sample_contracts"],
                "files": [entry["file"]],
            }
        else:
            # Accumulate files list; keep first entry's data (same chain per symbol)
            chain_per_symbol[sym]["files"].append(entry["file"])

    # Build scanner_diagnostics grouped by scanner_key + symbol
    scanner_per_key: dict[str, dict] = {}
    for entry in scanner_entries:
        key = f"{entry['scanner_key']}_{entry['symbol']}"
        scanner_per_key[key] = entry

    funnel = compute_funnel(chain_entries, narrow_entries, scanner_entries, pipeline_entries)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_diagnostic_files": len(file_list),
        "file_list": file_list,
        "parse_errors": errors if errors else None,
        "chain_diagnostics": {
            "files_found": len(chain_entries),
            "per_symbol": chain_per_symbol,
        },
        "narrow_diagnostics": {
            "files_found": len(narrow_entries),
            "runs": narrow_entries,
        },
        "scanner_diagnostics": {
            "files_found": len(scanner_entries),
            "per_scanner_key": scanner_per_key,
        },
        "pipeline_diagnostics": {
            "files_found": len(pipeline_entries),
            "runs": pipeline_entries,
        },
        "funnel_summary": funnel,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"Wrote {OUTPUT_FILE} ({len(file_list)} files processed)")
    print()
    print("=== FUNNEL SUMMARY ===")
    for k, v in funnel.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
