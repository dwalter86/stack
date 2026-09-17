"""Live round trip against the real ILG Forms API: for each datasource, insert one
clearly marked test row, read it back, update a cell, delete it, and confirm it
is gone. Proves the NewRows / RowColumnUpdates / DeletedRows shapes this platform
uses. Every test row has a key starting ZZTEST- and is removed again.

Run via scripts/ilgforms_roundtrip_test.sh. The key stays in memory.
"""
import os, sys, time
from ilgforms_client import IlgFormsClient, IlgFormsError

KEY = os.environ["ILGFORMS_KEY"]
COMPANY = int(os.environ.get("ILG_COMPANY_ID", "50255"))
STAMP = time.strftime("%d%m%Y-%H%M%S")
PLAN = [  # datasource, label column, a column to update
  ("nflList", "incd", "postCode"),
  ("nfmain", "incd", "notes"),
  ("deviceDB", "incidNo", "comments"),
]
LABEL = "ZZ TEST ROW - safe to delete"
ok = True

def find(client, ds, key_col, key):
  return [r for r in client.get_rows(ds) if r.get(key_col) == key]

with IlgFormsClient(COMPANY, KEY) as client:
  for ds, label_col, edit_col in PLAN:
    print(f"\n[{ds}]")
    try:
      headers = client.get_headers(ds); key_col = headers[0]; key = f"ZZTEST-{STAMP}-{ds}"
      before = len(client.get_rows(ds))
      client.insert_row(ds, {key_col: key, label_col: LABEL}, headers=headers)
      rows = find(client, ds, key_col, key)
      print(f"  insert : {'PASS' if len(rows) == 1 and rows[0].get(label_col) == LABEL else 'FAIL'}  (rows {before} -> {len(client.get_rows(ds))})")
      ok &= len(rows) == 1
      client.update_cells(ds, key, {edit_col: "updated by round-trip test"})
      rows = find(client, ds, key_col, key)
      good = bool(rows) and rows[0].get(edit_col) == "updated by round-trip test"
      print(f"  update : {'PASS' if good else 'FAIL'}"); ok &= good
      client.delete_rows(ds, [key])
      gone = not find(client, ds, key_col, key)
      if not gone:   # the schema allows a full row too: try that before giving up
        print("  delete : key-only form did not remove the row, trying the full-row form")
        client._send("PUT", json={"ExternalId": ds, "DeletedRows": [[str(rows[0].get(h) or "") for h in headers]],
                                  "CompanyId": COMPANY, "IntegrationKey": KEY})
        gone = not find(client, ds, key_col, key)
        print("           full-row form", "WORKED: tell Claude, the client needs that form" if gone else "also failed")
      after = len(client.get_rows(ds))
      print(f"  delete : {'PASS' if gone else 'FAIL - row ' + key + ' is still in ' + ds + ', remove it by hand'}  (rows now {after}, started at {before})")
      ok &= gone and after == before
    except IlgFormsError as exc:
      ok = False
      print(f"  ERROR  : {exc}")
print("\nRESULT:", "PASS" if ok else "CHECK THE OUTPUT ABOVE")
sys.exit(0 if ok else 1)
