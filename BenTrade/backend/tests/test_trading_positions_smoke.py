import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import create_app  # noqa: E402


class _StubTradierClient:
    async def get_positions(self):
        return {
            "positions": {
                "position": []
            }
        }

    async def get_orders(self, status=None):
        return {
            "orders": {
                "order": []
            }
        }

    async def get_quotes(self, symbols):
        return {}


# Stub response for request_json — returns empty positions/orders
async def _stub_request_json(client, method, url, **kwargs):
    if "positions" in url:
        return {"positions": {"position": []}}
    if "orders" in url:
        return {"orders": {"order": []}}
    return {}


class TradingPositionsSmokeTests(unittest.TestCase):
    def test_positions_returns_200_with_ok_false_when_credentials_missing(self):
        app = create_app()
        app.state.trading_service.settings = SimpleNamespace(
            TRADIER_TOKEN="",
            TRADIER_ACCOUNT_ID="",
            TRADIER_ENV="sandbox",
        )

        with TestClient(app) as client:
            response = client.get("/api/trading/positions")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("ok"), False)
        self.assertEqual(payload.get("positions"), [])
        self.assertIsInstance(payload.get("error"), dict)

    @patch("app.api.routes_active_trades.request_json", new=_stub_request_json)
    def test_positions_returns_200_with_ok_true_when_credentials_present(self):
        app = create_app()
        app.state.trading_service.settings = SimpleNamespace(
            TRADIER_TOKEN="token",
            TRADIER_ACCOUNT_ID="account",
            TRADIER_ENV="sandbox",
        )
        app.state.tradier_client = _StubTradierClient()

        with TestClient(app) as client:
            response = client.get("/api/trading/positions")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertIsInstance(payload.get("positions"), list)
        self.assertIn(payload.get("account_mode"), ("live", None))


if __name__ == "__main__":
    unittest.main()
