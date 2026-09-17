import json
from sqlalchemy import text
from database import SessionLocal

def set_current_account(account_id: str):
  # DB function accepts TEXT, so we bind as plain text
  return text("SELECT set_current_account(:a)").bindparams(a=account_id)

def _schema_name(account_id: str) -> str:
  return f"tenant_{account_id.replace('-', '')}"

def list_items(account_id: str, section: str, limit: int = 50, cursor: str | None = None):
  schema = _schema_name(account_id)
  where = "WHERE i.section_slug = :section"
  params: dict = {"limit": limit, "section": section}
  if cursor:
    where += " AND i.id > :cursor"
    params["cursor"] = cursor
  sql = f"""
  SELECT
    i.id::text,
    i.name,
    COALESCE(i.data, '{{}}'::jsonb) AS data,
    i.created_at,
    COALESCE((
      SELECT COUNT(*)::int
      FROM {schema}.comments c
      WHERE c.item_id = i.id
    ), 0) AS comment_count
  FROM {schema}.items AS i
  {where}
  ORDER BY i.id
  LIMIT :limit
  """
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    rows = db.execute(text(sql), params).all()
    return [
      {"id": r[0], "name": r[1], "data": r[2], "created_at": r[3], "comment_count": r[4]}
      for r in rows
    ]

def create_item(account_id: str, section: str, name: str, data: dict):
  schema = _schema_name(account_id)
  sql = f"""
  INSERT INTO {schema}.items (section_slug, name, data)
  VALUES (:s, :n, CAST(:d AS jsonb))
  RETURNING id::text, name, data, created_at
  """
  payload = json.dumps(data or {})
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    row = db.execute(text(sql), {"s": section, "n": name, "d": payload}).first()
    db.commit()
    return {"id": row[0], "name": row[1], "data": row[2], "created_at": row[3]}

def update_item(account_id: str, item_id: str, name: str | None, data: dict | None):
  schema = _schema_name(account_id)
  params: dict = {"id": item_id}
  sets = []

  with SessionLocal() as db:
    db.execute(set_current_account(account_id))

    if data is not None:
      current_row = db.execute(text(f"SELECT COALESCE(data, '{{}}'::jsonb) FROM {schema}.items WHERE id = :id"), params).first()
      if not current_row:
        return None
      current_data = current_row[0] if isinstance(current_row[0], dict) else {}
      merged_data = dict(current_data)
      merged_data.update(data)
      sets.append("data = CAST(:d AS jsonb)")
      params["d"] = json.dumps(merged_data)

    if name is not None:
      sets.append("name = :n")
      params["n"] = name

    if not sets:
      return None

    sql = f"""
    UPDATE {schema}.items
    SET {', '.join(sets)}
    WHERE id = :id
    RETURNING id::text, name, data, created_at
    """

    row = db.execute(text(sql), params).first()
    db.commit()
    if not row:
      return None
    return {"id": row[0], "name": row[1], "data": row[2], "created_at": row[3]}

def delete_item(account_id: str, item_id: str):
  schema = _schema_name(account_id)
  sql = f"DELETE FROM {schema}.items WHERE id = :id"
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    db.execute(text(sql), {"id": item_id})
    db.commit()

def list_comments(account_id: str, item_id: str):
  schema = _schema_name(account_id)
  sql = f"""
  SELECT id::text, item_id::text, user_name, comment, created_at
  FROM {schema}.comments
  WHERE item_id = :item_id
  ORDER BY created_at ASC
  """
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    rows = db.execute(text(sql), {"item_id": item_id}).all()
    return [dict(r._mapping) for r in rows]

def create_comment(account_id: str, item_id: str, user_id: str, user_name: str, comment: str):
  schema = _schema_name(account_id)
  sql = f"""
  INSERT INTO {schema}.comments (item_id, user_id, user_name, comment)
  VALUES (:item_id, :user_id, :user_name, :comment)
  RETURNING id::text, item_id::text, user_name, comment, created_at
  """
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    params = {"item_id": item_id, "user_id": user_id, "user_name": user_name, "comment": comment}
    row = db.execute(text(sql), params).first()
    db.commit()
    return dict(row._mapping)

def ensure_section_notes_table(account_id: str):
  schema = _schema_name(account_id)
  sql = f"""
  CREATE TABLE IF NOT EXISTS {schema}.section_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_slug TEXT NOT NULL,
    user_id UUID,
    user_name TEXT,
    note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )
  """
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    db.execute(text(sql))
    db.commit()

ITEM_SYNC_TABLE_SQL = """
  CREATE TABLE IF NOT EXISTS {schema}.item_sync (
    item_id UUID NOT NULL REFERENCES {schema}.items(id) ON DELETE CASCADE,
    datasource TEXT NOT NULL,
    row_id TEXT,
    status TEXT NOT NULL DEFAULT 'not_synced',
    last_checked_at TIMESTAMPTZ,
    last_synced_at TIMESTAMPTZ,
    last_error TEXT,
    PRIMARY KEY (item_id, datasource)
  )
"""

_item_sync_ready: set[str] = set()

def ensure_item_sync_table(account_id: str):
  """ILG Forms sync state per item and datasource (an item can be linked to a
  property row and to an appliance row). Kept out of items.data so it never
  leaks into exports or gets overwritten by an edit."""
  schema = _schema_name(account_id)
  if schema in _item_sync_ready:
    return
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    db.execute(text(ITEM_SYNC_TABLE_SQL.format(schema=schema)))
    # Early builds keyed the table on item_id alone: widen it in place.
    pk_cols = db.execute(text("""
      SELECT count(*) FROM information_schema.key_column_usage
      WHERE table_schema = :s AND table_name = 'item_sync' AND constraint_name = 'item_sync_pkey'
    """), {"s": schema}).scalar()
    if pk_cols == 1:
      db.execute(text(f"UPDATE {schema}.item_sync SET datasource = 'nfmain' WHERE datasource IS NULL"))
      db.execute(text(f"ALTER TABLE {schema}.item_sync ALTER COLUMN datasource SET NOT NULL"))
      db.execute(text(f"ALTER TABLE {schema}.item_sync DROP CONSTRAINT item_sync_pkey"))
      db.execute(text(f"ALTER TABLE {schema}.item_sync ADD PRIMARY KEY (item_id, datasource)"))
    db.execute(text(f"ALTER TABLE {schema}.item_sync ENABLE ROW LEVEL SECURITY"))
    exists = db.execute(text("""
      SELECT 1 FROM pg_policies
      WHERE schemaname = :s AND tablename = 'item_sync' AND policyname = 'item_sync_tenant_policy'
    """), {"s": schema}).first()
    if not exists:
      db.execute(text(f"CREATE POLICY item_sync_tenant_policy ON {schema}.item_sync USING (true)"))
    db.commit()
  _item_sync_ready.add(schema)

def list_section_notes(account_id: str, section_slug: str):
  schema = _schema_name(account_id)
  sql = f"""
  SELECT id::text, section_slug, user_name, note, created_at
  FROM {schema}.section_notes
  WHERE section_slug = :section_slug
  ORDER BY created_at ASC
  """
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    rows = db.execute(text(sql), {"section_slug": section_slug}).all()
    return [dict(r._mapping) for r in rows]

def create_section_note(account_id: str, section_slug: str, user_id: str, user_name: str, note: str):
  schema = _schema_name(account_id)
  sql = f"""
  INSERT INTO {schema}.section_notes (section_slug, user_id, user_name, note)
  VALUES (:section_slug, :user_id, :user_name, :note)
  RETURNING id::text, section_slug, user_name, note, created_at
  """
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    params = {"section_slug": section_slug, "user_id": user_id, "user_name": user_name, "note": note}
    row = db.execute(text(sql), params).first()
    db.commit()
    return dict(row._mapping)

def get_item(account_id: str, item_id: str):
  schema = _schema_name(account_id)
  sql = f"""
  SELECT id::text, name, COALESCE(data, '{{}}'::jsonb), section_slug, created_at
  FROM {schema}.items
  WHERE id = :id
  LIMIT 1
  """
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    row = db.execute(text(sql), {"id": item_id}).first()
    if not row:
      return None
    return {"id": row[0], "name": row[1], "data": row[2], "section_slug": row[3], "created_at": row[4]}