"""Live smoke test: one write to ONE nominated row of an ILG Forms datasource,
through the real job runner. It reads the row, writes the row's CURRENT itemId
back unchanged, and reads it again, so the sheet's data does not change.

Run via scripts/ilgforms_live_test.sh (it executes this inside the api container).
The key is taken from the environment, used in memory, and never stored.
"""
import json, os, sys
from sqlalchemy import text
from database import SessionLocal
import ilgforms_jobs as jobs
from ilgforms_client import IlgFormsClient
from ilgforms_sync import _log, JOB_WRITE_ITEM_ID

ROW_ID = os.environ["ILG_ROW_ID"]
KEY = os.environ["ILGFORMS_KEY"]
COMPANY = int(os.environ.get("ILG_COMPANY_ID", "50255"))
DATASOURCE = os.environ.get("ILG_DATASOURCE", "nfmain")

def read_row():
  with IlgFormsClient(COMPANY, KEY) as client:
    rows = client.get_rows(DATASOURCE)
  return rows, [r for r in rows if r.get("ID") == ROW_ID]

rows, match = read_row()
print(f"1. READ  {DATASOURCE}: {len(rows)} rows")
if len(match) != 1:
  sys.exit(f"ABORT: expected exactly one row with ID {ROW_ID}, found {len(match)}. Nothing written.")
before = match[0]
current = (before.get("itemId") or "").strip()
print(f"   row {ROW_ID}: incd={before.get('incd')!r} houseNo={before.get('houseNo')!r} itemId={current!r}")
if not current:
  sys.exit("ABORT: that row has no itemId, so there is no harmless value to write back. Nothing written.")

with SessionLocal() as db:
  if db.execute(text("select count(*) from ilgforms_jobs where status in ('pending','running')")).scalar():
    sys.exit("ABORT: other jobs are queued in this database; refusing to risk running them. Nothing written.")
  integ = db.execute(text("select id::text from ilgforms_integrations where company_id=:c"), {"c": COMPANY}).scalar()
  if not integ:
    sys.exit("ABORT: no integration configured for that company id. Nothing written.")
  log_id = _log(db, integration_id=integ, account_id=None, direction="sent", event="itemid.writeback",
                result="pending", row_id=ROW_ID, summary=f"LIVE TEST: itemId queued for {DATASOURCE}")
  db.execute(text("""insert into ilgforms_jobs (integration_id, account_id, kind, payload, log_id)
                     values (:i, '00000000-0000-0000-0000-000000000000', :k, cast(:p as jsonb), :l)"""),
             {"i": integ, "k": JOB_WRITE_ITEM_ID, "l": log_id,
              "p": json.dumps({"external_id": DATASOURCE, "row_id": ROW_ID, "column": "itemId", "value": current})})
  db.commit()
  integration = jobs._integration(db, integ)
integration["integration_key"] = KEY   # memory only

claimed = jobs.claim_due_jobs(limit=5)
if len(claimed) != 1 or claimed[0]["payload"]["row_id"] != ROW_ID:
  sys.exit(f"ABORT: claimed unexpected jobs {claimed!r}")
print("2. WRITE via job runner:", jobs.run_job(claimed[0], integration))
with SessionLocal() as db:
  print("   job:", tuple(db.execute(text("select status, attempts, last_error from ilgforms_jobs order by created_at desc limit 1")).first()))
  print("   log:", tuple(db.execute(text("select result, summary, error from ilgforms_sync_log where id=:l"), {"l": log_id}).first()))

rows2, match2 = read_row()
after = match2[0] if len(match2) == 1 else {}
differs = sorted(k for k in before if before.get(k) != after.get(k))
print(f"3. READ  again: {len(rows2)} rows; itemId={after.get('itemId')!r}; columns that changed: {differs or 'none'}")
print("RESULT:", "PASS" if after.get("itemId") == before.get("itemId") and not differs and len(rows2) == len(rows) else "CHECK THE OUTPUT ABOVE")
