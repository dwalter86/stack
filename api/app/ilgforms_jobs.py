"""ILG Forms outbound work: the job runner, the reconcile pass, payload pruning,
and the background loop that drives them.

Safety switch: nothing in this module talks to ILG Forms unless the
ILGFORMS_OUTBOUND environment variable is "on". It defaults to off so a
developer's local stack can never write to the live datasource by accident;
production sets it in .env.
"""
import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone

from sqlalchemy import text

import rls
from database import SessionLocal
from ilgforms_client import IlgFormsClient, IlgFormsError, IlgFormsRetryable
from ilgforms_sync import JOB_WRITE_ITEM_ID, _log
from ilgforms_outbound import JOB_INSERT_ROW, JOB_UPDATE_ROW, JOB_DELETE_ROWS

# Wait before attempt 2, 3, ... A job that fails MAX_ATTEMPTS times is marked failed.
BACKOFF_SECONDS = [30, 120, 600, 1800, 7200]
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1
STALE_RUNNING_MINUTES = 10
JOB_POLL_SECONDS = 15
RECONCILE_SECONDS = 15 * 60
PRUNE_SECONDS = 24 * 60 * 60
PAYLOAD_RETENTION_DAYS = 30
ADVISORY_LOCK_KEY = 815_001  # one worker at a time across API processes


def outbound_enabled() -> bool:
  return os.environ.get("ILGFORMS_OUTBOUND", "off").strip().lower() in ("on", "1", "true", "yes")


def _integration(db, integration_id: str) -> dict | None:
  row = db.execute(text("""
    SELECT id::text, name, company_id, integration_key, main_datasource, item_id_column,
           row_id_column, account_id_column, enabled, device_datasource, incident_datasource
    FROM ilgforms_integrations WHERE id = :i
  """), {"i": integration_id}).first()
  if not row:
    return None
  return {"id": row[0], "name": row[1], "company_id": row[2], "integration_key": row[3],
          "main_datasource": row[4], "item_id_column": row[5], "row_id_column": row[6],
          "account_id_column": row[7], "enabled": row[8], "device_datasource": row[9],
          "incident_datasource": row[10]}


def _client(integration: dict, transport=None) -> IlgFormsClient:
  return IlgFormsClient(integration["company_id"], integration["integration_key"], transport=transport)


# --- job runner ----------------------------------------------------------------

def claim_due_jobs(limit: int = 25) -> list[dict]:
  with SessionLocal() as db:
    # A worker that died mid-job leaves it "running": put those back in the queue.
    db.execute(text(f"""
      UPDATE ilgforms_jobs SET status = 'pending'
      WHERE status = 'running' AND next_attempt_at < now() - interval '{STALE_RUNNING_MINUTES} minutes'
    """))
    rows = db.execute(text("""
      UPDATE ilgforms_jobs SET status = 'running', attempts = attempts + 1, next_attempt_at = now()
      WHERE id IN (
        SELECT j.id FROM ilgforms_jobs j
        JOIN ilgforms_integrations i ON i.id = j.integration_id AND i.enabled
        WHERE j.status = 'pending' AND j.next_attempt_at <= now()
        ORDER BY j.created_at LIMIT :limit FOR UPDATE OF j SKIP LOCKED
      )
      RETURNING id::text, integration_id::text, account_id::text, kind, item_id::text, payload, attempts, log_id::text, datasource
    """), {"limit": limit}).all()
    db.commit()
  return [{"id": r[0], "integration_id": r[1], "account_id": r[2], "kind": r[3], "item_id": r[4],
           "payload": r[5] if isinstance(r[5], dict) else json.loads(r[5]), "attempts": r[6], "log_id": r[7],
           "datasource": r[8]}
          for r in rows]


def _finish_job(job: dict, *, status: str, log_result: str, note: str, error: str | None, retry_in: int | None):
  with SessionLocal() as db:
    db.execute(text("""
      UPDATE ilgforms_jobs
      SET status = CAST(:status AS text), last_error = :error,
          next_attempt_at = now() + make_interval(secs => :retry_in),
          finished_at = CASE WHEN CAST(:status AS text) IN ('done', 'failed') THEN now() END
      WHERE id = :id
    """), {"status": status, "error": error, "retry_in": retry_in or 0, "id": job["id"]})
    if job.get("log_id"):
      db.execute(text("""
        UPDATE ilgforms_sync_log
        SET result = :result, error = :error,
            summary = regexp_replace(
              CASE WHEN CAST(:result AS text) = 'success' THEN replace(replace(COALESCE(summary, ''), 'itemId queued for', 'itemId written to'), ' queued for ', ' sent to ')
                   ELSE COALESCE(summary, '') END, ' \\[attempt.*$', '') || :note
        WHERE id = :id
      """), {"result": log_result, "error": error, "note": note, "id": job["log_id"]})
    if job.get("item_id") and status in ("done", "failed"):
      schema = rls._schema_name(job["account_id"])
      datasource = job.get("datasource") or (job.get("payload") or {}).get("external_id")
      has_table = db.execute(text("SELECT to_regclass(:t)"), {"t": f"{schema}.item_sync"}).scalar()
      if has_table and datasource:
        db.execute(rls.set_current_account(job["account_id"]))
        # "synced" is only ever granted by the reconcile pass reading the sheet back.
        db.execute(text(f"""
          UPDATE {schema}.item_sync
          SET status = CASE WHEN :failed THEN 'not_synced' ELSE status END, last_error = :error
          WHERE item_id = :item AND datasource = :ds
        """), {"failed": status == "failed", "error": error, "item": job["item_id"], "ds": datasource})
    db.commit()


_header_cache: dict[tuple, tuple[float, list[str]]] = {}
HEADER_CACHE_SECONDS = 600

def _headers(client, integration_id: str, external_id: str) -> list[str]:
  key = (integration_id, external_id)
  hit = _header_cache.get(key)
  if hit and time.monotonic() - hit[0] < HEADER_CACHE_SECONDS:
    return hit[1]
  headers = client.get_headers(external_id)
  _header_cache[key] = (time.monotonic(), headers)
  return headers


def run_job(job: dict, integration: dict, transport=None) -> str:
  """Execute one job. Returns 'done', 'retry', or 'failed'."""
  attempt = f" [attempt {job['attempts']}/{MAX_ATTEMPTS}]"
  try:
    payload = job["payload"]
    with _client(integration, transport) as client:
      if job["kind"] == JOB_WRITE_ITEM_ID:
        client.update_cells(payload["external_id"], payload["row_id"], {payload["column"]: payload["value"]})
      elif job["kind"] == JOB_UPDATE_ROW:
        headers = _headers(client, integration["id"], payload["external_id"])
        columns = {k: v for k, v in payload["columns"].items() if k in headers}
        client.update_cells(payload["external_id"], payload["row_id"], columns)
      elif job["kind"] == JOB_INSERT_ROW:
        headers = _headers(client, integration["id"], payload["external_id"])
        # Only send columns the datasource really has (layouts differ slightly between companies).
        values = {k: v for k, v in payload["values"].items() if k in headers}
        client.insert_row(payload["external_id"], values, headers=headers)
      elif job["kind"] == JOB_DELETE_ROWS:
        try:
          client.delete_rows(payload["external_id"], payload["row_ids"])
        except IlgFormsRetryable:
          raise
        except IlgFormsError as exc:
          if "No Rows Found" not in str(exc):   # already gone is the outcome we wanted
            raise
      else:
        raise IlgFormsError(f"Unknown job kind: {job['kind']}")
    _finish_job(job, status="done", log_result="success", note=attempt,
                error=None, retry_in=None)
    return "done"
  except IlgFormsRetryable as exc:
    if job["attempts"] >= MAX_ATTEMPTS:
      _finish_job(job, status="failed", log_result="failed", note=attempt + " gave up",
                  error=str(exc)[:2000], retry_in=None)
      return "failed"
    wait = BACKOFF_SECONDS[min(job["attempts"] - 1, len(BACKOFF_SECONDS) - 1)]
    _finish_job(job, status="pending", log_result="retrying", note=attempt + f" will retry in {wait}s",
                error=str(exc)[:2000], retry_in=wait)
    return "retry"
  except Exception as exc:  # noqa: BLE001 - rejected by ILG Forms, or a bug: do not loop on it
    _finish_job(job, status="failed", log_result="failed", note=attempt + " rejected",
                error=str(exc)[:2000], retry_in=None)
    return "failed"


def run_due_jobs(limit: int = 25, transport=None) -> dict:
  """Run every due job, one at a time (parallel writers trip ILG Forms'
  datasource lock). Reconciles any integration that had a successful write."""
  if not outbound_enabled():
    return {"enabled": False}
  totals = {"enabled": True, "done": 0, "retry": 0, "failed": 0}
  touched: set[str] = set()
  integrations: dict[str, dict | None] = {}
  for job in claim_due_jobs(limit):
    if job["integration_id"] not in integrations:
      with SessionLocal() as db:
        integrations[job["integration_id"]] = _integration(db, job["integration_id"])
    integration = integrations[job["integration_id"]]
    if not integration:
      _finish_job(job, status="failed", log_result="failed", note=" integration missing",
                  error="Integration no longer exists", retry_in=None)
      totals["failed"] += 1
      continue
    outcome = run_job(job, integration, transport)
    totals[outcome] += 1
    if outcome == "done":
      touched.add(job["integration_id"])
  for integration_id in touched:
    try:
      reconcile(integration_id, transport=transport)
    except Exception:
      traceback.print_exc()
  return totals


# --- reconcile -------------------------------------------------------------------

def plan_reconcile(rows: list[dict], *, item_id_column: str, row_id_column: str, account_id_column: str,
                   account_items: dict[str, set[str]]) -> dict:
  """Pure: compare datasource rows with the item ids Stack holds per account.

  Returns {"synced": {account_id: {item_id: row_id}}, "orphans": [row, ...]}.
  An item is synced when some row's itemId equals its id. A row is an orphan
  when it carries an itemId, belongs to one of our accounts (or names none),
  and that id exists in none of them.
  """
  all_items = {item: account for account, items in account_items.items() for item in items}
  synced: dict[str, dict[str, str]] = {account: {} for account in account_items}
  orphans: list[dict] = []
  for row in rows:
    item_id = str(row.get(item_id_column) or "").strip().lower()
    if not item_id:
      continue
    row_id = str(row.get(row_id_column) or "").strip()
    row_account = str(row.get(account_id_column) or "").strip().lower()
    owner = all_items.get(item_id)
    if owner:
      synced[owner].setdefault(item_id, row_id)
    elif not row_account or row_account in account_items:
      orphans.append({"row_id": row_id, "item_id": item_id, "account_id": row_account or None, "row": row})
  return {"synced": synced, "orphans": orphans}


def _datasource_specs(integration: dict) -> list[dict]:
  """Where each datasource keeps its row key, the Stack item id, and the account id."""
  return [
    {"name": integration["main_datasource"], "item_id_column": integration["item_id_column"],
     "row_id_column": integration["row_id_column"], "account_id_column": integration["account_id_column"]},
    {"name": integration["device_datasource"], "item_id_column": "systemID", "row_id_column": "uniq",
     "account_id_column": "systemAccountID"},
  ]


def reconcile(integration_id: str, transport=None) -> dict:
  """Read the datasources back and set every item's (and incident's) sync status from them."""
  if not outbound_enabled():
    return {"enabled": False}
  with SessionLocal() as db:
    integration = _integration(db, integration_id)
    if not integration or not integration["enabled"]:
      return {"enabled": True, "skipped": "integration missing or disabled"}
    accounts = [r[0] for r in db.execute(text(
      "SELECT account_id::text FROM ilgforms_integration_accounts WHERE integration_id = :i"), {"i": integration_id}).all()]

  specs = _datasource_specs(integration)
  with _client(integration, transport) as client:
    sheets = {spec["name"]: client.get_rows(spec["name"]) for spec in specs}
    incident_rows = client.get_rows(integration["incident_datasource"])

  account_items: dict[str, set[str]] = {}
  for account_id in accounts:
    rls.ensure_item_sync_table(account_id)
    schema = rls._schema_name(account_id)
    with SessionLocal() as db:
      db.execute(rls.set_current_account(account_id))
      account_items[account_id] = {r[0] for r in db.execute(text(f"SELECT id::text FROM {schema}.items")).all()}

  plans = {spec["name"]: plan_reconcile(sheets[spec["name"]], item_id_column=spec["item_id_column"],
                                        row_id_column=spec["row_id_column"],
                                        account_id_column=spec["account_id_column"], account_items=account_items)
           for spec in specs}

  changes = {"newly_synced": 0, "no_longer_synced": 0}
  for account_id in accounts:
    schema = rls._schema_name(account_id)
    with SessionLocal() as db:
      db.execute(rls.set_current_account(account_id))
      current = {(r[0], r[1]): r[2] for r in db.execute(text(
        f"SELECT item_id::text, datasource, status FROM {schema}.item_sync")).all()}
      for datasource, plan in plans.items():
        synced = plan["synced"].get(account_id, {})
        for item_id, row_id in synced.items():
          if current.get((item_id, datasource)) != "synced":
            changes["newly_synced"] += 1
          db.execute(text(f"""
            INSERT INTO {schema}.item_sync (item_id, datasource, row_id, status, last_checked_at, last_synced_at)
            VALUES (:item, :ds, :row, 'synced', now(), now())
            ON CONFLICT (item_id, datasource) DO UPDATE SET row_id = EXCLUDED.row_id,
              status = 'synced', last_checked_at = now(), last_synced_at = now(), last_error = NULL
          """), {"item": item_id, "ds": datasource, "row": row_id or None})
        # Was synced, but that sheet no longer holds the id. Links still waiting on a queued
        # write stay "pending".
        lost = [item for (item, ds), status in current.items()
                if ds == datasource and status == "synced" and item not in synced]
        if lost:
          changes["no_longer_synced"] += len(lost)
          db.execute(text(f"""
            UPDATE {schema}.item_sync SET status = 'not_synced', last_checked_at = now()
            WHERE datasource = :ds AND item_id::text = ANY(:ids)
          """), {"ds": datasource, "ids": lost})
      db.execute(text(f"UPDATE {schema}.item_sync SET last_checked_at = now() WHERE status = 'pending'"))
      db.commit()

  with SessionLocal() as db:
    # Incidents: a section is synced when the incident list holds a row whose key is its slug.
    listed = {str(r.get("ID") or "").strip() for r in incident_rows} - {""}
    for account_id, slug in db.execute(text(
        "SELECT account_id::text, slug FROM sections WHERE account_id::text = ANY(:a)"), {"a": accounts}).all():
      if slug in listed:
        db.execute(text("""
          INSERT INTO ilgforms_section_links (account_id, section_slug, datasource, row_id, status, last_checked_at, last_synced_at)
          VALUES (:a, :s, :ds, :s, 'synced', now(), now())
          ON CONFLICT (account_id, section_slug, datasource) DO UPDATE
            SET status = 'synced', last_checked_at = now(), last_synced_at = now()
        """), {"a": account_id, "s": slug, "ds": integration["incident_datasource"]})
    db.execute(text("""
      UPDATE ilgforms_section_links SET status = 'not_synced', last_checked_at = now()
      WHERE datasource = :ds AND status = 'synced' AND account_id::text = ANY(:a) AND NOT (section_slug = ANY(:listed))
    """), {"ds": integration["incident_datasource"], "a": accounts, "listed": list(listed)})

    before = db.execute(text("SELECT count(*) FROM ilgforms_orphans WHERE integration_id = :i"),
                        {"i": integration_id}).scalar()
    orphan_total = 0
    for datasource, plan in plans.items():
      seen = [o["row_id"] for o in plan["orphans"] if o["row_id"]]
      orphan_total += len(seen)
      db.execute(text("""
        DELETE FROM ilgforms_orphans WHERE integration_id = :i AND datasource = :ds AND NOT (row_id = ANY(:seen))
      """), {"i": integration_id, "ds": datasource, "seen": seen})
      for orphan in plan["orphans"]:
        if not orphan["row_id"]:
          continue
        db.execute(text("""
          INSERT INTO ilgforms_orphans (integration_id, datasource, row_id, item_id, account_id, detail)
          VALUES (:i, :ds, :row, :item, :account, CAST(:detail AS jsonb))
          ON CONFLICT (integration_id, datasource, row_id) DO UPDATE
            SET item_id = EXCLUDED.item_id, account_id = EXCLUDED.account_id,
                detail = EXCLUDED.detail, last_seen_at = now()
        """), {"i": integration_id, "ds": datasource, "row": orphan["row_id"], "item": orphan["item_id"],
               "account": orphan["account_id"], "detail": json.dumps(orphan["row"])})
    db.execute(text("UPDATE ilgforms_integrations SET last_reconciled_at = now() WHERE id = :i"), {"i": integration_id})

    rows_total = sum(len(rows) for rows in sheets.values())
    result = {"enabled": True, "rows": rows_total,
              "synced": sum(len(s) for plan in plans.values() for s in plan["synced"].values()),
              "orphans": orphan_total, **changes}
    # Only log a pass that changed something: every 15 minutes would be noise.
    if changes["newly_synced"] or changes["no_longer_synced"] or before != orphan_total:
      _log(db, integration_id=integration_id, account_id=None, direction="received", event="reconcile",
           result="success",
           summary=(f"Checked {rows_total} rows across {', '.join(sheets)}: "
                    f"{changes['newly_synced']} newly synced, {changes['no_longer_synced']} no longer synced, "
                    f"{orphan_total} orphan rows"))
    db.commit()
  return result


def reconcile_all(transport=None) -> list[dict]:
  if not outbound_enabled():
    return []
  with SessionLocal() as db:
    ids = [r[0] for r in db.execute(text("SELECT id::text FROM ilgforms_integrations WHERE enabled")).all()]
  results = []
  for integration_id in ids:
    try:
      results.append({"integration_id": integration_id, **reconcile(integration_id, transport=transport)})
    except Exception as exc:  # noqa: BLE001
      traceback.print_exc()
      results.append({"integration_id": integration_id, "error": str(exc)[:500]})
  return results


# --- housekeeping + loop -------------------------------------------------------------

def prune_payloads() -> int:
  """Raw inbound bodies hold customer names and numbers: drop them after 30 days."""
  with SessionLocal() as db:
    count = db.execute(text(f"""
      UPDATE ilgforms_sync_log SET payload = NULL
      WHERE payload IS NOT NULL AND created_at < now() - interval '{PAYLOAD_RETENTION_DAYS} days'
    """)).rowcount
    db.execute(text("DELETE FROM ilgforms_inbound WHERE received_at < now() - interval '90 days'"))
    db.commit()
  return count


def _tick(state: dict):
  now = time.monotonic()
  with SessionLocal() as db:
    # Session-level advisory lock: if another API process holds it, skip this tick.
    if not db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
      return
    try:
      if now - state.get("prune", -PRUNE_SECONDS) >= PRUNE_SECONDS:
        state["prune"] = now
        prune_payloads()
      if outbound_enabled():
        run_due_jobs()
        if now - state.get("reconcile", -RECONCILE_SECONDS) >= RECONCILE_SECONDS:
          state["reconcile"] = now
          reconcile_all()
    finally:
      db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
      db.commit()


def _loop():
  state: dict = {}
  while True:
    try:
      _tick(state)
    except Exception:
      traceback.print_exc()
    time.sleep(JOB_POLL_SECONDS)


_started = False

def start_worker():
  """Start the background loop once per process. Pruning always runs; jobs and
  reconcile only when ILGFORMS_OUTBOUND is on."""
  global _started
  if _started or os.environ.get("ILGFORMS_WORKER", "on").strip().lower() in ("off", "0", "false", "no"):
    return
  _started = True
  threading.Thread(target=_loop, name="ilgforms-worker", daemon=True).start()
  print(f"[ilgforms] worker started (outbound {'ON' if outbound_enabled() else 'OFF'})", flush=True)
