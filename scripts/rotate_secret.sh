#!/usr/bin/env bash
set -euo pipefail
FILE="$(dirname "$0")/../.env"
sed -i "s/^JWT_SECRET=.*/JWT_SECRET=$(openssl rand -hex 32)/" "$FILE"
docker compose up -d --force-recreate api caddy
echo "Rotated JWT secret; existing tokens are now invalid."
