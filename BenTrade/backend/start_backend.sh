#!/usr/bin/env bash
# start_backend.sh - POSIX-compatible script for starting the backend
# Usage: cd backend; ./start_backend.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
  echo "Virtualenv not found. Creating venv..."
  python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "Upgrading pip and installing requirements (may take a while)..."
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "Preflight: verifying /api/stock/scan route is registered..."
python -c "from fastapi.testclient import TestClient; from app.main import create_app; import sys; c=TestClient(create_app()); paths=(c.get('/openapi.json').json() or {}).get('paths', {}); ok='/api/stock/scan' in paths; print(f'ROUTE_CHECK /api/stock/scan -> {ok}'); sys.exit(0 if ok else 3)"
if [ $? -ne 0 ]; then
  echo "Preflight failed: /api/stock/scan is not registered. Aborting backend startup." >&2
  exit 1
fi

echo "Starting FastAPI app (uvicorn app.main:app) on port 5000..."
python -m uvicorn app.main:app --host 127.0.0.1 --port 5000
