ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS user_type TEXT NOT NULL DEFAULT 'standard';

UPDATE users
SET user_type = CASE
  WHEN is_admin THEN 'super_admin'
  ELSE 'standard'
END
WHERE COALESCE(user_type, '') = '';
