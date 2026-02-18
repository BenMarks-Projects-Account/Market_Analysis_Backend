from __future__ import annotations

import json
from pathlib import Path

from app.tools.legacy_strategy_report_cleanup import (
    archive_or_delete_reports,
    find_legacy_reports,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_find_legacy_reports_detects_put_credit_strings(tmp_path: Path) -> None:
    legacy_path = tmp_path / "credit_spread_analysis_20260217_120000.json"
    canonical_path = tmp_path / "analysis_20260217_130000.json"

    _write_json(
        legacy_path,
        {
            "strategyId": "credit_spread",
            "symbol": "QQQ",
            "expiration": "2026-03-20",
            "trades": [
                {
                    "spread_type": "put_credit",
                    "trade_key": "QQQ|2026-03-20|put_credit|510|500|31",
                }
            ],
        },
    )
    _write_json(
        canonical_path,
        {
            "strategyId": "credit_spread",
            "symbol": "QQQ",
            "expiration": "2026-03-20",
            "trades": [
                {
                    "spread_type": "put_credit_spread",
                    "trade_key": "QQQ|2026-03-20|put_credit_spread|510|500|31",
                }
            ],
        },
    )

    reports, tasks = find_legacy_reports(tmp_path)

    assert len(reports) == 1
    assert reports[0].name == legacy_path.name
    assert len(tasks) == 1
    assert tasks[0].strategy_id == "credit_spread"
    assert tasks[0].request_payload.get("symbol") == "QQQ"


def test_archive_or_delete_reports_archive_mode_moves_files(tmp_path: Path) -> None:
    report_path = tmp_path / "analysis_20260217_140000.json"
    _write_json(report_path, {"trades": [{"spread_type": "put_credit"}]})

    archive_dir = tmp_path / "archive"
    summary = archive_or_delete_reports([report_path], mode="archive", archive_dir=archive_dir, dry_run=False)

    assert not report_path.exists()
    archived_paths = summary.get("archived") or []
    assert len(archived_paths) == 1
    moved = Path(archived_paths[0])
    assert moved.exists()
    assert moved.parent == archive_dir
