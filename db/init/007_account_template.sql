CREATE TABLE IF NOT EXISTS account_template (
  account_id UUID PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  content BYTEA NOT NULL,
  uploaded_by UUID REFERENCES users(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
