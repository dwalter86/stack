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
  where = "WHERE section_slug = :section"
  params: dict = {"limit": limit, "section": section}
  if cursor:
    where += " AND id > :cursor"
    params["cursor"] = cursor
  sql = f"""
  SELECT id::text, name, COALESCE(data, '{{}}'::jsonb), created_at
  FROM {schema}.items
  {where}
  ORDER BY id
  LIMIT :limit
  """
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
    rows = db.execute(text(sql), params).all()
    return [{"id": r[0], "name": r[1], "data": r[2], "created_at": r[3]} for r in rows]

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
  sets = []
  params: dict = {"id": item_id}
  if name is not None:
    sets.append("name = :n")
    params["n"] = name
  if data is not None:
    sets.append("data = CAST(:d AS jsonb)")
    params["d"] = json.dumps(data)
  if not sets:
    return None
  sql = f"""
  UPDATE {schema}.items
  SET {', '.join(sets)}
  WHERE id = :id
  RETURNING id::text, name, data, created_at
  """
  with SessionLocal() as db:
    db.execute(set_current_account(account_id))
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
