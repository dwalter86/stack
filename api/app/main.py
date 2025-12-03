from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import json
from schemas import (
    LoginRequest,
    Token,
    MeOut,
    AccountOut,
    AccountCreate,
    AccountUpdate,
    ItemCreate,
    ItemOut,
    ItemsPage,
    AdminUser,
    CreateAdmin,
    AdminUserUpdate,
    SectionCreate,
    SectionUpdate,
    SectionOut,
    Preferences,
    PreferencesUpdate,
    CommentCreate,
    CommentOut,
    ItemUpdate,
)
from auth import login_and_get_user, create_token, memberships_for_user
from deps import current_user, ip_allowlist, require_admin
import rls
from sqlalchemy import text
from database import SessionLocal

DEFAULT_PREFERENCES: dict[str, str | bool] = {
  "accounts_label": "Home",
  "sections_label": "Sections",
  "items_label": "Items",
  "show_slugs": False,
}

def merge_preferences(raw: dict | None) -> dict:
  merged = dict(DEFAULT_PREFERENCES)
  if isinstance(raw, dict):
    for key, val in raw.items():
      if key not in merged:
        continue
      if isinstance(merged[key], bool):
        merged[key] = bool(val)
      elif isinstance(val, str) and val.strip():
        merged[key] = val.strip()
  return merged

def get_preferences(db, user_id: str) -> dict:
  row = db.execute(text("SELECT ui_labels FROM user_preferences WHERE user_id=:u LIMIT 1"), {"u": user_id}).first()
  return merge_preferences(row[0] if row else None)

def save_preferences(db, user_id: str, labels: dict) -> dict:
  merged = merge_preferences(labels)
  db.execute(text("""
    INSERT INTO user_preferences(user_id, ui_labels)
    VALUES (:u, CAST(:l AS jsonb))
    ON CONFLICT (user_id) DO UPDATE SET ui_labels = EXCLUDED.ui_labels
  """), {"u": user_id, "l": json.dumps(merged)})
  db.commit()
  return merged

def normalize_section_schema(raw: dict | None) -> dict:
  """Accept a flexible schema shape and store it as a fields array.

  Existing clients send `{"fields": [...]}` already, but some callers
  provide an object keyed by field name (e.g. {"name": {"type": ...}}).
  Normalize both inputs so the stored schema always has a `fields` list
  compatible with the UI expectations.
  """
  if not isinstance(raw, dict):
    return {"fields": []}

  raw_fields = raw.get("fields")
  if isinstance(raw_fields, list):
    normalized = []
    for field in raw_fields:
      if isinstance(field, dict) and field.get("key"):
        normalized.append(field)
    return {"fields": normalized}

  normalized_fields = []
  for key, val in raw.items():
    if not isinstance(val, dict):
      continue
    field: dict = {"key": key}
    label = val.get("label") or val.get("friendlyname")
    if label:
      field["label"] = label
    if "type" in val:
      field["type"] = val["type"]
    if "options" in val:
      field["options"] = val["options"]
    if "order" in val:
      field["order"] = val["order"]
    normalized_fields.append(field)

  return {"fields": normalized_fields}

app = FastAPI(title="Multi-tenant JSON API")
app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"]
)

@app.post("/api/login", response_model=Token, dependencies=[Depends(ip_allowlist)])
async def login(payload: LoginRequest):
  uid = login_and_get_user(payload.email, payload.password)
  if not uid:
    raise HTTPException(status_code=401, detail="Invalid credentials")
  return Token(access_token=create_token(uid))

@app.get("/api/me", response_model=MeOut, dependencies=[Depends(ip_allowlist)])
async def me(user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    row = db.execute(text("""
      SELECT id::text,
             email,
             COALESCE(name, ''),
             COALESCE(user_type, CASE WHEN is_admin THEN 'admin' ELSE 'standard' END),
             is_admin
      FROM users
      WHERE id=:u
    """), {"u": user_id}).first()
    if not row:
      raise HTTPException(status_code=404, detail="User not found")
    prefs = get_preferences(db, user_id)
    user_type = row[3] or ("admin" if row[4] else "standard")
    is_admin_flag = user_type in ("admin", "super_admin") or bool(row[4])
    return MeOut(id=row[0], email=row[1], name=row[2], user_type=user_type, is_admin=is_admin_flag, preferences=Preferences(**prefs))

@app.get("/api/me/preferences", response_model=Preferences, dependencies=[Depends(ip_allowlist)])
async def read_preferences(user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    prefs = get_preferences(db, user_id)
    return Preferences(**prefs)

@app.put("/api/me/preferences", response_model=Preferences, dependencies=[Depends(ip_allowlist)])
async def update_preferences(body: PreferencesUpdate, user_id: str = Depends(current_user)):
  updates: dict[str, str | bool] = {}
  for field in ("accounts_label", "sections_label", "items_label"):
    val = getattr(body, field)
    if val is not None:
      cleaned = val.strip()
      if not cleaned:
        raise HTTPException(status_code=400, detail=f"{field.replace('_', ' ').title()} cannot be empty")
      updates[field] = cleaned

  if body.show_slugs is not None:
    updates["show_slugs"] = bool(body.show_slugs)

  with SessionLocal() as db:
    current = get_preferences(db, user_id)
    current.update(updates)
    merged = save_preferences(db, user_id, current)
    return Preferences(**merged)

@app.get("/api/me/accounts", response_model=list[AccountOut], dependencies=[Depends(ip_allowlist)])
async def my_accounts(user_id: str = Depends(current_user)):
  return memberships_for_user(user_id)

@app.post("/api/accounts", response_model=AccountOut, status_code=201, dependencies=[Depends(ip_allowlist)])
async def create_account(body: AccountCreate, user_id: str = Depends(current_user)):
  name = body.name.strip()
  if not name:
    raise HTTPException(status_code=400, detail="Name is required")

  with SessionLocal() as db:
    row = db.execute(
      text("INSERT INTO accounts(name) VALUES (:n) RETURNING id::text, name"),
      {"n": name}
    ).first()
    if not row:
      raise HTTPException(status_code=500, detail="Failed to create account")

    account_id = row[0]
    schema_name = f"tenant_{account_id.replace('-', '')}"

    db.execute(
      text("""
        INSERT INTO memberships(user_id, account_id, role)
        VALUES (:u, :a, 'owner')
        ON CONFLICT (user_id, account_id) DO NOTHING
      """),
      {"u": user_id, "a": account_id}
    )

    schema_sql = f"""
      DO $$
      DECLARE sch text := '{schema_name}';
      BEGIN
        EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', sch);
        EXECUTE format('CREATE TABLE IF NOT EXISTS %I.items (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          section_slug TEXT NOT NULL DEFAULT ''default'',
          name TEXT NOT NULL,
          data JSONB NOT NULL DEFAULT ''{{}}'',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )', sch);
        EXECUTE format('ALTER TABLE %I.items ADD COLUMN IF NOT EXISTS section_slug TEXT', sch);
        EXECUTE format('UPDATE %I.items SET section_slug = ''default'' WHERE section_slug IS NULL', sch);
        EXECUTE format('ALTER TABLE %I.items ALTER COLUMN section_slug SET DEFAULT ''default''', sch);
        EXECUTE format('ALTER TABLE %I.items ALTER COLUMN section_slug SET NOT NULL', sch);
        EXECUTE format('ALTER TABLE %I.items ENABLE ROW LEVEL SECURITY', sch);
        IF NOT EXISTS (
          SELECT 1 FROM pg_policies
          WHERE schemaname = sch AND tablename = 'items' AND policyname = 'items_tenant_policy'
        ) THEN
          EXECUTE format(
            'CREATE POLICY items_tenant_policy ON %I.items
             USING ( current_setting(''app.current_account'')::uuid = ''{account_id}'' )
             WITH CHECK ( current_setting(''app.current_account'')::uuid = ''{account_id}'' )',
            sch);
        END IF;

        EXECUTE format('CREATE TABLE IF NOT EXISTS %I.comments (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          item_id UUID NOT NULL REFERENCES %I.items(id) ON DELETE CASCADE,
          user_id UUID,
          user_name TEXT,
          comment TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )', sch, sch);
        EXECUTE format('ALTER TABLE %I.comments ENABLE ROW LEVEL SECURITY', sch);
        IF NOT EXISTS (
          SELECT 1 FROM pg_policies
          WHERE schemaname = sch AND tablename = 'comments' AND policyname = 'comments_tenant_policy'
        ) THEN
          EXECUTE format('CREATE POLICY comments_tenant_policy ON %I.comments USING (true)', sch);
        END IF;

      END $$;
    """
    db.execute(text(schema_sql))
    db.commit()
    return AccountOut(id=row[0], name=row[1])

# --- Account management ---

@app.put("/api/accounts/{account_id}", response_model=AccountOut, dependencies=[Depends(ip_allowlist)])
async def update_account(account_id: str, body: AccountUpdate, user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    row = db.execute(
      text("UPDATE accounts SET name=:n WHERE id=:a RETURNING id::text, name"),
      {"n": body.name, "a": account_id}
    ).first()
    if not row:
      raise HTTPException(status_code=404, detail="Account not found")
    db.commit()
    return AccountOut(id=row[0], name=row[1])

@app.delete("/api/accounts/{account_id}", dependencies=[Depends(ip_allowlist)])
async def delete_account(account_id: str, user_id: str = Depends(current_user)):
  schema_name = f"tenant_{account_id.replace('-', '')}"
  with SessionLocal() as db:
    db.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
    db.execute(text("DELETE FROM memberships WHERE account_id=:a"), {"a": account_id})
    db.execute(text("DELETE FROM sections WHERE account_id=:a"), {"a": account_id})
    result = db.execute(text("DELETE FROM accounts WHERE id=:a"), {"a": account_id})
    db.commit()
    if result.rowcount == 0:
      raise HTTPException(status_code=404, detail="Account not found")
  return {"ok": True}

# --- Sections API ---

@app.get("/api/accounts/{account_id}/sections", response_model=list[SectionOut], dependencies=[Depends(ip_allowlist)])
async def list_sections(account_id: str, user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    rows = db.execute(text("""
      SELECT id::text, slug, label, COALESCE(schema, '{}'::jsonb)
      FROM sections
      WHERE account_id = :a
      ORDER BY created_at
    """), {"a": account_id}).all()
    return [SectionOut(id=r[0], slug=r[1], label=r[2], schema=normalize_section_schema(r[3])) for r in rows]

@app.post("/api/accounts/{account_id}/sections", response_model=SectionOut, dependencies=[Depends(ip_allowlist)])
async def create_section(account_id: str, body: SectionCreate, user_id: str = Depends(current_user)):
  payload = json.dumps(normalize_section_schema(body.schema))
  with SessionLocal() as db:
    row = db.execute(text("""
      INSERT INTO sections(account_id, slug, label, schema)
      VALUES (:a, :slug, :label, CAST(:schema AS jsonb))
      ON CONFLICT (account_id, slug) DO UPDATE
        SET label = EXCLUDED.label,
            schema = EXCLUDED.schema
      RETURNING id::text, slug, label, COALESCE(schema, '{}'::jsonb)
    """), {"a": account_id, "slug": body.slug, "label": body.label, "schema": payload}).first()
    db.commit()
    return SectionOut(id=row[0], slug=row[1], label=row[2], schema=normalize_section_schema(row[3]))

@app.get("/api/accounts/{account_id}/sections/{slug}", response_model=SectionOut, dependencies=[Depends(ip_allowlist)])
async def get_section(account_id: str, slug: str, user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    row = db.execute(text("""
      SELECT id::text, slug, label, COALESCE(schema, '{}'::jsonb)
      FROM sections
      WHERE account_id = :a AND slug = :s
      LIMIT 1
    """), {"a": account_id, "s": slug}).first()
    if not row:
      raise HTTPException(status_code=404, detail="Section not found")
    return SectionOut(id=row[0], slug=row[1], label=row[2], schema=normalize_section_schema(row[3]))

@app.put("/api/accounts/{account_id}/sections/{slug}", response_model=SectionOut, dependencies=[Depends(ip_allowlist)])
async def update_section(account_id: str, slug: str, body: SectionUpdate, user_id: str = Depends(current_user)):
  payload = json.dumps(normalize_section_schema(body.schema))
  with SessionLocal() as db:
    row = db.execute(text("""
      UPDATE sections
      SET label = :label,
          schema = CAST(:schema AS jsonb)
      WHERE account_id = :a AND slug = :s
      RETURNING id::text, slug, label, COALESCE(schema, '{}'::jsonb)
    """), {"a": account_id, "s": slug, "label": body.label, "schema": payload}).first()
    if not row:
      raise HTTPException(status_code=404, detail="Section not found")
    db.commit()
    return SectionOut(id=row[0], slug=row[1], label=row[2], schema=normalize_section_schema(row[3]))

@app.delete("/api/accounts/{account_id}/sections/{slug}", dependencies=[Depends(ip_allowlist)])
async def delete_section(account_id: str, slug: str, user_id: str = Depends(current_user)):
  schema_name = f"tenant_{account_id.replace('-', '')}"
  with SessionLocal() as db:
    # Ensure RLS context and delete items in this section for that account
    db.execute(rls.set_current_account(account_id))
    db.execute(text(f"DELETE FROM {schema_name}.items WHERE section_slug = :slug"), {"slug": slug})
    res = db.execute(text("DELETE FROM sections WHERE account_id = :a AND slug = :s"), {"a": account_id, "s": slug})
    db.commit()
    if res.rowcount == 0:
      raise HTTPException(status_code=404, detail="Section not found")
  return {"ok": True}

# --- Items API (default section + per-section) ---

@app.get("/api/accounts/{account_id}/items", response_model=ItemsPage, dependencies=[Depends(ip_allowlist)])
async def list_items_default(account_id: str, limit: int = Query(50, ge=1, le=200), cursor: Optional[str] = None, user_id: str = Depends(current_user)):
  items = rls.list_items(account_id, section="default", limit=limit, cursor=cursor)
  next_cursor = items[-1]["id"] if items and len(items) == limit else None
  return ItemsPage(items=items, next=next_cursor)

@app.post("/api/accounts/{account_id}/items", response_model=ItemOut, dependencies=[Depends(ip_allowlist)])
async def create_item_default(account_id: str, body: ItemCreate, user_id: str = Depends(current_user)):
  return rls.create_item(account_id, section="default", name=body.name, data=body.data)

@app.get("/api/accounts/{account_id}/items/{item_id}", response_model=ItemOut, dependencies=[Depends(ip_allowlist)])
async def get_item(account_id: str, item_id: str, user_id: str = Depends(current_user)):
  item = rls.get_item(account_id, item_id)
  if not item:
    raise HTTPException(status_code=404, detail="Item not found")
  return ItemOut(id=item["id"], name=item["name"], data=item["data"], created_at=item["created_at"])

@app.put("/api/accounts/{account_id}/items/{item_id}", response_model=ItemOut, dependencies=[Depends(ip_allowlist)])
async def update_item(account_id: str, item_id: str, body: ItemUpdate, user_id: str = Depends(current_user)):
  if body.name is None and body.data is None:
    raise HTTPException(status_code=400, detail="At least one field must be provided for update")

  updated = rls.update_item(account_id, item_id, name=body.name, data=body.data)
  if not updated:
    raise HTTPException(status_code=404, detail="Item not found")
  return updated

@app.delete("/api/accounts/{account_id}/items/{item_id}", dependencies=[Depends(ip_allowlist)])
async def delete_item(account_id: str, item_id: str, user_id: str = Depends(current_user)):
  rls.delete_item(account_id, item_id)
  return {"ok": True}

@app.get("/api/accounts/{account_id}/sections/{slug}/items", response_model=ItemsPage, dependencies=[Depends(ip_allowlist)])
async def list_section_items(account_id: str, slug: str, limit: int = Query(50, ge=1, le=200), cursor: Optional[str] = None, user_id: str = Depends(current_user)):
  items = rls.list_items(account_id, section=slug, limit=limit, cursor=cursor)
  next_cursor = items[-1]["id"] if items and len(items) == limit else None
  return ItemsPage(items=items, next=next_cursor)

@app.post("/api/accounts/{account_id}/sections/{slug}/items", response_model=ItemOut, dependencies=[Depends(ip_allowlist)])
async def create_section_item(account_id: str, slug: str, body: ItemCreate, user_id: str = Depends(current_user)):
  return rls.create_item(account_id, section=slug, name=body.name, data=body.data)

# --- Comments API ---

@app.get("/api/accounts/{account_id}/items/{item_id}/comments", response_model=list[CommentOut], dependencies=[Depends(ip_allowlist)])
async def list_item_comments(account_id: str, item_id: str, user_id: str = Depends(current_user)):
  return rls.list_comments(account_id, item_id)

@app.post("/api/accounts/{account_id}/items/{item_id}/comments", response_model=CommentOut, status_code=201, dependencies=[Depends(ip_allowlist)])
async def create_item_comment(account_id: str, item_id: str, body: CommentCreate, user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    user_row = db.execute(text("SELECT COALESCE(name, email) FROM users WHERE id = :u"), {"u": user_id}).first()
    if not user_row:
      raise HTTPException(status_code=403, detail="User not found")
    default_user_name = user_row[0]

  user_name = None
  if body.user_name is not None:
    user_name = body.user_name.strip()
  if not user_name:
    user_name = default_user_name

  comment = body.comment.strip()
  if not comment:
    raise HTTPException(status_code=400, detail="Comment cannot be empty")
  return rls.create_comment(account_id, item_id, user_id, user_name, comment)

# --- Admin API ---

@app.get("/api/admin/users", response_model=list[AdminUser], dependencies=[Depends(ip_allowlist)])
async def list_admin_users(admin_ctx = Depends(require_admin)):
  with SessionLocal() as db:
    rows = db.execute(text("""
      SELECT id::text,
             email,
             COALESCE(name, ''),
             COALESCE(user_type, CASE WHEN is_admin THEN 'admin' ELSE 'standard' END),
             is_active
      FROM users
      ORDER BY created_at DESC
    """)).all()
    include_prefs = admin_ctx.get("user_type") == "super_admin"
    result: list[AdminUser] = []
    for r in rows:
      prefs = get_preferences(db, r[0]) if include_prefs else None
      result.append(AdminUser(id=r[0], email=r[1], name=r[2], user_type=r[3], is_active=r[4], preferences=Preferences(**prefs) if prefs else None))
    return result

@app.get("/api/admin/all-accounts", response_model=list[AccountOut], dependencies=[Depends(ip_allowlist), Depends(require_admin)])
async def list_all_accounts():
  with SessionLocal() as db:
    rows = db.execute(text("SELECT id::text, name FROM accounts ORDER BY created_at DESC")).all()
    return [{"id": r[0], "name": r[1]} for r in rows]

@app.post("/api/admin/users", response_model=AdminUser, status_code=201, dependencies=[Depends(ip_allowlist)])
async def create_admin(body: CreateAdmin, admin_ctx = Depends(require_admin)):
  requester_type = admin_ctx.get("user_type", "standard")
  if body.user_type == "super_admin" and requester_type != "super_admin":
    raise HTTPException(status_code=403, detail="Only super admins can create super admins")

  is_admin_flag = body.user_type in ("admin", "super_admin")

  with SessionLocal() as db:
    row = db.execute(text("SELECT id FROM users WHERE email=:e"), {"e": body.email}).first()
    if row:
      raise HTTPException(status_code=409, detail="Email already exists")
    row = db.execute(
      text("""
        INSERT INTO users(email, name, user_type, password_hash, is_admin, is_active)
        VALUES (:e, :n, :t, crypt(:p, gen_salt('bf', 12)), :is_admin, TRUE)
        RETURNING id::text, email, name, user_type, is_active
      """),
      {"e": body.email, "n": body.name.strip(), "t": body.user_type, "p": body.password, "is_admin": is_admin_flag}
    ).first()
    new_id = row[0]
    if body.accounts:
      ids = list({a for a in body.accounts})
      db.execute(
        text("INSERT INTO memberships(user_id, account_id, role) SELECT :u, a.id, 'owner' FROM accounts a WHERE a.id = ANY(:ids) ON CONFLICT DO NOTHING"),
        {"u": new_id, "ids": ids},
      )
    # Inherit creator customisation settings by default
    try:
      creator_prefs = get_preferences(db, admin_ctx.get("id"))
      save_preferences(db, new_id, creator_prefs)
    except Exception:
      pass
    db.commit()
    prefs = get_preferences(db, new_id) if requester_type == "super_admin" else None
    return AdminUser(id=row[0], email=row[1], name=row[2], user_type=row[3], is_active=row[4], preferences=Preferences(**prefs) if prefs else None)

@app.put("/api/admin/users/{user_id}", response_model=AdminUser, dependencies=[Depends(ip_allowlist)])
async def update_user(user_id: str, body: AdminUserUpdate, admin_ctx=Depends(require_admin)):
    requester_type = admin_ctx.get("user_type", "standard")
    if body.user_type == "super_admin" and requester_type != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can assign super admin role")

    with SessionLocal() as db:
        target_user = db.execute(text("SELECT user_type FROM users WHERE id=:id"), {"id": user_id}).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        if target_user[0] == "super_admin" and requester_type != "super_admin":
            raise HTTPException(status_code=403, detail="Only super admins can edit other super admins")

        updates = []
        params = {"id": user_id}

        if body.name is not None:
            updates.append("name = :name")
            params["name"] = body.name.strip()
        if body.user_type is not None:
            updates.append("user_type = :user_type")
            params["user_type"] = body.user_type
            updates.append("is_admin = :is_admin")
            params["is_admin"] = body.user_type in ("admin", "super_admin")
        if body.is_active is not None:
            updates.append("is_active = :is_active")
            params["is_active"] = body.is_active

        if updates:
            db.execute(
                text(f"UPDATE users SET {', '.join(updates)} WHERE id = :id"),
                params
            )

        if body.accounts is not None:
            db.execute(text("DELETE FROM memberships WHERE user_id = :id"), {"id": user_id})
            if body.accounts:
                ids = list(set(body.accounts))
                db.execute(
                    text("INSERT INTO memberships(user_id, account_id, role) SELECT :u, a.id, 'owner' FROM accounts a WHERE a.id = ANY(:ids::uuid[]) ON CONFLICT DO NOTHING"),
                    {"u": user_id, "ids": ids}
                )

        row = db.execute(text("SELECT id::text, email, name, user_type, is_active FROM users WHERE id=:id"), {"id": user_id}).first()
        db.commit()
        prefs = get_preferences(db, user_id) if requester_type == "super_admin" else None
        return AdminUser(id=row[0], email=row[1], name=row[2], user_type=row[3], is_active=row[4], preferences=Preferences(**prefs) if prefs else None)

@app.delete("/api/admin/users/{user_id}", status_code=204, dependencies=[Depends(ip_allowlist)])
async def delete_user(user_id: str, admin_ctx=Depends(require_admin)):
    requester_id = admin_ctx.get("id")
    if user_id == requester_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own user account.")

    with SessionLocal() as db:
        target_user = db.execute(text("SELECT user_type FROM users WHERE id=:id"), {"id": user_id}).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        if target_user[0] == "super_admin" and admin_ctx.get("user_type") != "super_admin":
            raise HTTPException(status_code=403, detail="Only super admins can delete other super admins")

        db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
        db.commit()
    return None