"""Web platform -> ILG Forms: turn a change made in Stack into queued datasource
operations. Nothing is sent from here; the job runner (ilgforms_jobs) does that.

Three datasources per integration:
  incident_datasource  one row per incident (section)      key ID    = section slug
  main_datasource      one row per property               key ID,   itemId   = item id
  device_datasource    one row per appliance              key uniq, systemID = item id
The ILG Forms app lists an incident's properties from the property sheet and the
appliances from the device sheet. So an item added in the web platform always
gets a property row (unless its house already has one in that incident), and
also an appliance row when it carries any appliance detail. One item can
therefore be linked to both sheets.
"""
import json
import random
import string
from datetime import datetime, timezone

from sqlalchemy import text

import ilgforms_matching as matching
import rls
from database import SessionLocal
from ilgforms_sync import _log

JOB_INSERT_ROW = "insert_row"
JOB_UPDATE_ROW = "update_row"
JOB_DELETE_ROWS = "delete_rows"

APPLIANCE_FIELDS = ("itemMake", "itemModel", "itemSerialNumber", "itemApplianceType", "itemAge", "itemPrice")


def integration_for_account(db, account_id: str) -> dict | None:
  row = db.execute(text("""
    SELECT i.id::text, i.main_datasource, i.device_datasource, i.incident_datasource, i.engineer_emails
    FROM ilgforms_integration_accounts ia JOIN ilgforms_integrations i ON i.id = ia.integration_id
    WHERE ia.account_id = :a AND i.enabled LIMIT 1
  """), {"a": account_id}).first()
  if not row:
    return None
  return {"id": row[0], "main": row[1], "device": row[2], "incident": row[3],
          "engineer_emails": row[4] if isinstance(row[4], dict) else {}}


def is_appliance(data: dict) -> bool:
  return any(str((data or {}).get(f) or "").strip() for f in APPLIANCE_FIELDS)


def new_row_id() -> str:
  """Same shape ILG Forms generates: XXXX-DDMMYYYY-HHMMSS-NNNNNNNN."""
  now = datetime.now(timezone.utc)
  prefix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
  return f"{prefix}-{now:%d%m%Y}-{now:%H%M%S}-{random.randint(0, 99_999_999):08d}"


def _s(value) -> str:
  return "" if value is None else str(value).strip()


# --- column maps -------------------------------------------------------------------

def device_columns(item: dict, account_id: str, section: dict, engineer_emails: dict, *, for_insert: bool) -> dict:
  data = item.get("data") or {}
  engineer = _s(data.get("engineer"))
  cols = {
    "houseNoName": _s(data.get("houseNo")), "Postcode": _s(data.get("postcode")),
    "Make": _s(data.get("itemMake")), "Model": _s(data.get("itemModel")),
    "SerialNumber": _s(data.get("itemSerialNumber")), "ApplianceType": _s(data.get("itemApplianceType")),
    "ApproxAge": _s(data.get("itemAge")), "ApproxPrice": _s(data.get("itemPrice")).replace("£", ""),
    "Status": _s(data.get("reportStatus")), "Engineer": engineer,
    "eEmail": _s(engineer_emails.get(engineer)) if engineer else "",
    "Repair Status": _s(data.get("status")), "Name": _s(item.get("name")),
    "Contact Number": _s(data.get("telephone")), "address": _s(data.get("address")),
    "systemID": item["id"],
  }
  if for_insert:
    cols.update({"uniq": item["id"], "incidNo": _s(section.get("label")), "systemAccountID": account_id,
                 "systemSectionID": _s(section.get("slug"))})
  return cols


def property_columns(item: dict, account_id: str, section: dict, *, for_insert: bool, row_id: str | None = None,
                     has_appliance: bool = False) -> dict:
  data = item.get("data") or {}
  status = _s(data.get("status"))
  is_initial = matching.norm(status) in matching.INITIAL_VISIT_STATUSES
  cols = {
    "email": _s(data.get("email")), "houseNo": _s(data.get("houseNo")), "postCode": _s(data.get("postcode")),
    "streetName": _s(data.get("address")), "customerName": _s(item.get("name")),
    "teleNo1": _s(data.get("telephone")), "teleNo2": _s(data.get("telephone2")),
  }
  # initialVisit is the outcome of the first visit (Faults / No Faults / Out / N/A). For an item that is
  # also an appliance, status is the repair status and belongs to the device sheet, not here.
  if for_insert:
    cols["initialVisit"] = status if (status and is_initial) else ("Faults" if has_appliance else "")
    cols.update({"ID": row_id, "incd": _s(section.get("label")), "incdId": _s(section.get("slug")),
                 "accountId": account_id, "itemId": item["id"]})
  elif not has_appliance or (status and is_initial):
    cols["initialVisit"] = status
  return cols


def _house_has_property_row(db, schema: str, slug: str, item: dict, main_ds: str) -> bool:
  """Does another item for the same house in this incident already own a property row?"""
  data = item.get("data") or {}
  house, postcode = matching.norm(data.get("houseNo")), matching.norm_postcode(data.get("postcode"))
  if not house:
    return False
  rows = db.execute(text(f"""
    SELECT COALESCE(i.data, '{{}}'::jsonb) FROM {schema}.items i
    JOIN {schema}.item_sync s ON s.item_id = i.id AND s.datasource = :ds AND s.row_id IS NOT NULL
    WHERE i.section_slug = :slug AND i.id::text <> :me
  """), {"ds": main_ds, "slug": slug, "me": item["id"]}).all()
  for (other,) in rows:
    other = other if isinstance(other, dict) else {}
    if matching.norm(other.get("houseNo")) == house and (not postcode or matching.norm_postcode(other.get("postcode")) in ("", postcode)):
      return True
  return False


def incident_columns(account_id: str, section: dict) -> dict:
  return {"ID": _s(section.get("slug")), "incd": _s(section.get("label")), "postCode": _s(section.get("detail")),
          "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "colour": "In Progress", "account": account_id,
          "address": _s(section.get("address"))}


# --- queue helpers -----------------------------------------------------------------------

def _enqueue(db, integ: dict, account_id: str, *, kind: str, datasource: str, payload: dict, summary: str,
             direction: str, event: str, section: dict | None, item_id: str | None = None, row_id: str | None = None):
  log_id = _log(db, integration_id=integ["id"], account_id=account_id, direction=direction, event=event,
                result="pending", section_slug=(section or {}).get("slug"), section_label=(section or {}).get("label"),
                item_id=item_id, row_id=row_id, summary=summary)
  db.execute(text("""
    INSERT INTO ilgforms_jobs (integration_id, account_id, kind, item_id, section_slug, datasource, payload, log_id)
    VALUES (:i, :a, :kind, :item, :slug, :ds, CAST(:p AS jsonb), :log)
  """), {"i": integ["id"], "a": account_id, "kind": kind, "item": item_id, "slug": (section or {}).get("slug"),
         "ds": datasource, "p": json.dumps({"external_id": datasource, **payload}), "log": log_id})


def _retire(db, *, item_id: str | None = None, account_id: str | None = None, section_slug: str | None = None,
            kinds: tuple, datasource: str | None = None, note: str):
  """Drop not-yet-run jobs that a newer change makes pointless, closing their log rows."""
  where, params = ["status = 'pending'", "kind = ANY(:kinds)"], {"kinds": list(kinds), "note": note}
  if item_id:
    where.append("item_id = :item"); params["item"] = item_id
  if account_id and section_slug:
    where.append("account_id = :acc AND section_slug = :slug"); params.update(acc=account_id, slug=section_slug)
  if datasource:
    where.append("datasource = :ds"); params["ds"] = datasource
  clause = " AND ".join(where)
  db.execute(text(f"""
    UPDATE ilgforms_sync_log SET result = 'skipped', summary = COALESCE(summary, '') || ' (' || :note || ')'
    WHERE id IN (SELECT log_id FROM ilgforms_jobs WHERE {clause})
  """), params)
  return db.execute(text(f"DELETE FROM ilgforms_jobs WHERE {clause} RETURNING kind"), params).all()


def _section(db, account_id: str, slug: str) -> dict:
  row = db.execute(text("SELECT slug, label, COALESCE(detail, ''), COALESCE(address, '') FROM sections WHERE account_id = :a AND slug = :s"),
                   {"a": account_id, "s": slug}).first()
  return {"slug": slug, "label": row[1] if row else slug, "detail": row[2] if row else "", "address": row[3] if row else ""}


def _links(db, schema: str, item_id: str) -> dict:
  return {r[0]: r[1] for r in db.execute(text(
    f"SELECT datasource, row_id FROM {schema}.item_sync WHERE item_id = :i AND row_id IS NOT NULL"), {"i": item_id}).all()}


def _set_link(db, schema: str, item_id: str, datasource: str, row_id: str, status: str = "pending"):
  db.execute(text(f"""
    INSERT INTO {schema}.item_sync (item_id, datasource, row_id, status, last_checked_at)
    VALUES (:i, :ds, :row, :status, now())
    ON CONFLICT (item_id, datasource) DO UPDATE SET row_id = EXCLUDED.row_id, status = EXCLUDED.status,
      last_checked_at = now(), last_error = NULL
  """), {"i": item_id, "ds": datasource, "row": row_id, "status": status})


def _what(item: dict) -> str:
  house = _s((item.get("data") or {}).get("houseNo"))
  name = _s(item.get("name"))
  return " ".join(p for p in (f"House {house}" if house else "", f"({name})" if name else "") if p) or "Item"


# --- public hooks (called by the API endpoints for web-UI changes) ---------------------------------

def section_created(account_id: str, slug: str) -> bool:
  """A new incident made in the web platform: add it to the incident list, and
  give it the standard incident layout if it was created without one."""
  from ilgforms_sync import resolve_section_schema
  with SessionLocal() as db:
    integ = integration_for_account(db, account_id)
    if not integ:
      return False
    row = db.execute(text("SELECT COALESCE(schema, '{}'::jsonb) FROM sections WHERE account_id = :a AND slug = :s"),
                     {"a": account_id, "s": slug}).first()
    if row is not None and not ((row[0] or {}).get("fields")):
      custom = db.execute(text("SELECT section_schema FROM ilgforms_integrations WHERE id = :i"), {"i": integ["id"]}).scalar()
      db.execute(text("UPDATE sections SET schema = CAST(:sch AS jsonb) WHERE account_id = :a AND slug = :s"),
                 {"sch": json.dumps(resolve_section_schema(db, account_id, custom)), "a": account_id, "s": slug})
    already = db.execute(text("""
      SELECT 1 FROM ilgforms_section_links WHERE account_id = :a AND section_slug = :s AND datasource = :ds
    """), {"a": account_id, "s": slug, "ds": integ["incident"]}).first()
    if not already:
      section = _section(db, account_id, slug)
      db.execute(text("""
        INSERT INTO ilgforms_section_links (account_id, section_slug, datasource, row_id, status)
        VALUES (:a, :s, :ds, :s, 'pending') ON CONFLICT DO NOTHING
      """), {"a": account_id, "s": slug, "ds": integ["incident"]})
      _enqueue(db, integ, account_id, kind=JOB_INSERT_ROW, datasource=integ["incident"],
               payload={"values": incident_columns(account_id, section)}, direction="sent", event="incident.insert",
               summary=f"Incident {section['label']}: queued for {integ['incident']}", section=section, row_id=slug)
    db.commit()
  return True


def section_updated(account_id: str, slug: str):
  with SessionLocal() as db:
    integ = integration_for_account(db, account_id)
    if not integ:
      return
    linked = db.execute(text("""
      SELECT row_id FROM ilgforms_section_links WHERE account_id = :a AND section_slug = :s AND datasource = :ds
    """), {"a": account_id, "s": slug, "ds": integ["incident"]}).first()
    if not linked:
      return
    section = _section(db, account_id, slug)
    _retire(db, account_id=account_id, section_slug=slug, kinds=(JOB_UPDATE_ROW,), datasource=integ["incident"],
            note="superseded by a newer edit")
    _enqueue(db, integ, account_id, kind=JOB_UPDATE_ROW, datasource=integ["incident"],
             payload={"row_id": linked[0], "columns": {"incd": section["label"], "postCode": section["detail"],
                                                        "address": section["address"]}},
             direction="updated", event="incident.update",
             summary=f"Incident {section['label']}: name / post code / address queued for {integ['incident']}",
             section=section, row_id=linked[0])
    db.commit()


def item_created(account_id: str, slug: str, item: dict):
  with SessionLocal() as db:
    integ = integration_for_account(db, account_id)
    if not integ:
      return
    rls.ensure_item_sync_table(account_id)
    schema = rls._schema_name(account_id)
    db.execute(rls.set_current_account(account_id))
    section = _section(db, account_id, slug)
    appliance = is_appliance(item.get("data"))
    what = _what(item)
    if _house_has_property_row(db, schema, slug, item, integ["main"]):
      if not appliance:
        _log(db, integration_id=integ["id"], account_id=account_id, direction="sent", event="item.insert",
             result="skipped", section_slug=section["slug"], section_label=section["label"], item_id=item["id"],
             summary=f"{what}: this house already has a property row in {integ['main']}, and the item has no appliance details")
    else:
      row_id = new_row_id()
      _set_link(db, schema, item["id"], integ["main"], row_id)
      _enqueue(db, integ, account_id, kind=JOB_INSERT_ROW, datasource=integ["main"],
               payload={"values": property_columns(item, account_id, section, for_insert=True, row_id=row_id,
                                                   has_appliance=appliance)},
               direction="sent", event="item.insert", summary=f"{what}: new property row queued for {integ['main']}",
               section=section, item_id=item["id"], row_id=row_id)
    if appliance:
      _set_link(db, schema, item["id"], integ["device"], item["id"])
      _enqueue(db, integ, account_id, kind=JOB_INSERT_ROW, datasource=integ["device"],
               payload={"values": device_columns(item, account_id, section, integ["engineer_emails"], for_insert=True)},
               direction="sent", event="item.insert", summary=f"{what}: new appliance row queued for {integ['device']}",
               section=section, item_id=item["id"], row_id=item["id"])
    db.commit()


def item_updated(account_id: str, item: dict):
  """Push an edit to every row the item is linked to. An unlinked item gains an
  appliance row once it has appliance details; it is never given a new property
  row on edit, because an unlinked property may already have a blank row in the
  sheet and that would duplicate it."""
  with SessionLocal() as db:
    integ = integration_for_account(db, account_id)
    if not integ:
      return
    rls.ensure_item_sync_table(account_id)
    schema = rls._schema_name(account_id)
    db.execute(rls.set_current_account(account_id))
    section = _section(db, account_id, item.get("section_slug") or "")
    links = _links(db, schema, item["id"])
    what = _what(item)

    for datasource, row_id in links.items():
      if datasource == integ["device"]:
        columns = device_columns(item, account_id, section, integ["engineer_emails"], for_insert=False)
      elif datasource == integ["main"]:
        columns = property_columns(item, account_id, section, for_insert=False,
                                   has_appliance=integ["device"] in links or is_appliance(item.get("data")))
      else:
        continue
      _retire(db, item_id=item["id"], kinds=(JOB_UPDATE_ROW,), datasource=datasource, note="superseded by a newer edit")
      _enqueue(db, integ, account_id, kind=JOB_UPDATE_ROW, datasource=datasource,
               payload={"row_id": row_id, "columns": columns}, direction="updated", event="item.update",
               summary=f"{what}: changes queued for {datasource}", section=section, item_id=item["id"], row_id=row_id)

    if integ["device"] not in links and is_appliance(item.get("data")):
      _set_link(db, schema, item["id"], integ["device"], item["id"])
      _enqueue(db, integ, account_id, kind=JOB_INSERT_ROW, datasource=integ["device"],
               payload={"values": device_columns(item, account_id, section, integ["engineer_emails"], for_insert=True)},
               direction="sent", event="item.insert", summary=f"{what}: new row queued for {integ['device']}",
               section=section, item_id=item["id"], row_id=item["id"])
    elif not links:
      _log(db, integration_id=integ["id"], account_id=account_id, direction="updated", event="item.update",
           result="skipped", section_slug=section["slug"], section_label=section["label"], item_id=item["id"],
           summary=f"{what}: edited, but it has no ILG Forms row to update")
    db.commit()


def item_deleting(account_id: str, item_id: str):
  """Call BEFORE the item row is deleted: its links vanish with it."""
  with SessionLocal() as db:
    integ = integration_for_account(db, account_id)
    if not integ:
      return
    rls.ensure_item_sync_table(account_id)
    schema = rls._schema_name(account_id)
    db.execute(rls.set_current_account(account_id))
    row = db.execute(text(f"SELECT name, COALESCE(data, '{{}}'::jsonb), section_slug FROM {schema}.items WHERE id = :i"),
                     {"i": item_id}).first()
    if not row:
      return
    item = {"id": item_id, "name": row[0], "data": row[1] if isinstance(row[1], dict) else {}}
    section = _section(db, account_id, row[2])
    links = _links(db, schema, item_id)
    for datasource, row_id in links.items():
      # A row whose insert never ran does not exist in ILG Forms: cancel the insert, skip the delete.
      never_sent = _retire(db, item_id=item_id, kinds=(JOB_INSERT_ROW,), datasource=datasource, note="item was deleted")
      _retire(db, item_id=item_id, kinds=(JOB_UPDATE_ROW,), datasource=datasource, note="item was deleted")
      if never_sent:
        continue
      _enqueue(db, integ, account_id, kind=JOB_DELETE_ROWS, datasource=datasource, payload={"row_ids": [row_id]},
               direction="sent", event="item.delete", summary=f"{_what(item)}: row removal queued for {datasource}",
               section=section, row_id=row_id)
    _retire(db, item_id=item_id, kinds=("write_item_id",), note="item was deleted")
    db.commit()


def section_deleting(account_id: str, slug: str):
  """Call BEFORE the section and its items are deleted: removes the incident row
  and every property / appliance row belonging to its items."""
  with SessionLocal() as db:
    integ = integration_for_account(db, account_id)
    if not integ:
      return
    rls.ensure_item_sync_table(account_id)
    schema = rls._schema_name(account_id)
    db.execute(rls.set_current_account(account_id))
    section = _section(db, account_id, slug)
    never_sent = {(r[0], r[1]) for r in db.execute(text("""
      SELECT datasource, payload->'values'->>(CASE WHEN datasource = :dev THEN 'uniq' ELSE 'ID' END)
      FROM ilgforms_jobs WHERE account_id = :a AND section_slug = :s AND kind = :k AND status = 'pending'
    """), {"dev": integ["device"], "a": account_id, "s": slug, "k": JOB_INSERT_ROW}).all()}
    by_ds: dict[str, list[str]] = {}
    for datasource, row_id in db.execute(text(f"""
      SELECT s.datasource, s.row_id FROM {schema}.item_sync s JOIN {schema}.items i ON i.id = s.item_id
      WHERE i.section_slug = :s AND s.row_id IS NOT NULL
    """), {"s": slug}).all():
      if (datasource, row_id) not in never_sent:
        by_ds.setdefault(datasource, []).append(row_id)
    incident_never_sent = (integ["incident"], slug) in never_sent
    _retire(db, account_id=account_id, section_slug=slug,
            kinds=(JOB_INSERT_ROW, JOB_UPDATE_ROW, "write_item_id"), note="incident was deleted")
    for datasource, row_ids in by_ds.items():
      for start in range(0, len(row_ids), 50):
        chunk = row_ids[start:start + 50]
        _enqueue(db, integ, account_id, kind=JOB_DELETE_ROWS, datasource=datasource, payload={"row_ids": chunk},
                 direction="sent", event="item.delete", section=section,
                 summary=f"Incident {section['label']}: removal of {len(chunk)} row{'s' if len(chunk) != 1 else ''} queued for {datasource}")
    link = db.execute(text("""
      DELETE FROM ilgforms_section_links WHERE account_id = :a AND section_slug = :s AND datasource = :ds RETURNING row_id
    """), {"a": account_id, "s": slug, "ds": integ["incident"]}).first()
    if link and not incident_never_sent:
      _enqueue(db, integ, account_id, kind=JOB_DELETE_ROWS, datasource=integ["incident"],
               payload={"row_ids": [link[0]]}, direction="sent", event="incident.delete",
               summary=f"Incident {section['label']}: removal queued for {integ['incident']}", section=section, row_id=slug)
    db.commit()
