#!/bin/bash
set -e
BASE="/Users/alexanderanthony/Backend Services/apis/ProfBetGeng_Claud001"
PYTHON="$BASE/venv/bin/python3.12"
cd "$BASE"
exec "$PYTHON" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
