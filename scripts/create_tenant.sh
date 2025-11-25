#!/usr/bin/env bash
set -euo pipefail
ACC_NAME=${1:-}
if [[ -z "$ACC_NAME" ]]; then echo "Usage: $0 <ACCOUNT_NAME>"; exit 1; fi
source "$(dirname "$0")/../.env"
PSQL="docker compose exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1"

ACC_ID=$($PSQL -t -A -c "INSERT INTO accounts(name) VALUES ($$${ACC_NAME}$$) RETURNING id;")
ACC_ID=$(echo "$ACC_ID" | tr -d '[:space:]')

$PSQL -c "DO $$
DECLARE sch text := 'tenant_' || replace('$ACC_ID','-','');
BEGIN
  EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', sch);
  -- Ensure items table exists with section support.
  EXECUTE format('CREATE TABLE IF NOT EXISTS %I.items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_slug TEXT NOT NULL DEFAULT ''default'',
    name TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT ''{}'',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )', sch);
  EXECUTE format('ALTER TABLE %I.items ADD COLUMN IF NOT EXISTS section_slug TEXT', sch);
  EXECUTE format('UPDATE %I.items SET section_slug = ''default'' WHERE section_slug IS NULL', sch);
  EXECUTE format('ALTER TABLE %I.items ALTER COLUMN section_slug SET DEFAULT ''default''', sch);
  EXECUTE format('ALTER TABLE %I.items ALTER COLUMN section_slug SET NOT NULL', sch);
  EXECUTE format('ALTER TABLE %I.items ENABLE ROW LEVEL SECURITY', sch);
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname=sch AND tablename='items' AND policyname='items_tenant_policy') THEN
    EXECUTE format('CREATE POLICY items_tenant_policy ON %I.items
      USING ( current_setting(''app.current_account'')::uuid = ''$ACC_ID'' )
      WITH CHECK ( current_setting(''app.current_account'')::uuid = ''$ACC_ID'' )', sch);
  END IF;
END $$;"
printf "Created account '%s' (%s) with schema tenant_%s\n" "$ACC_NAME" "$ACC_ID" "${ACC_ID//-/}"
