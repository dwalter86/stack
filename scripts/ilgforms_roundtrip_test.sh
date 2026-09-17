#!/usr/bin/env bash
# Live insert / update / delete round trip on a ZZTEST row in each ILG Forms datasource.
#   ILGFORMS_KEY=<integration key> scripts/ilgforms_roundtrip_test.sh
set -euo pipefail
if [ -z "${ILGFORMS_KEY:-}" ]; then echo "usage: ILGFORMS_KEY=<integration key> $0" >&2; exit 1; fi
cd "$(dirname "$0")/.."
docker compose cp scripts/ilgforms_roundtrip_test.py api:/tmp/ilgforms_roundtrip_test.py >/dev/null
trap 'docker compose exec -T api rm -f /tmp/ilgforms_roundtrip_test.py' EXIT
docker compose exec -T -e ILGFORMS_KEY -e PYTHONPATH=/app -w /app api python /tmp/ilgforms_roundtrip_test.py
