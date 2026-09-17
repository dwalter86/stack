from fastapi import FastAPI, Depends, HTTPException, Query, Request, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from typing import Optional
from datetime import datetime, timezone
import json
import urllib.request
import urllib.error
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
    SectionNoteCreate,
    SectionNoteOut,
    ItemUpdate,
    TemplateInfo,
    ExportRequest,
)
from auth import login_and_get_user, create_token, memberships_for_user
from deps import current_user, ip_allowlist, require_admin, require_editor, require_super_admin
import rls
import exports
import audit
import ilgforms_sync
import ilgforms_jobs
import ilgforms_admin
import ilgforms_outbound
import ilgforms_devices
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
  def normalize_status_summary(val: object) -> dict:
    if not isinstance(val, dict):
      return {
        "enabled": False,
        "field_key": "",
        "red_values": [],
        "yellow_values": [],
        "green_values": [],
        "blue_values": [],
        "red_label": "",
        "yellow_label": "",
        "green_label": "",
        "blue_label": "",
      }
    field_key = str(val.get("field_key") or "").strip()

    def clean_values(key: str) -> list[str]:
      raw_values = val.get(key)
      if not isinstance(raw_values, list):
        return []
      cleaned: list[str] = []
      seen: set[str] = set()
      for item in raw_values:
        text = str(item).strip()
        if not text:
          continue
        dedupe_key = text.lower()
        if dedupe_key in seen:
          continue
        seen.add(dedupe_key)
        cleaned.append(text)
      return cleaned

    red_values = clean_values("red_values")
    yellow_values = clean_values("yellow_values")
    green_values = clean_values("green_values")
    blue_values = clean_values("blue_values")
    enabled = bool(val.get("enabled")) and bool(field_key) and bool(red_values or yellow_values or green_values or blue_values)
    return {
      "enabled": enabled,
      "field_key": field_key,
      "red_values": red_values,
      "yellow_values": yellow_values,
      "green_values": green_values,
      "blue_values": blue_values,
      "red_label": str(val.get("red_label") or "").strip(),
      "yellow_label": str(val.get("yellow_label") or "").strip(),
      "green_label": str(val.get("green_label") or "").strip(),
      "blue_label": str(val.get("blue_label") or "").strip(),
    }

  if not isinstance(raw, dict):
    return {
      "fields": [],
      "status_summary": normalize_status_summary(None),
    }

  raw_fields = raw.get("fields")
  if isinstance(raw_fields, list):
    normalized = []
    for field in raw_fields:
      if isinstance(field, dict) and field.get("key"):
        normalized.append(field)
    return {
      "fields": normalized,
      "status_summary": normalize_status_summary(raw.get("status_summary")),
    }

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

  return {
    "fields": normalized_fields,
    "status_summary": normalize_status_summary(raw.get("status_summary")),
  }


WEBHOOK_ITEM_UPDATED = "https://n8n.adigi8.app/webhook/af693448-f43f-493c-97af-c46064dba8ba"
WEB_UI_UPDATE_SOURCE_HEADER = "X-Update-Source"
WEB_UI_UPDATE_SOURCE_VALUE = "web-ui"

def from_web_ui(request: Request) -> bool:
  """Changes made in the web platform carry this header. Only those are pushed to
  ILG Forms: calls made by other API clients (n8n during the changeover, scripts)
  are not, so a form that already wrote its own datasource row is never doubled."""
  return request.headers.get(WEB_UI_UPDATE_SOURCE_HEADER) == WEB_UI_UPDATE_SOURCE_VALUE


def push_to_ilgforms(fn, *args):
  """Queue an ILG Forms change. Best-effort: it must never fail the user's request."""
  try:
    fn(*args)
  except Exception:
    import traceback
    traceback.print_exc()


app = FastAPI(title="Multi-tenant JSON API")
app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"]
)
app.add_middleware(audit.AuditMiddleware)

@app.on_event("startup")
def start_background_workers():
  ilgforms_jobs.start_worker()

@app.get("/api/admin/audit-log", dependencies=[Depends(ip_allowlist)])
async def read_audit_log(
  limit: int = 50,
  offset: int = 0,
  user_email: str | None = None,
  action: str | None = None,
  account_id: str | None = None,
  date_from: str | None = None,
  date_to: str | None = None,
  search: str | None = None,
  _admin: dict = Depends(require_super_admin),
):
  limit = max(1, min(limit, 200))
  offset = max(0, offset)
  return audit.query_audit_log(
    limit=limit, offset=offset, user_email=user_email, action=action,
    account_id=account_id, date_from=date_from, date_to=date_to, search=search,
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

@app.put("/api/me/preferences", response_model=Preferences, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
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

@app.post("/api/accounts", response_model=AccountOut, status_code=201, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
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

    db.execute(
      text("""
        INSERT INTO memberships(user_id, account_id, role)
        VALUES (:u, :a, 'owner')
        ON CONFLICT (user_id, account_id) DO NOTHING
      """),
      {"u": user_id, "a": account_id}
    )

    rls.create_tenant_schema(db, account_id)
    db.commit()
    return AccountOut(id=row[0], name=row[1])

# --- Account management ---

@app.put("/api/accounts/{account_id}", response_model=AccountOut, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def update_account(account_id: str, body: AccountUpdate, request: Request, user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    row = db.execute(
      text("UPDATE accounts SET name=:n WHERE id=:a RETURNING id::text, name"),
      {"n": body.name, "a": account_id}
    ).first()
    if not row:
      raise HTTPException(status_code=404, detail="Account not found")
    db.commit()
  if from_web_ui(request):
    push_to_ilgforms(ilgforms_outbound.account_renamed, account_id)   # keeps the forms' account list in step
  return AccountOut(id=row[0], name=row[1])

@app.delete("/api/accounts/{account_id}", dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def delete_account(account_id: str, request: Request, user_id: str = Depends(current_user)):
  schema_name = f"tenant_{account_id.replace('-', '')}"
  if from_web_ui(request):
    # Before the delete: the integration link (which says which list to clean) goes with the account.
    push_to_ilgforms(ilgforms_outbound.account_removed, account_id)
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
      SELECT id::text, slug, label, COALESCE(detail, ''), COALESCE(schema, '{}'::jsonb), COALESCE(address, '')
      FROM sections
      WHERE account_id = :a
      ORDER BY created_at
    """), {"a": account_id}).all()
    engineers = ilgforms_sync.engineer_names(account_id)
    return [SectionOut(id=r[0], slug=r[1], label=r[2], detail=r[3], address=r[5],
                       schema=ilgforms_sync.with_engineer_options(normalize_section_schema(r[4]), engineers)) for r in rows]

@app.post("/api/accounts/{account_id}/sections", response_model=SectionOut, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def create_section(account_id: str, body: SectionCreate, request: Request, user_id: str = Depends(current_user)):
  payload = json.dumps(normalize_section_schema(body.schema))
  with SessionLocal() as db:
    row = db.execute(text("""
      INSERT INTO sections(account_id, slug, label, detail, schema, address)
      VALUES (:a, :slug, :label, :detail, CAST(:schema AS jsonb), :address)
      ON CONFLICT (account_id, slug) DO UPDATE
        SET label = EXCLUDED.label,
            detail = EXCLUDED.detail,
            schema = EXCLUDED.schema,
            address = EXCLUDED.address
      RETURNING id::text, slug, label, COALESCE(detail, ''), COALESCE(schema, '{}'::jsonb), COALESCE(address, '')
    """), {"a": account_id, "slug": body.slug, "label": body.label, "detail": body.detail, "schema": payload, "address": body.address}).first()
    db.commit()
  if from_web_ui(request):
    # Adds the incident to ILG Forms and applies the standard incident layout if none was given.
    push_to_ilgforms(ilgforms_outbound.section_created, account_id, body.slug)
    with SessionLocal() as db:
      fresh = db.execute(text("""
        SELECT id::text, slug, label, COALESCE(detail, ''), COALESCE(schema, '{}'::jsonb), COALESCE(address, '')
        FROM sections WHERE account_id = :a AND slug = :s
      """), {"a": account_id, "s": body.slug}).first()
      row = fresh or row
  return SectionOut(id=row[0], slug=row[1], label=row[2], detail=row[3], address=row[5],
                    schema=ilgforms_sync.with_engineer_options(normalize_section_schema(row[4]), ilgforms_sync.engineer_names(account_id)))

@app.get("/api/accounts/{account_id}/sections/{slug}", response_model=SectionOut, dependencies=[Depends(ip_allowlist)])
async def get_section(account_id: str, slug: str, user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    row = db.execute(text("""
      SELECT id::text, slug, label, COALESCE(detail, ''), COALESCE(schema, '{}'::jsonb), COALESCE(address, '')
      FROM sections
      WHERE account_id = :a AND slug = :s
      LIMIT 1
    """), {"a": account_id, "s": slug}).first()
    if not row:
      raise HTTPException(status_code=404, detail="Section not found")
    return SectionOut(id=row[0], slug=row[1], label=row[2], detail=row[3], address=row[5],
                    schema=ilgforms_sync.with_engineer_options(normalize_section_schema(row[4]), ilgforms_sync.engineer_names(account_id)))

@app.put("/api/accounts/{account_id}/sections/{slug}", response_model=SectionOut, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def update_section(account_id: str, slug: str, body: SectionUpdate, request: Request, user_id: str = Depends(current_user)):
  payload = json.dumps(normalize_section_schema(body.schema))
  with SessionLocal() as db:
    row = db.execute(text("""
      UPDATE sections
      SET label = :label,
          detail = COALESCE(:detail, detail),
          address = COALESCE(:address, address),
          schema = CAST(:schema AS jsonb)
      WHERE account_id = :a AND slug = :s
      RETURNING id::text, slug, label, COALESCE(detail, ''), COALESCE(schema, '{}'::jsonb), COALESCE(address, '')
    """), {"a": account_id, "s": slug, "label": body.label, "detail": body.detail, "address": body.address, "schema": payload}).first()
    if not row:
      raise HTTPException(status_code=404, detail="Section not found")
    db.commit()
  if from_web_ui(request):
    push_to_ilgforms(ilgforms_outbound.section_updated, account_id, slug)
  return SectionOut(id=row[0], slug=row[1], label=row[2], detail=row[3], address=row[5],
                    schema=ilgforms_sync.with_engineer_options(normalize_section_schema(row[4]), ilgforms_sync.engineer_names(account_id)))

@app.delete("/api/accounts/{account_id}/sections/{slug}", dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def delete_section(account_id: str, slug: str, request: Request, user_id: str = Depends(current_user)):
  schema_name = f"tenant_{account_id.replace('-', '')}"
  if from_web_ui(request):
    # Must run before the rows go: it reads the item links to know which ILG Forms rows to remove.
    push_to_ilgforms(ilgforms_outbound.section_deleting, account_id, slug)
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

@app.post("/api/accounts/{account_id}/items", response_model=ItemOut, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def create_item_default(account_id: str, body: ItemCreate, request: Request, user_id: str = Depends(current_user)):
  item = rls.create_item(account_id, section="default", name=body.name, data=body.data)
  if from_web_ui(request):
    push_to_ilgforms(ilgforms_outbound.item_created, account_id, "default", item)
  return item

@app.get("/api/accounts/{account_id}/items/{item_id}", response_model=ItemOut, dependencies=[Depends(ip_allowlist)])
async def get_item(account_id: str, item_id: str, user_id: str = Depends(current_user)):
  item = rls.get_item(account_id, item_id)
  if not item:
    raise HTTPException(status_code=404, detail="Item not found")
  return ItemOut(id=item["id"], name=item["name"], data=item["data"], created_at=item["created_at"])

@app.put("/api/accounts/{account_id}/items/{item_id}", response_model=ItemOut, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def update_item(account_id: str, item_id: str, request: Request, body: ItemUpdate, user_id: str = Depends(current_user)):
  if body.name is None and body.data is None:
    raise HTTPException(status_code=400, detail="At least one field must be provided for update")

  updated = rls.update_item(account_id, item_id, name=body.name, data=body.data)
  if not updated:
    raise HTTPException(status_code=404, detail="Item not found")
  should_fire_webhook = from_web_ui(request)
  if should_fire_webhook and ilgforms_sync.account_has_integration(account_id):
    # Native ILG Forms sync replaces the n8n webhook for accounts that have an integration.
    full = rls.get_item(account_id, item_id) or {}
    push_to_ilgforms(ilgforms_outbound.item_updated, account_id,
                     {"id": item_id, "name": updated["name"], "data": updated["data"], "section_slug": full.get("section_slug")})
    should_fire_webhook = False
  if should_fire_webhook:
    # Fire-and-forget style webhook notification using stdlib; failures should not affect the main response
    try:
      # Ensure the item payload is JSON-serializable (e.g. convert datetimes)
      item_payload = dict(updated)
      created_at_val = item_payload.get("created_at")
      if hasattr(created_at_val, "isoformat"):
        item_payload["created_at"] = created_at_val.isoformat()

      payload = json.dumps(
        {
          "event": "item.updated",
          "account_id": account_id,
          "item_id": item_id,
          "user_id": user_id,
          "item": item_payload,
        }
      ).encode("utf-8")
      req = urllib.request.Request(
        WEBHOOK_ITEM_UPDATED,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
      )
      # Best-effort; ignore response body
      urllib.request.urlopen(req, timeout=5)
    except Exception:
      # Intentionally swallow errors to avoid breaking item updates if the webhook is down
      pass
  return updated

@app.delete("/api/accounts/{account_id}/items/{item_id}", dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def delete_item(account_id: str, item_id: str, request: Request, user_id: str = Depends(current_user)):
  if from_web_ui(request):
    # Before the delete: the item's ILG Forms links go with it.
    push_to_ilgforms(ilgforms_outbound.item_deleting, account_id, item_id)
  rls.delete_item(account_id, item_id)
  return {"ok": True}

@app.get("/api/accounts/{account_id}/sections/{slug}/items", response_model=ItemsPage, dependencies=[Depends(ip_allowlist)])
async def list_section_items(account_id: str, slug: str, limit: int = Query(50, ge=1, le=200), cursor: Optional[str] = None, user_id: str = Depends(current_user)):
  items = rls.list_items(account_id, section=slug, limit=limit, cursor=cursor)
  next_cursor = items[-1]["id"] if items and len(items) == limit else None
  return ItemsPage(items=items, next=next_cursor)

@app.post("/api/accounts/{account_id}/sections/{slug}/items", response_model=ItemOut, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def create_section_item(account_id: str, slug: str, body: ItemCreate, request: Request, user_id: str = Depends(current_user)):
  item = rls.create_item(account_id, section=slug, name=body.name, data=body.data)
  if from_web_ui(request):
    push_to_ilgforms(ilgforms_outbound.item_created, account_id, slug, item)
  return item

# --- ILG Forms integration (inbound) ---
# Called by the ILG Forms platform, not by a logged-in user: no JWT and no IP
# allowlist (their egress IPs change). Authenticated by the integration key
# ILG Forms puts in the body, checked against ilgforms_integrations. Plain
# `def` so the blocking database work runs in FastAPI's threadpool.

def _ilgforms_inbound(request: Request, body: dict, handler):
  company_id = body.get("ProviderId") or request.query_params.get("CompanyId")
  try:
    integration = ilgforms_sync.authenticate(company_id, body.get("IntegrationKey"))
    return handler(integration, body)
  except ilgforms_sync.IntegrationAuthError as exc:
    ilgforms_sync.log_rejected(company_id=company_id, reason=exc.detail, status=exc.status)
    raise HTTPException(status_code=exc.status, detail=exc.detail)
  except ilgforms_sync.BadSubmission as exc:
    ilgforms_sync.log_rejected(company_id=company_id, reason=str(exc), status=400)
    raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/integrations/ilgforms/incident")
def ilgforms_incident(request: Request, body: dict = Body(...)):
  """Incident form: page1 + page1.locations[] (properties)."""
  return _ilgforms_inbound(request, body, ilgforms_sync.process_incident)

@app.post("/api/integrations/ilgforms/devices")
def ilgforms_devices_endpoint(request: Request, body: dict = Body(...)):
  """Appliance list form: page1 + deviceReview.devices[]."""
  return _ilgforms_inbound(request, body, ilgforms_devices.process_devices)

@app.post("/api/integrations/ilgforms/engineer-update")
def ilgforms_engineer_update(request: Request, body: dict = Body(...)):
  """Engineer's single-appliance form: deviceReview with a systemID."""
  return _ilgforms_inbound(request, body, ilgforms_devices.process_engineer_update)

@app.get("/api/accounts/{account_id}/integrations", dependencies=[Depends(ip_allowlist)])
def account_integrations(account_id: str, user_id: str = Depends(current_user)):
  """Lets the web UI know whether this account syncs natively (so it skips the legacy n8n calls)."""
  return {"ilgforms": ilgforms_sync.account_has_integration(account_id)}

# --- ILG Forms integration: sync status for the UI + super-admin tools ---

@app.get("/api/accounts/{account_id}/sections/{slug}/sync-status", dependencies=[Depends(ip_allowlist)])
def ilgforms_section_sync_status(account_id: str, slug: str, user_id: str = Depends(current_user)):
  return ilgforms_admin.section_sync_status(account_id, slug)

@app.get("/api/accounts/{account_id}/items/{item_id}/sync-status", dependencies=[Depends(ip_allowlist)])
def ilgforms_item_sync_status(account_id: str, item_id: str, user_id: str = Depends(current_user)):
  return ilgforms_admin.item_sync_status(account_id, item_id)

@app.get("/api/admin/ilgforms/summary", dependencies=[Depends(ip_allowlist)])
def ilgforms_summary(_admin: dict = Depends(require_super_admin)):
  return ilgforms_admin.summary()

@app.get("/api/admin/ilgforms/sync-log", dependencies=[Depends(ip_allowlist)])
def ilgforms_sync_log(
  limit: int = 50, offset: int = 0, flat: bool = False, account_id: str | None = None,
  direction: str | None = None, result: str | None = None, date_from: str | None = None,
  date_to: str | None = None, search: str | None = None, _admin: dict = Depends(require_super_admin),
):
  return ilgforms_admin.query_sync_log(
    limit=max(1, min(limit, 200)), offset=max(0, offset), flat=flat, account_id=account_id,
    direction=direction, result=result, date_from=date_from, date_to=date_to, search=search)

@app.get("/api/admin/ilgforms/sync-log/{log_id}/children", dependencies=[Depends(ip_allowlist)])
def ilgforms_sync_log_children(log_id: str, _admin: dict = Depends(require_super_admin)):
  return ilgforms_admin.sync_log_children(log_id)

@app.get("/api/admin/ilgforms/sync-log/{log_id}/payload", dependencies=[Depends(ip_allowlist)])
def ilgforms_sync_log_payload(log_id: str, _admin: dict = Depends(require_super_admin)):
  payload = ilgforms_admin.sync_log_payload(log_id)
  if payload is None:
    raise HTTPException(status_code=404, detail="No payload stored (kept for 30 days)")
  return payload

@app.get("/api/admin/ilgforms/orphans", dependencies=[Depends(ip_allowlist)])
def ilgforms_orphans(_admin: dict = Depends(require_super_admin)):
  return ilgforms_admin.list_orphans()

@app.get("/api/admin/ilgforms/integrations", dependencies=[Depends(ip_allowlist)])
def ilgforms_integrations(_admin: dict = Depends(require_super_admin)):
  return ilgforms_admin.list_integrations()

@app.post("/api/admin/ilgforms/integrations/{integration_id}/accounts", dependencies=[Depends(ip_allowlist)])
def ilgforms_link_account(integration_id: str, body: dict = Body(...), _admin: dict = Depends(require_super_admin)):
  """Link an account to an integration: it starts syncing and is added to the forms' account list."""
  if not ilgforms_admin.link_account(integration_id, str(body.get("account_id") or "")):
    raise HTTPException(status_code=404, detail="Integration or account not found")
  return {"ok": True}

@app.delete("/api/admin/ilgforms/accounts/{account_id}", dependencies=[Depends(ip_allowlist)])
def ilgforms_unlink_account(account_id: str, _admin: dict = Depends(require_super_admin)):
  """Unlink: the account stops syncing and is removed from the forms' account list. Its data is untouched."""
  if not ilgforms_admin.unlink_account(account_id):
    raise HTTPException(status_code=404, detail="That account is not linked")
  return {"ok": True}

@app.post("/api/admin/ilgforms/retry", dependencies=[Depends(ip_allowlist)])
def ilgforms_retry(body: dict = Body(default={}), _admin: dict = Depends(require_super_admin)):
  """Re-queue failed writebacks: {"log_id": "..."} for one, empty body for all."""
  return {"requeued": ilgforms_admin.retry_failed(body.get("log_id"))}

@app.post("/api/admin/ilgforms/reconcile", dependencies=[Depends(ip_allowlist)])
def ilgforms_reconcile(_admin: dict = Depends(require_super_admin)):
  if not ilgforms_jobs.outbound_enabled():
    raise HTTPException(status_code=409, detail="Outbound sync is switched off on this server (ILGFORMS_OUTBOUND)")
  return {"results": ilgforms_jobs.reconcile_all()}

# --- Comments API ---

@app.get("/api/accounts/{account_id}/items/{item_id}/comments", response_model=list[CommentOut], dependencies=[Depends(ip_allowlist)])
async def list_item_comments(account_id: str, item_id: str, user_id: str = Depends(current_user)):
  return rls.list_comments(account_id, item_id)

@app.post("/api/accounts/{account_id}/items/{item_id}/comments", response_model=CommentOut, status_code=201, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
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

# --- Section notes API ---

@app.get("/api/accounts/{account_id}/sections/{slug}/notes", response_model=list[SectionNoteOut], dependencies=[Depends(ip_allowlist)])
async def list_section_notes(account_id: str, slug: str, user_id: str = Depends(current_user)):
  rls.ensure_section_notes_table(account_id)
  return rls.list_section_notes(account_id, slug)

@app.post("/api/accounts/{account_id}/sections/{slug}/notes", response_model=SectionNoteOut, status_code=201, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def create_section_note(account_id: str, slug: str, body: SectionNoteCreate, user_id: str = Depends(current_user)):
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

  note = body.note.strip()
  if not note:
    raise HTTPException(status_code=400, detail="Note cannot be empty")
  rls.ensure_section_notes_table(account_id)
  return rls.create_section_note(account_id, slug, user_id, user_name, note)

# --- Account export template ---

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

def _fetch_template_row(account_id: str):
  with SessionLocal() as db:
    row = db.execute(text("""
      SELECT filename, content, updated_at,
             COALESCE((SELECT name FROM users WHERE id = uploaded_by), '')
      FROM account_template
      WHERE account_id = :a
      LIMIT 1
    """), {"a": account_id}).first()
  return row

@app.get("/api/accounts/{account_id}/template", response_model=TemplateInfo, dependencies=[Depends(ip_allowlist)])
async def get_account_template_info(account_id: str, user_id: str = Depends(current_user)):
  row = _fetch_template_row(account_id)
  if not row:
    raise HTTPException(status_code=404, detail="No template uploaded")
  return TemplateInfo(filename=row[0], updated_at=row[2], uploaded_by=row[3] or None)

@app.get("/api/accounts/{account_id}/template/file", dependencies=[Depends(ip_allowlist)])
async def download_account_template(account_id: str, user_id: str = Depends(current_user)):
  row = _fetch_template_row(account_id)
  if not row:
    raise HTTPException(status_code=404, detail="No template uploaded")
  filename, content, _, _ = row
  return Response(
    content=bytes(content),
    media_type=DOCX_MIME,
    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
  )

@app.get("/api/accounts/{account_id}/template/starter", dependencies=[Depends(ip_allowlist)])
async def download_starter_template(account_id: str, user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    acc = db.execute(text("SELECT name FROM accounts WHERE id = :a"), {"a": account_id}).first()
    if not acc:
      raise HTTPException(status_code=404, detail="Account not found")
    section_rows = db.execute(text("""
      SELECT slug, label, COALESCE(schema, '{}'::jsonb)
      FROM sections
      WHERE account_id = :a
      ORDER BY created_at
    """), {"a": account_id}).all()
  sections = [
    {"slug": r[0], "label": r[1], "schema": normalize_section_schema(r[2])}
    for r in section_rows
  ]
  data = exports.generate_starter_template(account_name=acc[0], sections=sections)
  filename = "starter_template.docx"
  return Response(
    content=data,
    media_type=DOCX_MIME,
    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
  )

@app.put("/api/accounts/{account_id}/template", response_model=TemplateInfo, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def upload_account_template(
  account_id: str,
  file: UploadFile = File(...),
  user_id: str = Depends(current_user),
):
  raw = await file.read()
  try:
    exports.validate_docx_bytes(raw)
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc))

  filename = file.filename or "template.docx"
  with SessionLocal() as db:
    acc = db.execute(text("SELECT 1 FROM accounts WHERE id = :a"), {"a": account_id}).first()
    if not acc:
      raise HTTPException(status_code=404, detail="Account not found")
    row = db.execute(text("""
      INSERT INTO account_template(account_id, filename, content, uploaded_by, updated_at)
      VALUES (:a, :f, :c, :u, now())
      ON CONFLICT (account_id) DO UPDATE
        SET filename = EXCLUDED.filename,
            content = EXCLUDED.content,
            uploaded_by = EXCLUDED.uploaded_by,
            updated_at = now()
      RETURNING filename, updated_at,
                COALESCE((SELECT name FROM users WHERE id = uploaded_by), '')
    """), {"a": account_id, "f": filename, "c": raw, "u": user_id}).first()
    db.commit()
  return TemplateInfo(filename=row[0], updated_at=row[1], uploaded_by=row[2] or None)

@app.delete("/api/accounts/{account_id}/template", status_code=204, dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def delete_account_template(account_id: str, user_id: str = Depends(current_user)):
  with SessionLocal() as db:
    res = db.execute(text("DELETE FROM account_template WHERE account_id = :a"), {"a": account_id})
    db.commit()
    if res.rowcount == 0:
      raise HTTPException(status_code=404, detail="No template uploaded")
  return None

# --- Section export ---

@app.post("/api/accounts/{account_id}/sections/{slug}/export", dependencies=[Depends(ip_allowlist), Depends(require_editor)])
async def export_section(
  account_id: str,
  slug: str,
  body: ExportRequest,
  user_id: str = Depends(current_user),
):
  with SessionLocal() as db:
    acc = db.execute(text("SELECT id::text, name FROM accounts WHERE id = :a"), {"a": account_id}).first()
    if not acc:
      raise HTTPException(status_code=404, detail="Account not found")
    section_row = db.execute(text("""
      SELECT slug, label, COALESCE(detail, ''), COALESCE(schema, '{}'::jsonb)
      FROM sections
      WHERE account_id = :a AND slug = :s
      LIMIT 1
    """), {"a": account_id, "s": slug}).first()
    if not section_row:
      raise HTTPException(status_code=404, detail="Section not found")
    template_row = db.execute(text("""
      SELECT filename, content
      FROM account_template
      WHERE account_id = :a
      LIMIT 1
    """), {"a": account_id}).first()
    if not template_row:
      raise HTTPException(status_code=409, detail="No template uploaded for this account")
    user_row = db.execute(text("SELECT COALESCE(NULLIF(name, ''), email) FROM users WHERE id = :u"), {"u": user_id}).first()
    exported_by = user_row[0] if user_row else ""

  account = {"id": acc[0], "name": acc[1]}
  section = {"slug": section_row[0], "label": section_row[1], "detail": section_row[2]}

  ids = body.item_ids
  items: list[dict]
  if ids:
    if len(ids) > exports.MAX_ITEMS_PER_EXPORT:
      raise HTTPException(status_code=400, detail=f"Too many items (max {exports.MAX_ITEMS_PER_EXPORT})")
    items = []
    for item_id in ids:
      it = rls.get_item(account_id, item_id)
      if it and it.get("section_slug") == slug:
        items.append(it)
  else:
    items = rls.list_items(account_id, section=slug, limit=exports.MAX_ITEMS_PER_EXPORT)

  if not items:
    raise HTTPException(status_code=400, detail="No items to export")

  context = exports.build_context(items=items, section=section, account=account, exported_by=exported_by)

  try:
    docx_bytes = exports.render_template(template_row[1], context)
  except exports.TemplateRenderError as exc:
    raise HTTPException(status_code=400, detail=str(exc))

  safe_section = "".join(c if c.isalnum() else "_" for c in (section["label"] or slug)).strip("_") or "section"
  base_name = f"{safe_section}_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

  if body.format == "docx":
    return Response(
      content=docx_bytes,
      media_type=DOCX_MIME,
      headers={"Content-Disposition": f'attachment; filename="{base_name}.docx"'},
    )

  try:
    pdf_bytes = exports.docx_to_pdf(docx_bytes, filename=f"{base_name}.docx")
  except exports.PdfConversionError as exc:
    raise HTTPException(status_code=503, detail=str(exc))

  return Response(
    content=pdf_bytes,
    media_type="application/pdf",
    headers={"Content-Disposition": f'attachment; filename="{base_name}.pdf"'},
  )

# --- Admin API ---

def account_ids_for_user(db, user_id: str) -> list[str]:
  rows = db.execute(
    text("SELECT account_id::text FROM memberships WHERE user_id = :u ORDER BY account_id"),
    {"u": user_id},
  ).all()
  return [r[0] for r in rows]

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
    include_super_fields = admin_ctx.get("user_type") == "super_admin"
    result: list[AdminUser] = []
    for r in rows:
      prefs = get_preferences(db, r[0]) if include_super_fields else None
      accounts = account_ids_for_user(db, r[0]) if include_super_fields else None
      result.append(AdminUser(
        id=r[0], email=r[1], name=r[2], user_type=r[3], is_active=r[4],
        preferences=Preferences(**prefs) if prefs else None,
        accounts=accounts,
      ))
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
    accounts = account_ids_for_user(db, new_id) if requester_type == "super_admin" else None
    return AdminUser(
      id=row[0], email=row[1], name=row[2], user_type=row[3], is_active=row[4],
      preferences=Preferences(**prefs) if prefs else None,
      accounts=accounts,
    )

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
            if requester_type != "super_admin":
                raise HTTPException(status_code=403, detail="Only super admins can manage account access")
            db.execute(text("DELETE FROM memberships WHERE user_id = :id"), {"id": user_id})
            if body.accounts:
                ids = list(set(body.accounts))
                db.execute(
                    text("INSERT INTO memberships(user_id, account_id, role) SELECT :u, a.id, 'owner' FROM accounts a WHERE a.id = ANY(:ids) ON CONFLICT DO NOTHING"),
                    {"u": user_id, "ids": ids}
                )

        row = db.execute(text("SELECT id::text, email, name, user_type, is_active FROM users WHERE id=:id"), {"id": user_id}).first()
        db.commit()
        prefs = get_preferences(db, user_id) if requester_type == "super_admin" else None
        accounts = account_ids_for_user(db, user_id) if requester_type == "super_admin" else None
        return AdminUser(
          id=row[0], email=row[1], name=row[2], user_type=row[3], is_active=row[4],
          preferences=Preferences(**prefs) if prefs else None,
          accounts=accounts,
        )

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