-- Engineers come from an ILG Forms datasource (id, department, name, email1, email2, Reason).
-- The reconcile pass copies it here; it feeds the Engineer dropdown on every incident of the
-- integration's accounts and the engineer email written to the device sheet.
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS engineer_datasource TEXT NOT NULL DEFAULT 'engineers';
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS engineers JSONB NOT NULL DEFAULT '[]'::jsonb;
