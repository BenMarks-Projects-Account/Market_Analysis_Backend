from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.main import create_app

LEGACY_STRATEGY_STRINGS: set[str] = {
    "put_credit",
    "call_credit",
    "credit_put_spread",
    "credit_call_spread",
}

REPORT_PATTERNS: tuple[str, ...] = (
    "analysis_*.json",
    "*_analysis_*.json",
)


@dataclass(frozen=True)
class RegenerationTask:
    strategy_id: str
    request_payload: dict[str, Any]


def _contains_legacy_strategy_string(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in LEGACY_STRATEGY_STRINGS:
            return True
        if "|put_credit|" in normalized or "|call_credit|" in normalized:
            return True
        return False
    if isinstance(value, list):
        return any(_contains_legacy_strategy_string(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_legacy_strategy_string(item) for item in value.values())
    return False


def _iter_report_files(results_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in REPORT_PATTERNS:
        files.extend(results_dir.glob(pattern))
    unique = sorted({path.resolve() for path in files if path.is_file()}, reverse=True)
    return [Path(path) for path in unique]


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, (dict, list)):
            return payload
        return None
    except Exception:
        return None


def _extract_regeneration_task(payload: dict[str, Any]) -> RegenerationTask | None:
    strategy_id = str(payload.get("strategyId") or "").strip()
    if not strategy_id:
        return None

    request_payload: dict[str, Any] = {}
    symbol = str(payload.get("symbol") or "").strip().upper()
    expiration = str(payload.get("expiration") or "").strip()
    if symbol:
        request_payload["symbol"] = symbol
    if expiration:
        request_payload["expiration"] = expiration

    return RegenerationTask(strategy_id=strategy_id, request_payload=request_payload)


def find_legacy_reports(results_dir: Path) -> tuple[list[Path], list[RegenerationTask]]:
    legacy_reports: list[Path] = []
    tasks: list[RegenerationTask] = []

    for report_path in _iter_report_files(results_dir):
        payload = _read_json(report_path)
        if payload is None:
            continue
        if not _contains_legacy_strategy_string(payload):
            continue

        legacy_reports.append(report_path)
        if isinstance(payload, dict):
            task = _extract_regeneration_task(payload)
            if task is not None:
                tasks.append(task)

    deduped_tasks: list[RegenerationTask] = []
    seen: set[tuple[str, str]] = set()
    for task in tasks:
        key = (task.strategy_id, json.dumps(task.request_payload, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        deduped_tasks.append(task)

    return legacy_reports, deduped_tasks


def archive_or_delete_reports(
    report_paths: list[Path],
    *,
    mode: str,
    archive_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    archived: list[str] = []
    deleted: list[str] = []

    resolved_mode = str(mode or "archive").strip().lower()
    if resolved_mode not in {"archive", "delete"}:
        raise ValueError("mode must be 'archive' or 'delete'")

    target_archive = archive_dir
    if resolved_mode == "archive":
        if target_archive is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            target_archive = (report_paths[0].parent if report_paths else Path.cwd()) / "archive_legacy_strategy_reports" / stamp
        if not dry_run:
            target_archive.mkdir(parents=True, exist_ok=True)

    for source_path in report_paths:
        if resolved_mode == "archive":
            destination = (target_archive or source_path.parent) / source_path.name
            if not dry_run:
                shutil.move(str(source_path), str(destination))
            archived.append(str(destination))
        else:
            if not dry_run and source_path.exists():
                source_path.unlink()
            deleted.append(str(source_path))

    return {
        "archived": archived,
        "deleted": deleted,
    }


async def regenerate_reports(tasks: list[RegenerationTask]) -> dict[str, Any]:
    if not tasks:
        return {"generated": [], "failed": []}

    app = create_app()
    strategy_service = app.state.strategy_service

    generated: list[str] = []
    failed: list[dict[str, str]] = []

    for task in tasks:
        try:
            result = await strategy_service.generate(
                strategy_id=task.strategy_id,
                request_payload=dict(task.request_payload),
            )
            generated.append(str(result.get("filename") or ""))
        except Exception as exc:
            failed.append(
                {
                    "strategy_id": task.strategy_id,
                    "error": str(exc),
                }
            )

    return {
        "generated": generated,
        "failed": failed,
    }


def run_cleanup(
    *,
    results_dir: Path,
    mode: str,
    regenerate: bool,
    dry_run: bool,
) -> dict[str, Any]:
    legacy_reports, tasks = find_legacy_reports(results_dir)

    cleanup_summary = archive_or_delete_reports(
        legacy_reports,
        mode=mode,
        archive_dir=None,
        dry_run=dry_run,
    )

    regen_summary: dict[str, Any] = {"generated": [], "failed": []}
    if regenerate and not dry_run:
        regen_summary = asyncio.run(regenerate_reports(tasks))

    return {
        "results_dir": str(results_dir),
        "legacy_reports_found": len(legacy_reports),
        "regeneration_candidates": len(tasks),
        "mode": mode,
        "dry_run": dry_run,
        "cleanup": cleanup_summary,
        "regeneration": regen_summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive/delete legacy scan reports containing deprecated strategy IDs and optionally regenerate fresh reports.",
    )
    parser.add_argument(
        "--results-dir",
        default=str(Path(__file__).resolve().parents[2] / "results"),
        help="Path to backend results directory (default: backend/results)",
    )
    parser.add_argument(
        "--mode",
        choices=("archive", "delete"),
        default="archive",
        help="Whether to archive or delete matching legacy reports.",
    )
    parser.add_argument(
        "--no-regenerate",
        action="store_true",
        help="Do not regenerate fresh reports after cleanup.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without mutating report files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_cleanup(
        results_dir=Path(args.results_dir),
        mode=str(args.mode),
        regenerate=not bool(args.no_regenerate),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
