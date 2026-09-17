#!/usr/bin/env bash
# Ensure every existing tenant schema has the item_sync table (ILG Forms sync
# state per item). New accounts get it from create_account in main.py.
set -euo pipefail

source "$(dirname "$0")/../.env"
PSQL="docker compose exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1"

$PSQL -c "DO \$\$
DECLARE
  acc RECORD;
  sch text;
BEGIN
  FOR acc IN SELECT id::text AS account_id FROM accounts LOOP
    sch := 'tenant_' || replace(acc.account_id, '-', '');
    IF NOT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = sch) THEN
      CONTINUE;
    END IF;

    EXECUTE format('CREATE TABLE IF NOT EXISTS %I.item_sync (
      item_id UUID NOT NULL REFERENCES %I.items(id) ON DELETE CASCADE,
      datasource TEXT NOT NULL,
      row_id TEXT,
      status TEXT NOT NULL DEFAULT ''not_synced'',
      last_checked_at TIMESTAMPTZ,
      last_synced_at TIMESTAMPTZ,
      last_error TEXT,
      PRIMARY KEY (item_id, datasource)
    )', sch, sch);

    EXECUTE format('ALTER TABLE %I.item_sync ENABLE ROW LEVEL SECURITY', sch);

    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = sch AND tablename = 'item_sync' AND policyname = 'item_sync_tenant_policy'
    ) THEN
      EXECUTE format('CREATE POLICY item_sync_tenant_policy ON %I.item_sync USING (true)', sch);
    END IF;
  END LOOP;
END \$\$;"

echo "Backfill complete: ensured item_sync exists for all tenant schemas."
