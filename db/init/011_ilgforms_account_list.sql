-- ILG Forms account list: a pick list (Answer Value = account id, Display Text = name) that the
-- forms use to choose which account an incident belongs to. Only accounts linked to the
-- integration belong in it: other customers' accounts must never appear there.
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS account_datasource TEXT NOT NULL DEFAULT 'accountList';
-- Create a platform account for a row that appears in the list with an id the platform does not
-- have. OFF by default, and it must stay off on any server that shares its ILG Forms company with
-- another (staging + production read the same list and would copy each other's accounts).
ALTER TABLE ilgforms_integrations ADD COLUMN IF NOT EXISTS accept_new_accounts BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE ilgforms_integration_accounts ADD COLUMN IF NOT EXISTS list_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE ilgforms_integration_accounts ADD COLUMN IF NOT EXISTS list_checked_at TIMESTAMPTZ;
