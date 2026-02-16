from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import create_app  # noqa: E402


class _DummyStockService:
    async def get_summary(self, symbol: str, range_key: str = "6mo") -> dict:
        return {
            "symbol": symbol,
            "as_of": "2026-02-15T00:00:00Z",
            "price": {"last": 100.0, "change": 1.0},
            "history": [{"idx": 0, "close": 99.0}, {"idx": 1, "close": 100.0}],
            "indicators": {"rsi14": 55.0},
            "options_context": {"iv": 0.2},
            "notes": [],
            "source_health": {},
        }

    async def scan_universe(self, universe: str = "default") -> dict:
        return {
            "as_of": "2026-02-15T00:00:00Z",
            "universe": universe,
            "results": [
                {
                    "symbol": "SPY",
                    "scanner_score": 4.2,
                    "signals": {
                        "trend": "up",
                        "rsi_14": 56.0,
                        "rv_20d": 0.18,
                        "iv": 0.24,
                        "iv_rv_ratio": 1.33,
                    },
                    "reasons": ["liquidity bonus", "IV rich"],
                }
            ],
            "notes": [],
            "source_health": {},
        }


class _DummySpreadService:
    async def analyze_spreads(self, _payload) -> list[dict]:
        return [
            {
                "underlying": "SPY",
                "dte": 7,
                "short_strike": 580.0,
                "long_strike": 575.0,
                "net_credit": 1.0,
                "return_on_risk": 0.2,
                "p_win_used": 0.7,
                "ev_per_share": 0.1,
            }
        ]


class _DummyBaseDataService:
    def get_source_health_snapshot(self) -> dict:
        return {"tradier": {"status": "green", "message": "ok"}}


class _DummyRiskPolicyService:
    def get_policy(self) -> dict:
        return {"portfolio_size": 100000.0}

    async def build_snapshot(self, _request) -> dict:
        return {
            "as_of": "2026-02-15T00:00:00Z",
            "policy": {"portfolio_size": 100000.0},
            "exposure": {
                "open_trades": 0,
                "total_risk_used": 0.0,
                "risk_remaining": 0.0,
                "risk_by_underlying": [],
                "trades": [],
                "warnings": {"hard_limits": [], "soft_gates": []},
            },
        }


def _build_client() -> TestClient:
    from app.api import routes_active_trades

    app = create_app()
    app.state.stock_analysis_service = _DummyStockService()
    app.state.spread_service = _DummySpreadService()
    app.state.base_data_service = _DummyBaseDataService()
    app.state.risk_policy_service = _DummyRiskPolicyService()
    
    async def _dummy_active_payload(_request) -> dict:
        return {
            "as_of": "2026-02-15T00:00:00Z",
            "source": "stub",
            "positions": [],
            "orders": [],
            "active_trades": [],
            "source_health": {},
        }

    routes_active_trades._build_active_payload = _dummy_active_payload
    return TestClient(app)


def test_stock_scan_route_returns_200_and_results() -> None:
    client = _build_client()
    response = client.get("/api/stock/scan?universe=default")
    assert response.status_code == 200
    payload = response.json()
    assert "results" in payload
    assert isinstance(payload["results"], list)
    assert payload["results"]


def test_route_presence_and_top_level_shapes() -> None:
    client = _build_client()

    r_active = client.get("/api/trading/active")
    assert r_active.status_code == 200
    assert "active_trades" in r_active.json()

    r_workbench_analyze = client.post(
        "/api/workbench/analyze",
        json={
            "symbol": "SPY",
            "expiration": "2026-03-20",
            "strategy": "credit_put_spread",
            "short_strike": 580,
            "long_strike": 575,
            "contractsMultiplier": 1,
        },
    )
    assert r_workbench_analyze.status_code == 200
    assert "trade" in r_workbench_analyze.json()

    r_workbench_scenarios = client.get("/api/workbench/scenarios")
    assert r_workbench_scenarios.status_code == 200
    assert "scenarios" in r_workbench_scenarios.json()

    r_summary = client.get("/api/stock/summary?symbol=SPY&range=6mo")
    assert r_summary.status_code == 200
    assert "price" in r_summary.json()

    r_scan = client.get("/api/stock/scan?universe=default")
    assert r_scan.status_code == 200
    assert "results" in r_scan.json()

    r_policy = client.get("/api/risk/policy")
    assert r_policy.status_code == 200
    assert "policy" in r_policy.json()

    r_snapshot = client.get("/api/risk/snapshot")
    assert r_snapshot.status_code == 200
    assert "exposure" in r_snapshot.json()
