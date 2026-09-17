"""ILG Forms integration service: inbound incident submissions.

Replaces the n8n "Graphic Electronic - Add Incident" workflow. A submission
is processed in one database transaction: find or create the incident's
section, match every location against ALL items in it, create / update /
link items, record each item's sync state, queue the itemId writebacks, and
log what happened. Nothing here talks to ILG Forms; outbound writes are
queued in ilgforms_jobs for the job runner.
"""
import copy
import hashlib
import hmac
import json
from datetime import datetime, timezone

from sqlalchemy import text

import ilgforms_matching as matching
import rls
from database import SessionLocal

JOB_WRITE_ITEM_ID = "write_item_id"
REDACTED = "[redacted]"

# Fallback layout for a new incident, used only when the account has no incident to
# copy from. Normally resolve_section_schema() copies the account's newest incident,
# so dropdowns edited in the UI (engineers, statuses) carry forward and no names live
# in code. ilgforms_integrations.section_schema overrides both.
DEFAULT_SECTION_SCHEMA = {
  "fields": [
    {"key": "houseNo", "label": "House No/Name", "type": "string", "order": 1},
    {"key": "address", "label": "Address", "type": "string", "order": 2},
    {"key": "postcode", "label": "Post code", "type": "string", "order": 3},
    {"key": "telephone", "label": "Telephone", "type": "string", "order": 4},
    {"key": "telephone2", "label": "Telephone No 2", "type": "string", "order": 5},
    {"key": "email", "label": "Email", "type": "string", "order": 6},
    {"key": "visitDate", "label": "Visit Date", "type": "string", "order": 7},
    {"key": "itemApplianceType", "label": "Appliance Type", "type": "string", "order": 8},
    {"key": "itemMake", "label": "Item & Make", "type": "string", "order": 9},
    {"key": "itemModel", "label": "Model No", "type": "string", "order": 10},
    {"key": "itemSerialNumber", "label": "Serial Number", "type": "string", "order": 11},
    {"key": "itemAge", "label": "Approx Age", "type": "string", "order": 12},
    {"key": "itemPrice", "label": "Approx purchase price", "type": "string", "order": 13},
    {"key": "engineer", "label": "Engineer", "type": "dropdown", "order": 14, "options": {"option1": ""}},
    {"key": "reportStatus", "label": "Report Status", "type": "dropdown", "order": 15, "options": {
      "option1": "", "option2": "Repaired on Site", "option3": "White Goods Engineer to Call",
      "option4": "Requires Workshop Attention", "option5": "Customer to Replace and Claim",
      "option6": "Electrician to Call", "option7": "Gas Central Heating Engineer to Call",
      "option8": "Customer to Organise Own Repair Agent", "option9": "Cheque Settlement Required",
      "option10": "Alarm Engineer to Call", "option11": "GE to replace", "option12": "ILG TEST",
      "option13": "Customer to get a quote", "option14": "Not covered",
      "option15": "Replaced on Site, Ready to Return"}},
    {"key": "status", "label": "Status", "type": "dropdown", "order": 16, "options": {
      "option1": "", "option2": "In Progress", "option3": "Repaired", "option4": "Ready to Return",
      "option5": "Returned", "option6": "Awaiting parts", "option7": "Temp Repair",
      "option8": "Completed", "option9": "Out", "option10": "N/A", "option11": "No Faults",
      "option12": "BER", "option13": "W/shop"}},
  ],
  "status_summary": {
    "enabled": True,
    "field_key": "status",
    "red_values": ["In Progress"],
    "yellow_values": ["Awaiting parts", "Ready to Return", "Appointment Arranged", "Temp Repair", "W/shop"],
    "green_values": ["Completed", "Returned", "Repaired", "No Faults", "BER"],
    "blue_values": ["Out"],
    "red_label": "", "yellow_label": "", "green_label": "", "blue_label": "",
  },
}


def resolve_section_schema(db, account_id: str, override=None) -> dict:
  """Layout for a new incident: the integration's override, else a copy of the
  account's most recent incident layout, else the generic fallback."""
  if isinstance(override, dict) and override.get("fields"):
    return override
  row = db.execute(text("""
    SELECT schema FROM sections
    WHERE account_id = :a AND jsonb_array_length(COALESCE(schema->'fields', '[]'::jsonb)) > 0
    ORDER BY created_at DESC LIMIT 1
  """), {"a": account_id}).first()
  if row and isinstance(row[0], dict):
    schema = dict(row[0])
    # The n8n flow left a stray "status_summary" pseudo-field in most layouts: do not propagate it.
    schema["fields"] = [f for f in schema.get("fields") or [] if isinstance(f, dict) and f.get("key") != "status_summary"]
    if schema["fields"]:
      return schema
  return DEFAULT_SECTION_SCHEMA


class IntegrationAuthError(Exception):
  def __init__(self, status: int, detail: str):
    super().__init__(detail)
    self.status = status
    self.detail = detail


class BadSubmission(Exception):
  pass


# --- integration lookup / auth ---------------------------------------------

def authenticate(company_id, integration_key) -> dict:
  """Resolve the integration for an inbound call or raise IntegrationAuthError."""
  try:
    company = int(company_id)
  except (TypeError, ValueError):
    raise IntegrationAuthError(401, "Unknown integration")
  with SessionLocal() as db:
    row = db.execute(text("""
      SELECT id::text, name, company_id, integration_key, main_datasource, item_id_column,
             section_schema, enabled, device_datasource, incident_datasource
      FROM ilgforms_integrations WHERE company_id = :c LIMIT 1
    """), {"c": company}).first()
    if not row:
      raise IntegrationAuthError(401, "Unknown integration")
    supplied = str(integration_key or "")
    if not supplied or not hmac.compare_digest(supplied.encode(), str(row[3]).encode()):
      raise IntegrationAuthError(401, "Invalid integration key")
    if not row[7]:
      raise IntegrationAuthError(403, "Integration is disabled")
    accounts = [r[0] for r in db.execute(text(
      "SELECT account_id::text FROM ilgforms_integration_accounts WHERE integration_id = :i"), {"i": row[0]}).all()]
  return {"id": row[0], "name": row[1], "company_id": row[2], "main_datasource": row[4],
          "item_id_column": row[5], "section_schema": row[6], "accounts": accounts,
          "device_datasource": row[8], "incident_datasource": row[9]}


def account_has_integration(account_id: str) -> bool:
  with SessionLocal() as db:
    return bool(db.execute(text("""
      SELECT 1 FROM ilgforms_integration_accounts ia
      JOIN ilgforms_integrations i ON i.id = ia.integration_id
      WHERE ia.account_id = :a AND i.enabled LIMIT 1
    """), {"a": account_id}).first())


# --- helpers -----------------------------------------------------------------

def redact_payload(body: dict) -> dict:
  """Copy of the inbound body that is safe to store: no integration keys."""
  def walk(value):
    if isinstance(value, dict):
      return {k: (REDACTED if "key" in k.lower() and "integration" in k.lower() else walk(v))
              for k, v in value.items()}
    if isinstance(value, list):
      return [walk(v) for v in value]
    return value
  return walk(copy.deepcopy(body))


def body_hash(body: dict) -> str:
  return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _log(db, *, integration_id, account_id, direction, event, result, parent_id=None,
         section_slug=None, section_label=None, item_id=None, row_id=None, entry_id=None,
         summary=None, error=None, payload=None) -> str:
  return db.execute(text("""
    INSERT INTO ilgforms_sync_log (created_at, parent_id, integration_id, account_id, direction, event, result,
      section_slug, section_label, item_id, row_id, entry_id, summary, error, payload)
    VALUES (clock_timestamp(), :parent_id, :integration_id, :account_id, :direction, :event, :result,
      :section_slug, :section_label, :item_id, :row_id, :entry_id, :summary, :error,
      CAST(:payload AS jsonb))
    RETURNING id::text
  """), {
    "parent_id": parent_id, "integration_id": integration_id, "account_id": account_id,
    "direction": direction, "event": event, "result": result, "section_slug": section_slug,
    "section_label": section_label, "item_id": item_id, "row_id": row_id or None,
    "entry_id": entry_id, "summary": summary, "error": error,
    "payload": json.dumps(payload) if payload is not None else None,
  }).scalar()


def log_rejected(*, company_id, reason: str, status: int):
  """Best-effort record of an inbound call that failed auth or validation."""
  try:
    with SessionLocal() as db:
      integration_id = None
      try:
        integration_id = db.execute(text(
          "SELECT id::text FROM ilgforms_integrations WHERE company_id = :c"), {"c": int(company_id)}).scalar()
      except (TypeError, ValueError):
        pass
      _log(db, integration_id=integration_id, account_id=None, direction="received",
           event="submission.rejected", result="failed", summary=f"Rejected with HTTP {status}", error=reason)
      db.commit()
  except Exception:
    pass


def _retire_pending_writebacks(db, item_id: str, result: str, note: str):
  """Remove not-yet-run itemId writebacks for an item and close their log rows."""
  db.execute(text("""
    UPDATE ilgforms_sync_log SET result = :result, summary = COALESCE(summary, '') || ' (' || :note || ')'
    WHERE id IN (SELECT log_id FROM ilgforms_jobs WHERE item_id = :item AND kind = :kind AND status = 'pending')
  """), {"result": result, "note": note, "item": item_id, "kind": JOB_WRITE_ITEM_ID})
  db.execute(text("DELETE FROM ilgforms_jobs WHERE item_id = :item AND kind = :kind AND status = 'pending'"),
             {"item": item_id, "kind": JOB_WRITE_ITEM_ID})


def _describe(location: dict) -> str:
  house = str(location.get("houseNo") or "").strip()
  name = str(location.get("customersName") or "").strip()
  return " ".join(part for part in (f"House {house}" if house else "", f"({name})" if name else "") if part) or "Location"


# --- inbound incident ----------------------------------------------------------

def process_incident(integration: dict, body: dict) -> dict:
  entry = body.get("Entry") if isinstance(body.get("Entry"), dict) else {}
  answers = entry.get("AnswersJson") if isinstance(entry.get("AnswersJson"), dict) else {}
  page1 = answers.get("page1") if isinstance(answers.get("page1"), dict) else {}

  slug = str(page1.get("ID") or "").strip()
  label = str(page1.get("incd") or "").strip() or slug
  account_id = str(page1.get("account") or "").strip().lower()
  detail = str(page1.get("postCode") or "").strip()
  entry_id = str(entry.get("Id") or "").strip() or None
  locations = page1.get("locations") if isinstance(page1.get("locations"), list) else []

  if not slug or not account_id:
    raise BadSubmission("Submission is missing page1.ID or page1.account")
  if account_id not in integration["accounts"]:
    raise IntegrationAuthError(403, "This integration may not write to that account")

  digest = body_hash(body)
  now_iso = datetime.now(timezone.utc).isoformat()
  schema = rls._schema_name(account_id)
  counts = {"created": 0, "updated": 0, "linked": 0, "skipped": 0, "failed": 0}
  results: list[dict] = []

  rls.ensure_item_sync_table(account_id)

  with SessionLocal() as db:
    duplicate = db.execute(text("""
      SELECT log_id::text FROM ilgforms_inbound WHERE integration_id = :i AND body_hash = :h
    """), {"i": integration["id"], "h": digest}).first()
    if duplicate:
      _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
           event="submission.duplicate", result="skipped", section_slug=slug, section_label=label,
           entry_id=entry_id, summary="Identical submission already processed; ignored",
           parent_id=None)
      db.commit()
      return {"ok": True, "duplicate": True, "section": slug, "original_log_id": duplicate[0]}

    db.execute(rls.set_current_account(account_id))

    section_schema = resolve_section_schema(db, account_id, integration.get("section_schema"))
    section_created = bool(db.execute(text("""
      INSERT INTO sections(account_id, slug, label, detail, schema)
      VALUES (:a, :slug, :label, :detail, CAST(:schema AS jsonb))
      ON CONFLICT (account_id, slug) DO NOTHING
      RETURNING id
    """), {"a": account_id, "slug": slug, "label": label, "detail": detail,
           "schema": json.dumps(section_schema)}).first())

    db.execute(text("""
      INSERT INTO ilgforms_section_links (account_id, section_slug, datasource, row_id, status, last_checked_at)
      VALUES (:a, :s, :ds, :s, 'pending', now()) ON CONFLICT DO NOTHING
    """), {"a": account_id, "s": slug, "ds": integration["incident_datasource"]})

    # Every item in the section: no page limit (the n8n flow only ever saw 50).
    section_items = [
      {"id": r[0], "name": r[1], "data": r[2] if isinstance(r[2], dict) else {}, "created_at": r[3].isoformat()}
      for r in db.execute(text(f"""
        SELECT id::text, name, COALESCE(data, '{{}}'::jsonb), created_at
        FROM {schema}.items WHERE section_slug = :s ORDER BY created_at, id
      """), {"s": slug}).all()
    ]
    items_by_id = {item["id"]: item for item in section_items}
    links = {r[0]: r[1] for r in db.execute(text(f"""
      SELECT s.item_id::text, s.row_id FROM {schema}.item_sync s
      JOIN {schema}.items i ON i.id = s.item_id
      WHERE i.section_slug = :s AND s.row_id IS NOT NULL AND s.datasource = :ds
    """), {"s": slug, "ds": integration["main_datasource"]}).all()}

    sheet_ids = {str(l.get("itemId") or "").strip().lower() for l in locations if isinstance(l, dict)} - {""}
    known_ids = set()
    if sheet_ids - set(items_by_id):
      known_ids = {r[0] for r in db.execute(text(f"""
        SELECT id::text FROM {schema}.items WHERE id::text = ANY(:ids)
      """), {"ids": list(sheet_ids)}).all()}

    parent_id = _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
                     event="incident.received", result="success", section_slug=slug, section_label=label,
                     entry_id=entry_id, summary="", payload=redact_payload(body))

    plans = matching.plan_locations(locations, section_items, links=links, known_item_ids=known_ids)

    for plan in plans:
      location, row_id, action = plan["location"], plan["row_id"], plan["action"]
      what = _describe(location)
      outcome = {"index": plan["index"], "row_id": row_id, "action": action, "item_id": plan["item_id"],
                 "reason": plan["reason"]}
      try:
        with db.begin_nested():  # one bad location must not sink the rest
          if action == matching.ACTION_SKIP:
            counts["skipped"] += 1
            _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
                 event="location.skipped", result="skipped", parent_id=parent_id, section_slug=slug,
                 section_label=label, row_id=row_id, entry_id=entry_id,
                 summary=f"{what}: {plan['reason']} [{plan['item_id']}]")
            results.append(outcome)
            continue

          if action == matching.ACTION_CREATE:
            name, data = matching.item_fields(location, existing_data=None, now_iso=now_iso)
            item_id = db.execute(text(f"""
              INSERT INTO {schema}.items (section_slug, name, data)
              VALUES (:s, :n, CAST(:d AS jsonb)) RETURNING id::text
            """), {"s": slug, "n": name or "", "d": json.dumps(data)}).scalar()
            changed = sorted(data)
          else:
            item_id = plan["item_id"]
            current = items_by_id.get(item_id)
            if current is None:  # live item in another section
              row = db.execute(text(f"SELECT name, COALESCE(data, '{{}}'::jsonb) FROM {schema}.items WHERE id = :id"),
                               {"id": item_id}).first()
              current = {"name": row[0], "data": row[1] if isinstance(row[1], dict) else {}}
            name, data = matching.item_fields(location, existing_data=current["data"], now_iso=now_iso)
            changed = sorted(k for k, v in data.items() if str(current["data"].get(k, "")) != str(v))
            new_name = name if name and name != current["name"] else None
            if new_name:
              changed.append("name")
            if changed:
              merged = dict(current["data"])
              merged.update(data)
              db.execute(text(f"""
                UPDATE {schema}.items SET data = CAST(:d AS jsonb), name = COALESCE(CAST(:n AS text), name) WHERE id = :id
              """), {"d": json.dumps(merged), "n": new_name, "id": item_id})

          outcome["item_id"] = item_id
          needs_writeback = action in (matching.ACTION_CREATE, matching.ACTION_LINK)
          can_writeback = needs_writeback and bool(row_id)
          sync_status = "synced" if action == matching.ACTION_UPDATE else ("pending" if can_writeback else "not_synced")
          db.execute(text(f"""
            INSERT INTO {schema}.item_sync (item_id, datasource, row_id, status, last_checked_at, last_synced_at, last_error)
            VALUES (:item, :ds, :row, CAST(:status AS text), now(), CASE WHEN CAST(:status AS text) = 'synced' THEN now() END, :err)
            ON CONFLICT (item_id, datasource) DO UPDATE SET
              row_id = COALESCE(EXCLUDED.row_id, {schema}.item_sync.row_id),
              status = EXCLUDED.status,
              last_checked_at = now(),
              last_synced_at = COALESCE(EXCLUDED.last_synced_at, {schema}.item_sync.last_synced_at),
              last_error = EXCLUDED.last_error
          """), {"item": item_id, "ds": integration["main_datasource"], "row": row_id or None,
                 "status": sync_status,
                 "err": None if (can_writeback or not needs_writeback) else "Location has no row id; cannot write itemId back"})

          if action == matching.ACTION_CREATE:
            counts["created"] += 1
            direction, event, summary = "received", "item.created", f"{what}: item created"
          elif action == matching.ACTION_LINK:
            counts["linked"] += 1
            direction, event = "updated", "item.linked"
            summary = f"{what}: {plan['reason']}" + (f"; updated {', '.join(changed)}" if changed else "")
          else:
            counts["updated"] += 1
            direction, event = "updated", "item.updated"
            summary = f"{what}: " + (f"updated {', '.join(changed)}" if changed else "no changes")

          _log(db, integration_id=integration["id"], account_id=account_id, direction=direction, event=event,
               result="success", parent_id=parent_id, section_slug=slug, section_label=label,
               item_id=item_id, row_id=row_id, entry_id=entry_id, summary=summary)

          if action == matching.ACTION_UPDATE:
            # The sheet already holds this itemId, so a queued writeback is moot.
            _retire_pending_writebacks(db, item_id, "success", "confirmed: the sheet already holds this itemId")

          if needs_writeback:
            if can_writeback:
              _retire_pending_writebacks(db, item_id, "skipped", "superseded by a newer submission")
              sent_log = _log(db, integration_id=integration["id"], account_id=account_id, direction="sent",
                              event="itemid.writeback", result="pending", parent_id=parent_id,
                              section_slug=slug, section_label=label, item_id=item_id, row_id=row_id,
                              entry_id=entry_id, summary=f"{what}: itemId queued for {integration['main_datasource']}")
              db.execute(text("""
                INSERT INTO ilgforms_jobs (integration_id, account_id, kind, item_id, section_slug, datasource, payload, log_id)
                VALUES (:i, :a, :kind, :item, :slug, :ds, CAST(:p AS jsonb), :log)
              """), {"i": integration["id"], "a": account_id, "kind": JOB_WRITE_ITEM_ID, "item": item_id,
                     "slug": slug, "ds": integration["main_datasource"],
                     "log": sent_log, "p": json.dumps({
                       "external_id": integration["main_datasource"], "row_id": row_id,
                       "column": integration["item_id_column"], "value": item_id})})
              outcome["writeback"] = "queued"
            else:
              _log(db, integration_id=integration["id"], account_id=account_id, direction="sent",
                   event="itemid.writeback", result="failed", parent_id=parent_id, section_slug=slug,
                   section_label=label, item_id=item_id, entry_id=entry_id,
                   summary=f"{what}: cannot write itemId back", error="Location has no row id (uniq_up / uniq)")
              outcome["writeback"] = "impossible"
          results.append(outcome)
      except Exception as exc:  # noqa: BLE001 - logged per location, batch continues
        counts["failed"] += 1
        outcome.update(action="failed", error=str(exc)[:500])
        results.append(outcome)
        _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
             event="location.failed", result="failed", parent_id=parent_id, section_slug=slug,
             section_label=label, row_id=row_id, entry_id=entry_id,
             summary=f"{what}: could not be processed", error=str(exc)[:2000])

    summary = (f"{label}: {len(locations)} location{'s' if len(locations) != 1 else ''}"
               f"{' (new incident)' if section_created else ''} - "
               + ", ".join(f"{v} {k}" for k, v in counts.items() if v) if locations
               else f"{label}: no locations{' (new incident)' if section_created else ''}")
    db.execute(text("UPDATE ilgforms_sync_log SET summary = :s, result = :r WHERE id = :id"),
               {"s": summary, "r": "failed" if counts["failed"] and counts["failed"] == len(locations) else "success",
                "id": parent_id})
    db.execute(text("""
      INSERT INTO ilgforms_inbound (integration_id, entry_id, body_hash, log_id) VALUES (:i, :e, :h, :l)
    """), {"i": integration["id"], "e": entry_id, "h": digest, "l": parent_id})
    db.commit()

  return {"ok": True, "duplicate": False, "account_id": account_id, "section": slug,
          "section_created": section_created, "counts": counts, "log_id": parent_id, "locations": results}
