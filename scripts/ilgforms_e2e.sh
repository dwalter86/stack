#!/usr/bin/env bash
# End-to-end run of the ILG Forms integration on the LOCAL stack against an in-memory fake of
# ILG Forms (nothing is sent to the real one). Needs the stack up: docker compose up -d --build
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
BASE="${API_BASE:-http://localhost}"
TOKEN=$(curl -s -X POST "$BASE/api/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
docker compose exec -T api rm -rf /tests
docker compose cp api/tests api:/tests >/dev/null
trap 'docker compose exec -T api rm -rf /tests' EXIT
docker compose exec -T -e TOKEN="$TOKEN" -e PYTHONPATH=/app -w /app api python /tests/e2e_ilgforms.py
