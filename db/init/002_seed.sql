DO $$
DECLARE
  u_id uuid;
  a_id uuid;
  sch  text;
  pol_exists boolean;
BEGIN
  SELECT id INTO u_id FROM users WHERE email='admin@admin.co';
  IF u_id IS NULL THEN
    INSERT INTO users(email, name, user_type, password_hash, is_admin)
    VALUES ('admin@admin.co', 'Super Admin', 'super_admin', crypt('password', gen_salt('bf', 12)), TRUE)
    RETURNING id INTO u_id;
  ELSE
    UPDATE users
    SET is_admin=TRUE,
        user_type='super_admin',
        name=COALESCE(NULLIF(name, ''), 'Super Admin')
    WHERE id=u_id;
  END IF;

  SELECT id INTO a_id FROM accounts WHERE name='Default company';
  IF a_id IS NULL THEN
    INSERT INTO accounts(name) VALUES ('Default company') RETURNING id INTO a_id;
  END IF;

  INSERT INTO memberships(user_id, account_id, role)
  VALUES (u_id, a_id, 'owner')
  ON CONFLICT (user_id, account_id) DO NOTHING;

  sch := 'tenant_' || replace(a_id::text, '-', '');
  EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', sch);

  -- Ensure items table exists with section support.
  EXECUTE format('CREATE TABLE IF NOT EXISTS %I.items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_slug TEXT NOT NULL DEFAULT ''default'',
    name TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT ''{}'',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )', sch);

  -- For existing tables, backfill and enforce section_slug.
  EXECUTE format('ALTER TABLE %I.items ADD COLUMN IF NOT EXISTS section_slug TEXT', sch);
  EXECUTE format('UPDATE %I.items SET section_slug = ''default'' WHERE section_slug IS NULL', sch);
  EXECUTE format('ALTER TABLE %I.items ALTER COLUMN section_slug SET DEFAULT ''default''', sch);
  EXECUTE format('ALTER TABLE %I.items ALTER COLUMN section_slug SET NOT NULL', sch);

  EXECUTE format('ALTER TABLE %I.items ENABLE ROW LEVEL SECURITY', sch);

  SELECT EXISTS(
    SELECT 1 FROM pg_policies
    WHERE schemaname = sch AND tablename = 'items' AND policyname = 'items_tenant_policy'
  ) INTO pol_exists;

  IF NOT pol_exists THEN
    EXECUTE format(
      'CREATE POLICY items_tenant_policy ON %I.items
       USING ( current_setting(''app.current_account'')::uuid = %L )
       WITH CHECK ( current_setting(''app.current_account'')::uuid = %L )',
      sch, a_id::text, a_id::text
    );
  END IF;
END$$;
