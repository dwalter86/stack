"""Read models and admin actions for the ILG Forms integration: per-item sync
status for the UI icon, and the super-admin sync log / summary / orphans / retry."""
from sqlalchemy import text

import ilgforms_jobs
import rls
from database import SessionLocal
from ilgforms_sync import JOB_WRITE_ITEM_ID, account_has_integration


def _iso(value):
  return value.isoformat() if value else None


def _aggregate(links: list[dict]) -> dict:
  """One state per item from its links (a property row and/or an appliance row).
  Synced if ANY ILG Forms sheet holds the item's id; waiting if a write is queued."""
  statuses = {l["status"] for l in links}
  status = "synced" if "synced" in statuses else ("pending" if "pending" in statuses else "not_synced")
  synced_at = max((l["last_synced_at"] for l in links if l["last_synced_at"]), default=None)
  return {"status": status, "last_synced_at": synced_at,
          "last_checked_at": max((l["last_checked_at"] for l in links if l["last_checked_at"]), default=None),
          "last_error": next((l["last_error"] for l in links if l["last_error"]), None),
          "row_id": next((l["row_id"] for l in links if l["row_id"]), None),
          "datasource": next((l["datasource"] for l in links if l["status"] == status), None),
          "links": [{"datasource": l["datasource"], "row_id": l["row_id"], "status": l["status"]} for l in links]}


def _link(r) -> dict:
  return {"status": r[1], "row_id": r[2], "datasource": r[3], "last_synced_at": _iso(r[4]),
          "last_checked_at": _iso(r[5]), "last_error": r[6]}


NOT_SYNCED = {"status": "not_synced", "row_id": None, "datasource": None, "last_synced_at": None,
              "last_checked_at": None, "last_error": None, "links": []}


# --- sync status for the icon ---------------------------------------------------

def section_sync_status(account_id: str, slug: str) -> dict:
  """{enabled, items: {item_id: state}}. Items with no entry are not synced."""
  if not account_has_integration(account_id):
    return {"enabled": False, "items": {}}
  rls.ensure_item_sync_table(account_id)
  schema = rls._schema_name(account_id)
  with SessionLocal() as db:
    db.execute(rls.set_current_account(account_id))
    rows = db.execute(text(f"""
      SELECT s.item_id::text, s.status, s.row_id, s.datasource, s.last_synced_at, s.last_checked_at, s.last_error
      FROM {schema}.item_sync s JOIN {schema}.items i ON i.id = s.item_id
      WHERE i.section_slug = :slug ORDER BY s.datasource
    """), {"slug": slug}).all()
  grouped: dict[str, list[dict]] = {}
  for r in rows:
    grouped.setdefault(r[0], []).append(_link(r))
  return {"enabled": True, "items": {item: _aggregate(links) for item, links in grouped.items()}}


def item_sync_status(account_id: str, item_id: str) -> dict:
  if not account_has_integration(account_id):
    return {"enabled": False, "state": None}
  rls.ensure_item_sync_table(account_id)
  schema = rls._schema_name(account_id)
  with SessionLocal() as db:
    db.execute(rls.set_current_account(account_id))
    rows = db.execute(text(f"""
      SELECT item_id::text, status, row_id, datasource, last_synced_at, last_checked_at, last_error
      FROM {schema}.item_sync WHERE item_id = :id ORDER BY datasource
    """), {"id": item_id}).all()
  return {"enabled": True, "state": _aggregate([_link(r) for r in rows]) if rows else dict(NOT_SYNCED)}


# --- super-admin views --------------------------------------------------------------

LOG_COLUMNS = """l.id::text, l.created_at, l.parent_id::text, l.account_id::text, a.name, l.direction, l.event,
  l.result, l.section_slug, l.section_label, l.item_id::text, l.row_id, l.entry_id, l.summary, l.error,
  (l.payload IS NOT NULL)"""


def _log_row(r, children: int | None = None, failed_children: int | None = None, job=None) -> dict:
  row = {"id": r[0], "created_at": r[1].isoformat(), "parent_id": r[2], "account_id": r[3], "account_name": r[4],
         "direction": r[5], "event": r[6], "result": r[7], "section_slug": r[8], "section_label": r[9],
         "item_id": r[10], "row_id": r[11], "entry_id": r[12], "summary": r[13], "error": r[14],
         "has_payload": bool(r[15])}
  if children is not None:
    row["children"] = children
    row["failed_children"] = failed_children or 0
  if job is not None:
    row["job"] = job
  return row


def query_sync_log(*, limit: int, offset: int, flat: bool, account_id=None, direction=None, result=None,
                   date_from=None, date_to=None, search=None) -> dict:
  """Grouped view (flat=False): one row per submission / standalone event, with child counts.
  Flat view: every row that matches, so a failed writeback shows up on its own."""
  where = ["1=1"] if flat else ["l.parent_id IS NULL"]
  params: dict = {"limit": limit, "offset": offset}
  if account_id:
    where.append("l.account_id = :account_id"); params["account_id"] = account_id
  if direction:
    where.append("l.direction = :direction"); params["direction"] = direction
  if result:
    where.append("l.result = :result"); params["result"] = result
  if date_from:
    where.append("l.created_at >= :date_from"); params["date_from"] = date_from
  if date_to:
    where.append("l.created_at < (CAST(:date_to AS date) + 1)"); params["date_to"] = date_to
  if search:
    where.append("""(l.summary ILIKE :search OR l.error ILIKE :search OR l.section_label ILIKE :search
                     OR l.section_slug ILIKE :search OR l.row_id ILIKE :search OR l.item_id::text ILIKE :search)""")
    params["search"] = f"%{search}%"
  clause = " AND ".join(where)

  with SessionLocal() as db:
    total = db.execute(text(f"SELECT count(*) FROM ilgforms_sync_log l WHERE {clause}"), params).scalar()
    rows = db.execute(text(f"""
      SELECT {LOG_COLUMNS},
        (SELECT count(*) FROM ilgforms_sync_log c WHERE c.parent_id = l.id),
        (SELECT count(*) FROM ilgforms_sync_log c WHERE c.parent_id = l.id AND c.result = 'failed'),
        j.status, j.attempts, j.next_attempt_at
      FROM ilgforms_sync_log l
      LEFT JOIN accounts a ON a.id = l.account_id
      LEFT JOIN ilgforms_jobs j ON j.log_id = l.id
      WHERE {clause}
      ORDER BY l.created_at DESC, l.id
      LIMIT :limit OFFSET :offset
    """), params).all()
  return {"total": total, "flat": flat, "rows": [
    _log_row(r, children=r[16], failed_children=r[17],
             job={"status": r[18], "attempts": r[19], "next_attempt_at": _iso(r[20])} if r[18] else None)
    for r in rows]}


def sync_log_children(log_id: str) -> list[dict]:
  with SessionLocal() as db:
    rows = db.execute(text(f"""
      SELECT {LOG_COLUMNS}, j.status, j.attempts, j.next_attempt_at
      FROM ilgforms_sync_log l
      LEFT JOIN accounts a ON a.id = l.account_id
      LEFT JOIN ilgforms_jobs j ON j.log_id = l.id
      WHERE l.parent_id = :id ORDER BY l.created_at, l.id
    """), {"id": log_id}).all()
  return [_log_row(r, job={"status": r[16], "attempts": r[17], "next_attempt_at": _iso(r[18])} if r[16] else None)
          for r in rows]


def sync_log_payload(log_id: str):
  with SessionLocal() as db:
    row = db.execute(text("SELECT payload FROM ilgforms_sync_log WHERE id = :id"), {"id": log_id}).first()
  return row[0] if row else None


def summary() -> dict:
  with SessionLocal() as db:
    today = dict(db.execute(text("""
      SELECT key, n FROM (
        SELECT 'received' AS key, count(*) AS n FROM ilgforms_sync_log
          WHERE event = 'incident.received' AND created_at >= date_trunc('day', now())
        UNION ALL SELECT 'items_created', count(*) FROM ilgforms_sync_log
          WHERE event = 'item.created' AND created_at >= date_trunc('day', now())
        UNION ALL SELECT 'items_updated', count(*) FROM ilgforms_sync_log
          WHERE event IN ('item.updated', 'item.linked') AND created_at >= date_trunc('day', now())
        UNION ALL SELECT 'sent', count(*) FROM ilgforms_sync_log
          WHERE direction = 'sent' AND result = 'success' AND created_at >= date_trunc('day', now())
        UNION ALL SELECT 'failed', count(*) FROM ilgforms_sync_log
          WHERE result = 'failed' AND created_at >= date_trunc('day', now())
      ) t
    """)).all())
    jobs = dict(db.execute(text("SELECT status, count(*) FROM ilgforms_jobs GROUP BY status")).all())
    integrations = db.execute(text("""
      SELECT i.id::text, i.name, i.company_id, i.main_datasource, i.enabled, i.last_reconciled_at,
             (SELECT count(*) FROM ilgforms_orphans o WHERE o.integration_id = i.id)
      FROM ilgforms_integrations i ORDER BY i.name
    """)).all()
    accounts = db.execute(text("""
      SELECT ia.integration_id::text, a.id::text, a.name
      FROM ilgforms_integration_accounts ia JOIN accounts a ON a.id = ia.account_id ORDER BY a.name
    """)).all()

  account_stats = []
  for integration_id, account_id, name in accounts:
    rls.ensure_item_sync_table(account_id)
    schema = rls._schema_name(account_id)
    with SessionLocal() as db:
      db.execute(rls.set_current_account(account_id))
      r = db.execute(text(f"""
        SELECT (SELECT count(*) FROM {schema}.items),
               (SELECT count(DISTINCT item_id) FROM {schema}.item_sync WHERE status = 'synced'),
               (SELECT count(DISTINCT item_id) FROM {schema}.item_sync p WHERE p.status = 'pending'
                  AND NOT EXISTS (SELECT 1 FROM {schema}.item_sync q WHERE q.item_id = p.item_id AND q.status = 'synced'))
      """)).first()
    account_stats.append({"integration_id": integration_id, "account_id": account_id, "name": name,
                          "items": r[0], "synced": r[1], "pending": r[2], "not_synced": r[0] - r[1] - r[2]})

  return {
    "outbound_enabled": ilgforms_jobs.outbound_enabled(),
    "today": {k: today.get(k, 0) for k in ("received", "items_created", "items_updated", "sent", "failed")},
    "jobs": {k: jobs.get(k, 0) for k in ("pending", "running", "failed")},
    "integrations": [{"id": r[0], "name": r[1], "company_id": r[2], "datasource": r[3], "enabled": r[4],
                      "last_reconciled_at": _iso(r[5]), "orphans": r[6]} for r in integrations],
    "accounts": account_stats,
  }


def list_orphans(limit: int = 500) -> list[dict]:
  with SessionLocal() as db:
    rows = db.execute(text("""
      SELECT o.row_id, o.item_id, o.account_id::text, a.name, o.detail, o.first_seen_at, o.last_seen_at, i.name, o.datasource
      FROM ilgforms_orphans o
      JOIN ilgforms_integrations i ON i.id = o.integration_id
      LEFT JOIN accounts a ON a.id = o.account_id
      ORDER BY o.first_seen_at DESC, o.row_id LIMIT :limit
    """), {"limit": limit}).all()
  return [{"row_id": r[0], "item_id": r[1], "account_id": r[2], "account_name": r[3],
           "datasource": r[8],
           "incident": (r[4] or {}).get("incd") or (r[4] or {}).get("incidNo") if isinstance(r[4], dict) else None,
           "house": (r[4] or {}).get("houseNo") or (r[4] or {}).get("houseNoName") if isinstance(r[4], dict) else None,
           "detail": ((r[4] or {}).get("initialVisit") or (r[4] or {}).get("Make")) if isinstance(r[4], dict) else None,
           "first_seen_at": _iso(r[5]), "last_seen_at": _iso(r[6]), "integration": r[7]} for r in rows]


def retry_failed(log_id: str | None = None) -> int:
  """Put failed writebacks back in the queue: one (by its log row) or all of them."""
  with SessionLocal() as db:
    params: dict = {}
    clause = "status = 'failed'"
    if log_id:
      clause += " AND log_id = :log_id"; params["log_id"] = log_id
    jobs = db.execute(text(f"""
      UPDATE ilgforms_jobs SET status = 'pending', attempts = 0, next_attempt_at = now(),
             last_error = NULL, finished_at = NULL
      WHERE {clause} RETURNING log_id::text, account_id::text, item_id::text, kind, COALESCE(datasource, payload->>'external_id')
    """), params).all()
    for job_log_id, account_id, item_id, kind, datasource in jobs:
      if job_log_id:
        db.execute(text("""
          UPDATE ilgforms_sync_log SET result = 'pending', error = NULL,
            summary = regexp_replace(COALESCE(summary, ''), ' \\[attempt.*$', '') || ' [retry requested]'
          WHERE id = :id
        """), {"id": job_log_id})
      if item_id and datasource:
        schema = rls._schema_name(account_id)
        if db.execute(text("SELECT to_regclass(:t)"), {"t": f"{schema}.item_sync"}).scalar():
          db.execute(rls.set_current_account(account_id))
          db.execute(text(f"""UPDATE {schema}.item_sync SET status = 'pending', last_error = NULL
                              WHERE item_id = :i AND datasource = :ds"""), {"i": item_id, "ds": datasource})
    db.commit()
  return len(jobs)
