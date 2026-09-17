#!/usr/bin/env bash
# One harmless live write to a single ILG Forms datasource row (rewrites its current itemId).
#   ILGFORMS_KEY=<integration key> scripts/ilgforms_live_test.sh <row id>
set -euo pipefail
if [ "$#" -ne 1 ] || [ -z "${ILGFORMS_KEY:-}" ]; then
  echo "usage: ILGFORMS_KEY=<integration key> $0 <row id>" >&2; exit 1
fi
cd "$(dirname "$0")/.."
docker compose cp scripts/ilgforms_live_test.py api:/tmp/ilgforms_live_test.py >/dev/null
trap 'docker compose exec -T api rm -f /tmp/ilgforms_live_test.py' EXIT
ILG_ROW_ID="$1" docker compose exec -T -e ILGFORMS_KEY -e ILG_ROW_ID -e ILGFORMS_OUTBOUND=on -e PYTHONPATH=/app -w /app api python /tmp/ilgforms_live_test.py
