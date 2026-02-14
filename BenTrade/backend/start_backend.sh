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

echo "Starting FastAPI app (uvicorn app.main:app) on port 5000..."
python -m uvicorn app.main:app --host 127.0.0.1 --port 5000
