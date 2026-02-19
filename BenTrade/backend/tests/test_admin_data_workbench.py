from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import create_app  # noqa: E402
from app.services.validation_events import ValidationEventsService  # noqa: E402
from app.utils.computed_metrics import CORE_COMPUTED_METRIC_FIELDS  # noqa: E402


class _DummyBaseDataService:
    def get_source_health_snapshot(self) -> dict:
        return {"tradier": {"status": "green", "message": "ok"}}


def _build_client(tmp_path: Path) -> TestClient:
    app = create_app()
    app.state.base_data_service = _DummyBaseDataService()
    app.state.results_dir = tmp_path
    app.state.validation_events = ValidationEventsService(results_dir=tmp_path)
    app.state.data_workbench_service = None
    return TestClient(app)


def test_data_workbench_trade_found_in_latest_report(tmp_path: Path) -> None:
    report_path = tmp_path / "credit_spread_analysis_20260217_120000.json"
    report_payload = {
        "strategyId": "credit_spread",
        "generated_at": "2026-02-17T12:00:00+00:00",
        "expiration": "2026-03-20",
        "trades": [
            {
                "trade_key": "QQQ|2026-03-20|put_credit_spread|510|500|31",
                "strategy_id": "put_credit_spread",
                "underlying": "QQQ",
                "spread_type": "put_credit_spread",
                "short_strike": 510,
                "long_strike": 500,
                "dte": 31,
                "rank_score": 89.1,
                "composite_score": 88.0,
                "ev_per_share": 1.22,
                "p_win_used": 0.68,
                "max_profit": 145.0,
                "max_loss": 855.0,
                "break_even": 508.55,
                "return_on_risk": 0.17,
                "validation_warnings": ["SAMPLE_WARNING"],
                "computed": {
                    "max_profit": 145.0,
                    "max_loss": 855.0,
                    "pop": 0.68,
                    "expected_value": 122.0,
                    "return_on_risk": 0.17,
                },
                "details": {"dte": 31, "break_even": 508.55},
                "pills": {"strategy_label": "Put Credit Spread", "dte": 31, "pop": 0.68},
                "input_snapshot": {
                    "underlying_snapshot": {"price": 520.1},
                    "chain_metadata": {"contracts": 100},
                    "pricing_source": "tradier",
                    "timestamp": "2026-02-17T11:59:30+00:00",
                },
            }
        ],
    }
    report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")

    client = _build_client(tmp_path)
    trade_key = "QQQ|2026-03-20|put_credit_spread|510|500|31"

    response = client.get(f"/api/admin/data-workbench/trade/{trade_key}")
    assert response.status_code == 200

    payload = response.json()
    assert payload["trade_key"] == trade_key
    assert payload["trade"]["trade_key"] == trade_key
    assert payload["trade"]["strategy_id"] == "put_credit_spread"
    assert payload["sources"]["where_found"][0] == "latest_scan_report"
    assert payload["sources"]["report_id"] == report_path.name

    trade_json = payload["trade_json"]
    assert "raw_candidate" in trade_json
    assert isinstance(trade_json.get("input_snapshot"), dict)
    assert "computed_metrics" in trade_json
    assert "metrics_status" in trade_json
    assert set(CORE_COMPUTED_METRIC_FIELDS).issubset(set((trade_json.get("computed_metrics") or {}).keys()))
    assert isinstance((trade_json.get("metrics_status") or {}).get("ready"), bool)
    assert isinstance((trade_json.get("metrics_status") or {}).get("missing_fields"), list)
    assert "SAMPLE_WARNING" in trade_json.get("validation_warnings", [])

    query_response = client.get(f"/api/admin/data-workbench/trade?trade_key={trade_key}")
    assert query_response.status_code == 200
    query_payload = query_response.json()
    assert query_payload.get("trade_key") == trade_key
    assert query_payload.get("strategy_id") == "put_credit_spread"
    assert isinstance(query_payload.get("input_snapshot"), dict)
    assert isinstance(query_payload.get("trade_output"), dict)


def test_data_workbench_trade_falls_back_to_ledger(tmp_path: Path) -> None:
    ledger_path = tmp_path / "trade_ledger.jsonl"
    ledger_rows = [
        {
            "ts": "2026-02-17T10:00:00+00:00",
            "event": "CREATE",
            "trade_key": "QQQ|2026-03-20|put_credit_spread|510|500|31",
            "source": "scanner",
            "payload": {
                "underlying": "QQQ",
                "strategy": "credit_put_spread",
                "expiration": "2026-03-20",
                "short_strike": 510,
                "long_strike": 500,
                "dte": 31,
                "rank_score": 80.0,
                "input_snapshot": {
                    "underlying_snapshot": {"price": 520.0},
                    "chain_metadata": {"contracts": 80},
                    "pricing_source": "tradier",
                    "timestamp": "2026-02-17T09:59:30+00:00",
                },
            },
        }
    ]
    with open(ledger_path, "w", encoding="utf-8") as handle:
        for row in ledger_rows:
            handle.write(json.dumps(row) + "\n")

    client = _build_client(tmp_path)
    trade_key = "QQQ|2026-03-20|put_credit_spread|510|500|31"

    response = client.get(f"/api/admin/data-workbench/trade/{trade_key}")
    assert response.status_code == 200

    payload = response.json()
    assert payload["trade_key"] == trade_key
    assert payload["sources"]["where_found"][0] == "trade_ledger"
    assert payload["trade"]["strategy_id"] == "put_credit_spread"
    assert isinstance(payload["trade_json"].get("input_snapshot"), dict)


def test_data_workbench_trade_not_found_returns_404_with_trade_key(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    trade_key = " qqq | 2026-03-20 | put_credit_spread | 510.0 | 500.0 | 31.0 "

    response = client.get(f"/api/admin/data-workbench/trade/{trade_key}")
    assert response.status_code == 404

    payload = response.json()
    assert payload.get("error", {}).get("code") == "DATA_WORKBENCH_TRADE_NOT_FOUND"
    details = payload.get("error", {}).get("details", {})
    assert details.get("original_key") == trade_key.strip()
    assert details.get("normalized_key") == "QQQ|2026-03-20|put_credit_spread|510|500|31"
    assert isinstance(details.get("attempted_keys"), list)
    assert "QQQ|2026-03-20|put_credit_spread|510|500|31" in details.get("attempted_keys", [])


def test_data_workbench_trade_alias_key_resolves_and_emits_noncanonical_warning(tmp_path: Path) -> None:
    report_path = tmp_path / "credit_spread_analysis_20260217_120000.json"
    report_payload = {
        "strategyId": "credit_spread",
        "generated_at": "2026-02-17T12:00:00+00:00",
        "expiration": "2026-03-20",
        "trades": [
            {
                "trade_key": "QQQ|2026-03-20|put_credit_spread|510|500|31",
                "strategy_id": "put_credit_spread",
                "underlying": "QQQ",
                "spread_type": "put_credit_spread",
                "short_strike": 510,
                "long_strike": 500,
                "dte": 31,
                "computed": {"max_profit": 100.0, "max_loss": 500.0, "pop": 0.70},
                "details": {"dte": 31},
                "pills": {"strategy_label": "Put Credit Spread", "dte": 31},
                "input_snapshot": {
                    "underlying_snapshot": {"price": 520.1},
                    "chain_metadata": {"contracts": 100},
                    "pricing_source": "tradier",
                    "timestamp": "2026-02-17T11:59:30+00:00",
                },
            }
        ],
    }
    report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")

    client = _build_client(tmp_path)
    incoming_key = " qqq | 2026-03-20 | put_credit_spread | 510.0 | 500.0 | 31.0 "

    response = client.get(f"/api/admin/data-workbench/trade/{incoming_key}")
    assert response.status_code == 200

    payload = response.json()
    assert payload["trade_key"] == "QQQ|2026-03-20|put_credit_spread|510|500|31"

    events_file = tmp_path / "validation_events.jsonl"
    assert events_file.exists()
    raw_events = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    noncanonical_events = [
        event for event in raw_events if str(event.get("code") or "") == "TRADE_KEY_NON_CANONICAL"
    ]
    assert noncanonical_events


def test_data_workbench_reconstructs_input_snapshot_without_missing_warning(tmp_path: Path) -> None:
    report_path = tmp_path / "credit_spread_analysis_20260217_120001.json"
    report_payload = {
        "strategyId": "credit_spread",
        "generated_at": "2026-02-17T12:01:00+00:00",
        "expiration": "2026-03-20",
        "trades": [
            {
                "trade_key": "QQQ|2026-03-20|put_credit_spread|510|500|31",
                "strategy_id": "put_credit_spread",
                "underlying": "QQQ",
                "spread_type": "put_credit_spread",
                "short_strike": 510,
                "long_strike": 500,
                "dte": 31,
                "net_credit": 1.45,
                "contractsMultiplier": 100,
                "computed": {"max_profit": 145.0, "max_loss": 855.0, "pop": 0.70},
                "details": {"dte": 31},
                "pills": {"strategy_label": "Put Credit Spread"},
            }
        ],
    }
    report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")

    client = _build_client(tmp_path)
    trade_key = "QQQ|2026-03-20|put_credit_spread|510|500|31"

    response = client.get(f"/api/admin/data-workbench/trade/{trade_key}")
    assert response.status_code == 200
    payload = response.json()

    trade_json = payload.get("trade_json") if isinstance(payload.get("trade_json"), dict) else {}
    snapshot = trade_json.get("input_snapshot") if isinstance(trade_json.get("input_snapshot"), dict) else None
    assert isinstance(snapshot, dict)
    assert snapshot.get("pricing_source") == "reconstructed_from_raw_candidate"
    trade_context = snapshot.get("trade_context") if isinstance(snapshot.get("trade_context"), dict) else {}
    assert trade_context.get("short_strike") == 510.0
    assert trade_context.get("long_strike") == 500.0
    assert trade_context.get("net_credit") == 1.45

    warnings = trade_json.get("validation_warnings") if isinstance(trade_json.get("validation_warnings"), list) else []
    assert "DATA_WORKBENCH_INPUT_SNAPSHOT_MISSING" not in warnings

    events_file = tmp_path / "validation_events.jsonl"
    if events_file.exists():
        raw_events = [
            json.loads(line)
            for line in events_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        missing_events = [
            event for event in raw_events if str(event.get("code") or "") == "DATA_WORKBENCH_INPUT_SNAPSHOT_MISSING"
        ]
        assert not missing_events
