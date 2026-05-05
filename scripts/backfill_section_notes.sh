#!/usr/bin/env bash
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

    EXECUTE format('CREATE TABLE IF NOT EXISTS %I.section_notes (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      section_slug TEXT NOT NULL,
      user_id UUID,
      user_name TEXT,
      note TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )', sch);

    EXECUTE format('ALTER TABLE %I.section_notes ENABLE ROW LEVEL SECURITY', sch);

    IF NOT EXISTS (
      SELECT 1
      FROM pg_policies
      WHERE schemaname = sch
        AND tablename = 'section_notes'
        AND policyname = 'section_notes_tenant_policy'
    ) THEN
      EXECUTE format(
        'CREATE POLICY section_notes_tenant_policy ON %I.section_notes USING (true)',
        sch
      );
    END IF;
  END LOOP;
END \$\$;"

echo "Backfill complete: ensured section_notes exists for all tenant schemas."
