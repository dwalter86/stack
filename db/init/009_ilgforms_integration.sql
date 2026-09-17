-- ILG Forms integration (replaces the n8n "Add Incident" workflow).
-- One integration per ILG Forms company; a company may push into several
-- Stack accounts, and only the accounts listed here are accepted.
CREATE TABLE IF NOT EXISTS ilgforms_integrations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  company_id INT NOT NULL UNIQUE,
  integration_key TEXT NOT NULL,
  main_datasource TEXT NOT NULL DEFAULT 'nfmain',
  item_id_column TEXT NOT NULL DEFAULT 'itemId',
  section_schema JSONB,
  enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ilgforms_integration_accounts (
  integration_id UUID NOT NULL REFERENCES ilgforms_integrations(id) ON DELETE CASCADE,
  account_id UUID NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
  PRIMARY KEY (integration_id, account_id)
);

-- Sync log: one row per thing received from, sent to, or updated with ILG Forms.
-- A "received" submission is the parent; each location it touched is a child.
-- payload holds the raw inbound body (customer PII) and is pruned after 30 days.
CREATE TABLE IF NOT EXISTS ilgforms_sync_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  parent_id UUID REFERENCES ilgforms_sync_log(id) ON DELETE CASCADE,
  integration_id UUID REFERENCES ilgforms_integrations(id) ON DELETE SET NULL,
  account_id UUID,
  direction TEXT NOT NULL CHECK (direction IN ('received', 'sent', 'updated')),
  event TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('success', 'pending', 'retrying', 'failed', 'skipped')),
  section_slug TEXT,
  section_label TEXT,
  item_id UUID,
  row_id TEXT,
  entry_id TEXT,
  summary TEXT,
  error TEXT,
  payload JSONB
);

CREATE INDEX IF NOT EXISTS idx_ilgforms_sync_log_created_at ON ilgforms_sync_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ilgforms_sync_log_parent ON ilgforms_sync_log (parent_id);
CREATE INDEX IF NOT EXISTS idx_ilgforms_sync_log_account ON ilgforms_sync_log (account_id);
CREATE INDEX IF NOT EXISTS idx_ilgforms_sync_log_result ON ilgforms_sync_log (result);
CREATE INDEX IF NOT EXISTS idx_ilgforms_sync_log_item ON ilgforms_sync_log (item_id);

-- Inbound de-duplication: an identical body delivered twice is processed once.
CREATE TABLE IF NOT EXISTS ilgforms_inbound (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  integration_id UUID NOT NULL REFERENCES ilgforms_integrations(id) ON DELETE CASCADE,
  entry_id TEXT,
  body_hash TEXT NOT NULL,
  log_id UUID REFERENCES ilgforms_sync_log(id) ON DELETE SET NULL,
  UNIQUE (integration_id, body_hash)
);

-- Outbound job queue: writes to ILG Forms datasources, retried with backoff.
CREATE TABLE IF NOT EXISTS ilgforms_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  integration_id UUID NOT NULL REFERENCES ilgforms_integrations(id) ON DELETE CASCADE,
  account_id UUID NOT NULL,
  kind TEXT NOT NULL,
  item_id UUID,
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed')),
  attempts INT NOT NULL DEFAULT 0,
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_error TEXT,
  log_id UUID REFERENCES ilgforms_sync_log(id) ON DELETE SET NULL,
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ilgforms_jobs_due ON ilgforms_jobs (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_ilgforms_jobs_item ON ilgforms_jobs (item_id);

-- Column in the main datasource that holds each row's id (the RowId used for updates).
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS row_id_column TEXT NOT NULL DEFAULT 'ID';
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS account_id_column TEXT NOT NULL DEFAULT 'accountId';
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS last_reconciled_at TIMESTAMPTZ;

-- Orphans: datasource rows whose itemId matches nothing in Stack. Listed for a
-- human to look at; never cleared or recreated automatically. Rebuilt by each
-- reconcile pass.
CREATE TABLE IF NOT EXISTS ilgforms_orphans (
  integration_id UUID NOT NULL REFERENCES ilgforms_integrations(id) ON DELETE CASCADE,
  row_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  account_id UUID,
  detail JSONB,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (integration_id, row_id)
);

-- Three datasources per integration: incidents (one row per section), properties
-- (main_datasource, one row per location) and devices (one row per appliance).
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS incident_datasource TEXT NOT NULL DEFAULT 'nflList';
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS device_datasource TEXT NOT NULL DEFAULT 'deviceDB';
-- Engineer name -> email, written to the device row's eEmail column.
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS engineer_emails JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Incident (section) <-> incident datasource row.
CREATE TABLE IF NOT EXISTS ilgforms_section_links (
  account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  section_slug TEXT NOT NULL,
  datasource TEXT NOT NULL,
  row_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  last_checked_at TIMESTAMPTZ,
  last_synced_at TIMESTAMPTZ,
  PRIMARY KEY (account_id, section_slug, datasource)
);

-- Orphans are now tracked per datasource.
ALTER TABLE ilgforms_orphans ADD COLUMN IF NOT EXISTS datasource TEXT NOT NULL DEFAULT 'nfmain';
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint c
    WHERE c.conname = 'ilgforms_orphans_pkey' AND array_length(c.conkey, 1) = 2
  ) THEN
    ALTER TABLE ilgforms_orphans DROP CONSTRAINT ilgforms_orphans_pkey;
    ALTER TABLE ilgforms_orphans ADD PRIMARY KEY (integration_id, datasource, row_id);
  END IF;
END $$;

ALTER TABLE ilgforms_jobs ADD COLUMN IF NOT EXISTS section_slug TEXT;
ALTER TABLE ilgforms_jobs ADD COLUMN IF NOT EXISTS datasource TEXT;
CREATE INDEX IF NOT EXISTS idx_ilgforms_jobs_section ON ilgforms_jobs (account_id, section_slug);
