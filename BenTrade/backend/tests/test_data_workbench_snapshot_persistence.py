from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.data_workbench_service import DataWorkbenchService
from app.services.strategy_service import StrategyService
from app.services.strategies.base import StrategyPlugin


class _FakeBaseDataService:
    tradier_client = None

    @staticmethod
    def get_source_health_snapshot() -> dict:
        return {"tradier": {"status": "green", "message": "ok"}}

    async def get_analysis_inputs(self, symbol: str, expiration: str, include_prices_history: bool = True) -> dict[str, Any]:
        return {
            "underlying_price": 500.0,
            "contracts": [{"mock": True}],
            "prices_history": [498.0, 499.0, 500.0],
            "vix": 18.5,
        }


class _RiskStub:
    @staticmethod
    def get_policy() -> dict:
        return {}


class _TestStrategy(StrategyPlugin):
    id = "test_strategy"

    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        symbol = str(inputs.get("symbol") or "SPY").upper()
        expiration = str(inputs.get("expiration") or "2026-03-20")
        return [{"symbol": symbol, "expiration": expiration, "dte": 7}]

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        row = candidates[0]
        return [
            {
                "underlying": row["symbol"],
                "symbol": row["symbol"],
                "expiration": row["expiration"],
                "dte": 7,
                "spread_type": "credit_put_spread",
                "strategy": "credit_put_spread",
                "short_strike": 495,
                "long_strike": 490,
                "max_profit": 120.0,
                "max_loss": 380.0,
                "p_win_used": 0.66,
                "return_on_risk": 0.315,
                "ev_per_contract": 15.0,
                "rank_score": 82.0,
                "open_interest": 1000,
                "volume": 300,
            }
        ]

    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        return True, []

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        return 82.0, {"edge": 0.2, "pop": 0.66, "liq": 0.8}


@pytest.mark.anyio
async def test_scanner_generation_persists_snapshot_for_data_workbench(tmp_path: Path) -> None:
    strategy_service = StrategyService(
        base_data_service=_FakeBaseDataService(),
        results_dir=tmp_path,
        risk_policy_service=_RiskStub(),
        signal_service=None,
        regime_service=None,
    )
    strategy_service.register(_TestStrategy())

    generated = await strategy_service.generate(
        strategy_id="test_strategy",
        request_payload={
            "symbol": "SPY",
            "expiration": "2026-03-20",
        },
    )

    trades = generated.get("trades") or []
    assert trades
    trade_key = str(trades[0].get("trade_key") or "").strip()
    assert trade_key

    workbench = DataWorkbenchService(results_dir=tmp_path)
    resolved = workbench.resolve_trade_with_trace(trade_key).get("record")

    assert resolved is not None
    trade_json = resolved.get("trade_json") if isinstance(resolved.get("trade_json"), dict) else {}
    assert isinstance(trade_json.get("input_snapshot"), dict)

    report_file = tmp_path / str(generated.get("filename") or "")
    assert report_file.exists()
    report_payload = json.loads(report_file.read_text(encoding="utf-8"))
    report_trades = report_payload.get("trades") if isinstance(report_payload, dict) else []
    assert isinstance(report_trades, list) and report_trades
    assert isinstance(report_trades[0].get("input_snapshot"), dict)

    records_file = tmp_path / "data_workbench_records.jsonl"
    assert records_file.exists()
    record_rows = [
        json.loads(line)
        for line in records_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert record_rows
    matched = [row for row in record_rows if str(row.get("trade_key") or "") == trade_key]
    assert matched
    assert isinstance(matched[-1].get("input_snapshot"), dict)
