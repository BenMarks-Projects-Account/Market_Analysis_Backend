"""Tests for app.utils.report_conformance."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.utils.report_conformance import is_conforming_report, validate_report_file


# ---------------------------------------------------------------------------
# is_conforming_report
# ---------------------------------------------------------------------------

class TestIsConformingReport:
    """Unit tests for the pure-function checker."""

    def test_valid_report(self):
        data = {
            "trades": [
                {
                    "trade_key": "SPY|2026-03-20|put_credit_spread|580|575|5",
                    "strategy_id": "put_credit_spread",
                    "computed": {"max_profit": 100},
                    "details": {"dte": 30},
                    "pills": {"strategy_label": "Put Credit Spread"},
                }
            ]
        }
        assert is_conforming_report(data) is True

    def test_empty_trades_rejected(self):
        assert is_conforming_report({"trades": []}) is False

    def test_no_trades_key_rejected(self):
        assert is_conforming_report({"report_stats": {}}) is False

    def test_list_top_level_rejected(self):
        assert is_conforming_report([{"trade_key": "x"}]) is False

    def test_not_a_dict_rejected(self):
        assert is_conforming_report("hello") is False
        assert is_conforming_report(None) is False

    def test_trade_missing_canonical_fields(self):
        data = {
            "trades": [
                {
                    "spread_type": "put_credit_spread",
                    "max_profit_per_share": 0.5,
                }
            ]
        }
        assert is_conforming_report(data) is False

    def test_partial_canonical(self):
        data = {
            "trades": [
                {
                    "trade_key": "SPY|...",
                    "strategy_id": "put_credit_spread",
                    "computed": {},
                    # missing details and pills
                }
            ]
        }
        assert is_conforming_report(data) is False


# ---------------------------------------------------------------------------
# validate_report_file
# ---------------------------------------------------------------------------

class TestValidateReportFile:
    """Integration tests for file-level validation."""

    def _write(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_conforming_file_returned(self, tmp_path: Path):
        p = tmp_path / "good.json"
        report = {
            "trades": [
                {
                    "trade_key": "SPY|2026-03-20|put_credit_spread|580|575|5",
                    "strategy_id": "put_credit_spread",
                    "computed": {},
                    "details": {},
                    "pills": {},
                }
            ]
        }
        self._write(p, report)
        result = validate_report_file(p)
        assert result is not None
        assert result["trades"][0]["trade_key"] == report["trades"][0]["trade_key"]
        assert p.exists()  # not deleted

    def test_non_conforming_file_deleted(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        self._write(p, {"trades": [{"spread_type": "put_credit_spread"}]})
        result = validate_report_file(p)
        assert result is None
        assert not p.exists()

    def test_invalid_json_deleted(self, tmp_path: Path):
        p = tmp_path / "corrupt.json"
        p.write_text("{not valid json", encoding="utf-8")
        result = validate_report_file(p)
        assert result is None
        assert not p.exists()

    def test_missing_file_returns_none(self, tmp_path: Path):
        p = tmp_path / "missing.json"
        result = validate_report_file(p)
        assert result is None

    def test_auto_delete_false_keeps_file(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        self._write(p, {"trades": []})
        result = validate_report_file(p, auto_delete=False)
        assert result is None
        assert p.exists()  # not deleted

    def test_validation_event_emitted(self, tmp_path: Path):
        """When a ValidationEventsService is provided, it logs the event."""

        class FakeVE:
            events: list[dict[str, Any]] = []

            def append_event(self, *, severity, code, message, context=None):
                self.events.append({"severity": severity, "code": code, "message": message, "context": context})

        ve = FakeVE()
        p = tmp_path / "legacy.json"
        self._write(p, [{"spread_type": "x"}])  # list top-level
        result = validate_report_file(p, validation_events=ve)
        assert result is None
        assert len(ve.events) == 1
        assert ve.events[0]["code"] == "NON_CONFORMING_FILE_ENCOUNTERED"
        assert "legacy.json" in ve.events[0]["message"]

    def test_empty_trades_are_non_conforming(self, tmp_path: Path):
        p = tmp_path / "empty.json"
        self._write(p, {"trades": []})
        result = validate_report_file(p)
        assert result is None
        assert not p.exists()
