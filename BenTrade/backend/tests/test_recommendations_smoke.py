import unittest
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.recommendation_service import RecommendationService


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import create_app  # noqa: E402


class _StubStrategyService:
    def __init__(self, *, reports_by_strategy=None, report_payloads=None, report_errors=None):
        self._reports_by_strategy = reports_by_strategy or {}
        self._report_payloads = report_payloads or {}
        self._report_errors = report_errors or {}

    def list_strategy_ids(self):
        return list(self._reports_by_strategy.keys())

    def list_reports(self, strategy_id):
        return list(self._reports_by_strategy.get(strategy_id, []))

    def get_report(self, strategy_id, filename):
        key = (strategy_id, filename)
        if key in self._report_errors:
            raise self._report_errors[key]
        return self._report_payloads.get(key, {"trades": []})


class _StubStockAnalysisService:
    def __init__(self, scanner_payload=None, scanner_error=None):
        self._scanner_payload = scanner_payload
        self._scanner_error = scanner_error

    async def stock_scanner(self, max_candidates=15):
        if self._scanner_error:
            raise self._scanner_error
        return self._scanner_payload or {"candidates": []}


class _StubRegimeService:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    async def get_regime(self):
        if self._error:
            raise self._error
        return self._payload or {"regime_label": "NEUTRAL", "regime_score": 50.0, "suggested_playbook": {}}


def _build_client(*, strategy_service, scanner_service, regime_service) -> TestClient:
    app = create_app()
    app.state.recommendation_service = RecommendationService(
        strategy_service=strategy_service,
        stock_analysis_service=scanner_service,
        regime_service=regime_service,
    )
    return TestClient(app)


class RecommendationSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_recommendations_selector_handles_no_files_malformed_and_scanner(self):
        scenarios = [
            {
                "name": "no_files",
                "strategy": _StubStrategyService(reports_by_strategy={"credit_spread": []}),
                "scanner": _StubStockAnalysisService(scanner_payload={"candidates": []}),
                "expect_non_empty": False,
            },
            {
                "name": "malformed_analysis",
                "strategy": _StubStrategyService(
                    reports_by_strategy={"credit_spread": ["analysis_bad.json"]},
                    report_errors={("credit_spread", "analysis_bad.json"): ValueError("invalid json")},
                ),
                "scanner": _StubStockAnalysisService(scanner_payload={"candidates": []}),
                "expect_non_empty": False,
            },
            {
                "name": "scanner_fallback",
                "strategy": _StubStrategyService(reports_by_strategy={"credit_spread": []}),
                "scanner": _StubStockAnalysisService(
                    scanner_payload={
                        "candidates": [
                            {
                                "symbol": "SPY",
                                "composite_score": 0.86,
                                "price": 600.12,
                                "signals": {"rsi_14": 55.0, "iv_rv_ratio": 1.12},
                            }
                        ]
                    }
                ),
                "expect_non_empty": True,
            },
        ]

        for scenario in scenarios:
            service = RecommendationService(
                strategy_service=scenario["strategy"],
                stock_analysis_service=scenario["scanner"],
                regime_service=_StubRegimeService(),
            )

            payload = await service.get_top_recommendations(limit=3)

            self.assertIsInstance(payload, dict, msg=scenario["name"])
            self.assertIn("picks", payload, msg=scenario["name"])
            self.assertIn("notes", payload, msg=scenario["name"])
            self.assertIsInstance(payload["picks"], list, msg=scenario["name"])
            self.assertIsInstance(payload["notes"], list, msg=scenario["name"])

            if scenario["expect_non_empty"]:
                self.assertGreater(len(payload["picks"]), 0, msg=scenario["name"])


class RecommendationEndpointSmokeTests(unittest.TestCase):
    def test_recommendations_endpoint_returns_200_for_smoke_scenarios(self):
        scenarios = [
            {
                "name": "no_files",
                "strategy": _StubStrategyService(reports_by_strategy={"credit_spread": []}),
                "scanner": _StubStockAnalysisService(scanner_payload={"candidates": []}),
            },
            {
                "name": "malformed_analysis",
                "strategy": _StubStrategyService(
                    reports_by_strategy={"credit_spread": ["analysis_bad.json"]},
                    report_errors={("credit_spread", "analysis_bad.json"): ValueError("invalid json")},
                ),
                "scanner": _StubStockAnalysisService(scanner_payload={"candidates": []}),
            },
            {
                "name": "scanner_fallback",
                "strategy": _StubStrategyService(reports_by_strategy={"credit_spread": []}),
                "scanner": _StubStockAnalysisService(
                    scanner_payload={
                        "candidates": [
                            {
                                "symbol": "SPY",
                                "composite_score": 0.86,
                                "price": 600.12,
                                "signals": {"rsi_14": 55.0, "iv_rv_ratio": 1.12},
                            }
                        ]
                    }
                ),
            },
        ]

        for scenario in scenarios:
            client = _build_client(
                strategy_service=scenario["strategy"],
                scanner_service=scenario["scanner"],
                regime_service=_StubRegimeService(),
            )
            response = client.get("/api/recommendations/top")
            self.assertEqual(response.status_code, 200, msg=scenario["name"])
            payload = response.json()
            self.assertIn("picks", payload, msg=scenario["name"])
            self.assertIn("notes", payload, msg=scenario["name"])
            self.assertIsInstance(payload.get("picks"), list, msg=scenario["name"])
            self.assertIsInstance(payload.get("notes"), list, msg=scenario["name"])


if __name__ == "__main__":
    unittest.main()
