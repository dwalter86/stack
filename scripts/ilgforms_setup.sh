#!/usr/bin/env bash
# Create or update an ILG Forms integration and the accounts it may write to.
# The integration key is a secret: pass it via the environment, never commit it.
#
#   ILGFORMS_KEY=xxxx scripts/ilgforms_setup.sh "Graphic Electronics" 50255 <account-id> [<account-id> ...]
#
# Optional: ILGFORMS_ENGINEERS='{"Engineer Name":"engineer@example.com"}' sets the engineer -> email map
# written to the device datasource's eEmail column (personal addresses: keep them out of git too).
set -euo pipefail

if [ "$#" -lt 3 ] || [ -z "${ILGFORMS_KEY:-}" ]; then
  echo "usage: ILGFORMS_KEY=<integration key> $0 <name> <company id> <account id> [<account id> ...]" >&2
  exit 1
fi

source "$(dirname "$0")/../.env"
NAME="$1"; COMPANY="$2"; shift 2
PSQL="docker compose exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1 -At"

INTEGRATION_ID=$($PSQL -v name="$NAME" -v company="$COMPANY" -v key="$ILGFORMS_KEY" <<'SQL'
INSERT INTO ilgforms_integrations (name, company_id, integration_key)
VALUES (:'name', :'company'::int, :'key')
ON CONFLICT (company_id) DO UPDATE SET name = EXCLUDED.name, integration_key = EXCLUDED.integration_key
RETURNING id;
SQL
)
INTEGRATION_ID=$(echo "$INTEGRATION_ID" | head -n1)

if [ -n "${ILGFORMS_ENGINEERS:-}" ]; then
  $PSQL -v integration="$INTEGRATION_ID" -v engineers="$ILGFORMS_ENGINEERS" <<'SQL' >/dev/null
UPDATE ilgforms_integrations SET engineer_emails = :'engineers'::jsonb WHERE id = :'integration'::uuid;
SQL
  echo "Engineer email map saved"
fi

for ACCOUNT in "$@"; do
  $PSQL -v integration="$INTEGRATION_ID" -v account="$ACCOUNT" <<'SQL' >/dev/null
INSERT INTO ilgforms_integration_accounts (integration_id, account_id)
VALUES (:'integration'::uuid, :'account'::uuid)
ON CONFLICT (account_id) DO UPDATE SET integration_id = EXCLUDED.integration_id;
SQL
  echo "Linked account $ACCOUNT"
done

echo "Integration $INTEGRATION_ID ready for company $COMPANY."
echo "Callback URLs and payloads: Settings > ILG Forms callbacks"
