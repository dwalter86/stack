"""ILG Forms integration: the two appliance forms.

  process_devices          replaces n8n "Graphic Electronics - Items upload"
                           (page1 + deviceReview.devices[]: create / update appliance items,
                           add comments, write the item id back to the device datasource)
  process_engineer_update  replaces n8n "Graphic Electronics - Engineer Job Updater"
                           (a single deviceReview for an item that already has a systemID)
"""
import json

from sqlalchemy import text

import ilgforms_matching as matching
import rls
from database import SessionLocal
from ilgforms_outbound import JOB_UPDATE_ROW
from ilgforms_sync import BadSubmission, IntegrationAuthError, _log, body_hash, redact_payload


def _entry_parts(body: dict):
  entry = body.get("Entry") if isinstance(body.get("Entry"), dict) else {}
  answers = entry.get("AnswersJson") if isinstance(entry.get("AnswersJson"), dict) else {}
  provider_id = body.get("ProviderId") or entry.get("ProviderId") or ""
  entry_ref = matching.hyphenate_entry_id(entry.get("Id"), entry.get("DSRowId"))
  user_name = " ".join(p for p in (str(entry.get("UserFirstName") or "").strip(),
                                   str(entry.get("UserLastName") or "").strip()) if p) or "ILG Forms"
  return entry, answers, provider_id, entry_ref, user_name


def _duplicate(db, integration_id: str, digest: str):
  return db.execute(text("SELECT log_id::text FROM ilgforms_inbound WHERE integration_id = :i AND body_hash = :h"),
                    {"i": integration_id, "h": digest}).first()


def _add_comment_once(db, schema: str, item_id: str, user_name: str, comment: str) -> bool:
  """Forms are resubmitted, so the same note must not pile up as repeat comments."""
  comment = str(comment or "").strip()
  if not comment:
    return False
  exists = db.execute(text(f"SELECT 1 FROM {schema}.comments WHERE item_id = :i AND comment = :c LIMIT 1"),
                      {"i": item_id, "c": comment}).first()
  if exists:
    return False
  db.execute(text(f"INSERT INTO {schema}.comments (item_id, user_name, comment) VALUES (:i, :u, :c)"),
             {"i": item_id, "u": user_name, "c": comment})
  return True


def _apply_update(db, schema: str, item_id: str, name: str | None, data: dict) -> list[str]:
  row = db.execute(text(f"SELECT name, COALESCE(data, '{{}}'::jsonb) FROM {schema}.items WHERE id = :id"),
                   {"id": item_id}).first()
  current = row[1] if isinstance(row[1], dict) else {}
  changed = sorted(k for k, v in data.items() if str(current.get(k, "")) != str(v))
  new_name = name if name and name != row[0] else None
  if new_name:
    changed.append("name")
  if changed:
    merged = dict(current); merged.update(data)
    db.execute(text(f"UPDATE {schema}.items SET data = CAST(:d AS jsonb), name = COALESCE(CAST(:n AS text), name) WHERE id = :id"),
               {"d": json.dumps(merged), "n": new_name, "id": item_id})
  return changed


def _set_link(db, schema: str, item_id: str, datasource: str, row_id: str | None, status: str):
  db.execute(text(f"""
    INSERT INTO {schema}.item_sync (item_id, datasource, row_id, status, last_checked_at, last_synced_at)
    VALUES (:i, :ds, :row, CAST(:st AS text), now(), CASE WHEN CAST(:st AS text) = 'synced' THEN now() END)
    ON CONFLICT (item_id, datasource) DO UPDATE SET row_id = COALESCE(EXCLUDED.row_id, {schema}.item_sync.row_id),
      status = EXCLUDED.status, last_checked_at = now(),
      last_synced_at = COALESCE(EXCLUDED.last_synced_at, {schema}.item_sync.last_synced_at), last_error = NULL
  """), {"i": item_id, "ds": datasource, "row": row_id or None, "st": status})


def process_devices(integration: dict, body: dict) -> dict:
  entry, answers, provider_id, entry_ref, user_name = _entry_parts(body)
  page1 = answers.get("page1") if isinstance(answers.get("page1"), dict) else {}
  review = answers.get("deviceReview") if isinstance(answers.get("deviceReview"), dict) else {}
  devices = review.get("devices") if isinstance(review.get("devices"), list) else []
  account_id = str(page1.get("accountid") or "").strip().lower()
  slug = str(page1.get("incdid") or "").strip()
  label = str(page1.get("refNo") or "").strip() or slug
  entry_id = str(entry.get("Id") or "").strip() or None
  datasource = str(page1.get("datasourceID") or "").strip() or integration["device_datasource"]

  if not account_id or not slug:
    raise BadSubmission("Submission is missing page1.accountid or page1.incdid")
  if account_id not in integration["accounts"]:
    raise IntegrationAuthError(403, "This integration may not write to that account")

  digest = body_hash(body)
  schema = rls._schema_name(account_id)
  counts = {"created": 0, "updated": 0, "linked": 0, "skipped": 0, "failed": 0, "comments": 0}
  results: list[dict] = []
  rls.ensure_item_sync_table(account_id)

  with SessionLocal() as db:
    duplicate = _duplicate(db, integration["id"], digest)
    if duplicate:
      _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
           event="submission.duplicate", result="skipped", section_slug=slug, section_label=label,
           entry_id=entry_id, summary="Identical submission already processed; ignored")
      db.commit()
      return {"ok": True, "duplicate": True, "section": slug, "original_log_id": duplicate[0]}

    db.execute(rls.set_current_account(account_id))
    section = db.execute(text("SELECT label FROM sections WHERE account_id = :a AND slug = :s"),
                         {"a": account_id, "s": slug}).first()
    if not section:
      raise BadSubmission(f"Incident {slug} does not exist in that account")
    label = section[0] or label

    section_items = [
      {"id": r[0], "name": r[1], "data": r[2] if isinstance(r[2], dict) else {}, "created_at": r[3].isoformat()}
      for r in db.execute(text(f"""
        SELECT id::text, name, COALESCE(data, '{{}}'::jsonb), created_at FROM {schema}.items
        WHERE section_slug = :s ORDER BY created_at, id
      """), {"s": slug}).all()]
    links = {r[0]: r[1] for r in db.execute(text(f"""
      SELECT s.item_id::text, s.row_id FROM {schema}.item_sync s JOIN {schema}.items i ON i.id = s.item_id
      WHERE i.section_slug = :s AND s.datasource = :ds AND s.row_id IS NOT NULL
    """), {"s": slug, "ds": datasource}).all()}
    sheet_ids = {str((d or {}).get("systemID") or "").strip().lower() for d in devices if isinstance(d, dict)} - {""}
    known_ids = {r[0] for r in db.execute(text(f"SELECT id::text FROM {schema}.items WHERE id::text = ANY(:ids)"),
                                          {"ids": list(sheet_ids)}).all()} if sheet_ids else set()

    parent_id = _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
                     event="devices.received", result="success", section_slug=slug, section_label=label,
                     entry_id=entry_id, summary="", payload=redact_payload(body))

    for plan in matching.plan_devices(devices, page1, section_items, links=links, known_item_ids=known_ids):
      device, row_id, action = plan["device"], plan["row_id"], plan["action"]
      what = " ".join(p for p in (str(device.get("make") or "").strip(), str(device.get("applianceType") or "").strip()) if p) or "Appliance"
      what = f"{what} at {str(page1.get('houseNoName') or device.get('houseNo') or '').strip() or '?'}"
      outcome = {"index": plan["index"], "row_id": row_id, "action": action, "item_id": plan["item_id"], "reason": plan["reason"]}
      try:
        with db.begin_nested():
          if action == matching.ACTION_SKIP:
            counts["skipped"] += 1
            _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
                 event="device.skipped", result="skipped", parent_id=parent_id, section_slug=slug, section_label=label,
                 row_id=row_id, entry_id=entry_id, summary=f"{what}: {plan['reason']} [{plan['item_id']}]")
            results.append(outcome)
            continue

          if action == matching.ACTION_CREATE:
            name, data = matching.device_item_fields(device, page1, provider_id=provider_id, entry_ref=entry_ref, existing=False)
            item_id = db.execute(text(f"""
              INSERT INTO {schema}.items (section_slug, name, data) VALUES (:s, :n, CAST(:d AS jsonb)) RETURNING id::text
            """), {"s": slug, "n": name or "", "d": json.dumps(data)}).scalar()
            changed = []
          else:
            item_id = plan["item_id"]
            name, data = matching.device_item_fields(device, page1, provider_id=provider_id, entry_ref=entry_ref,
                                                     existing=True, joins_property=bool(plan.get("joins_property")))
            changed = _apply_update(db, schema, item_id, name, data)
          outcome["item_id"] = item_id

          if _add_comment_once(db, schema, item_id, user_name, device.get("comments")):
            counts["comments"] += 1
            changed = changed + ["comment added"]

          needs_writeback = action in (matching.ACTION_CREATE, matching.ACTION_LINK)
          _set_link(db, schema, item_id, datasource, row_id,
                    "synced" if action == matching.ACTION_UPDATE else ("pending" if row_id else "not_synced"))

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
               result="success", parent_id=parent_id, section_slug=slug, section_label=label, item_id=item_id,
               row_id=row_id, entry_id=entry_id, summary=summary)

          if needs_writeback and row_id:
            sent_log = _log(db, integration_id=integration["id"], account_id=account_id, direction="sent",
                            event="itemid.writeback", result="pending", parent_id=parent_id, section_slug=slug,
                            section_label=label, item_id=item_id, row_id=row_id, entry_id=entry_id,
                            summary=f"{what}: item id queued for {datasource}")
            db.execute(text("""
              DELETE FROM ilgforms_jobs WHERE item_id = :item AND datasource = :ds AND kind = :k AND status = 'pending'
            """), {"item": item_id, "ds": datasource, "k": JOB_UPDATE_ROW})
            db.execute(text("""
              INSERT INTO ilgforms_jobs (integration_id, account_id, kind, item_id, section_slug, datasource, payload, log_id)
              VALUES (:i, :a, :k, :item, :slug, :ds, CAST(:p AS jsonb), :log)
            """), {"i": integration["id"], "a": account_id, "k": JOB_UPDATE_ROW, "item": item_id, "slug": slug,
                   "ds": datasource, "log": sent_log, "p": json.dumps({
                     "external_id": datasource, "row_id": row_id,
                     "columns": {"systemID": item_id, "systemAccountID": account_id, "systemSectionID": slug}})})
            outcome["writeback"] = "queued"
          results.append(outcome)
      except Exception as exc:  # noqa: BLE001
        counts["failed"] += 1
        outcome.update(action="failed", error=str(exc)[:500])
        results.append(outcome)
        _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
             event="device.failed", result="failed", parent_id=parent_id, section_slug=slug, section_label=label,
             row_id=row_id, entry_id=entry_id, summary=f"{what}: could not be processed", error=str(exc)[:2000])

    summary = f"{label}: {len(devices)} appliance{'s' if len(devices) != 1 else ''} - " + \
      (", ".join(f"{v} {k}" for k, v in counts.items() if v) or "nothing to do")
    db.execute(text("UPDATE ilgforms_sync_log SET summary = :s, result = :r WHERE id = :id"),
               {"s": summary, "r": "failed" if devices and counts["failed"] == len(devices) else "success", "id": parent_id})
    db.execute(text("INSERT INTO ilgforms_inbound (integration_id, entry_id, body_hash, log_id) VALUES (:i, :e, :h, :l)"),
               {"i": integration["id"], "e": entry_id, "h": digest, "l": parent_id})
    db.commit()
  return {"ok": True, "duplicate": False, "account_id": account_id, "section": slug, "counts": counts,
          "log_id": parent_id, "devices": results}


def process_engineer_update(integration: dict, body: dict) -> dict:
  entry, answers, provider_id, entry_ref, user_name = _entry_parts(body)
  review = answers.get("deviceReview") if isinstance(answers.get("deviceReview"), dict) else {}
  item_id = str(review.get("systemID") or "").strip().lower()
  account_id = str(review.get("systemAccountID") or "").strip().lower()
  entry_id = str(entry.get("Id") or "").strip() or None
  if not item_id or not account_id:
    raise BadSubmission("Submission is missing deviceReview.systemID or deviceReview.systemAccountID")
  if account_id not in integration["accounts"]:
    raise IntegrationAuthError(403, "This integration may not write to that account")

  digest = body_hash(body)
  schema = rls._schema_name(account_id)
  rls.ensure_item_sync_table(account_id)
  with SessionLocal() as db:
    duplicate = _duplicate(db, integration["id"], digest)
    if duplicate:
      _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
           event="submission.duplicate", result="skipped", item_id=item_id, entry_id=entry_id,
           summary="Identical submission already processed; ignored")
      db.commit()
      return {"ok": True, "duplicate": True, "item_id": item_id, "original_log_id": duplicate[0]}

    db.execute(rls.set_current_account(account_id))
    row = db.execute(text(f"""
      SELECT i.section_slug, s.label FROM {schema}.items i
      LEFT JOIN sections s ON s.account_id = :a AND s.slug = i.section_slug WHERE i.id::text = :id
    """), {"a": account_id, "id": item_id}).first()
    if not row:
      _log(db, integration_id=integration["id"], account_id=account_id, direction="received",
           event="engineer.update", result="skipped", entry_id=entry_id, row_id=str(review.get("uniq") or "") or None,
           summary=f"Engineer update for an item that no longer exists [{item_id}]", payload=redact_payload(body))
      db.execute(text("INSERT INTO ilgforms_inbound (integration_id, entry_id, body_hash) VALUES (:i, :e, :h)"),
                 {"i": integration["id"], "e": entry_id, "h": digest})
      db.commit()
      return {"ok": True, "duplicate": False, "item_id": item_id, "action": "skipped", "reason": "Item does not exist"}

    name, data = matching.engineer_item_fields(review, provider_id=provider_id, entry_ref=entry_ref)
    changed = _apply_update(db, schema, item_id, name, data)
    if _add_comment_once(db, schema, item_id, user_name, review.get("comments")):
      changed.append("comment added")
    row_id = str(review.get("uniq") or "").strip()
    if row_id and row_id != "0":
      _set_link(db, schema, item_id, integration["device_datasource"], row_id, "synced")
    what = " ".join(p for p in (str(review.get("make") or "").strip(), str(review.get("applianceType") or "").strip()) if p) or "Appliance"
    log_id = _log(db, integration_id=integration["id"], account_id=account_id, direction="updated",
                  event="engineer.update", result="success", section_slug=row[0], section_label=row[1] or row[0],
                  item_id=item_id, row_id=row_id or None, entry_id=entry_id,
                  summary=f"{what} at {str(review.get('houseNo') or '').strip() or '?'}: engineer update by {user_name} - "
                          + (f"updated {', '.join(changed)}" if changed else "no changes"),
                  payload=redact_payload(body))
    db.execute(text("INSERT INTO ilgforms_inbound (integration_id, entry_id, body_hash, log_id) VALUES (:i, :e, :h, :l)"),
               {"i": integration["id"], "e": entry_id, "h": digest, "l": log_id})
    db.commit()
  return {"ok": True, "duplicate": False, "account_id": account_id, "item_id": item_id, "action": "updated",
          "changed": changed, "log_id": log_id}
