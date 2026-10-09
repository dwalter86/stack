"""End-to-end run of the ILG Forms integration against an in-memory fake of ILG Forms.

Runs INSIDE the api container against the local stack's real database and HTTP API; only the
calls to ILG Forms are faked, so nothing leaves the machine. It provisions its own account and
integration and removes them afterwards.

    scripts/ilgforms_e2e.sh          (wraps: docker compose exec ... python /tests/e2e_ilgforms.py)

Every scenario here was either a requirement or a fault found on staging / production.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
os.environ["ILGFORMS_OUTBOUND"] = "on"   # this process only; the fake transport is passed explicitly

import httpx
from sqlalchemy import text

import ilgforms_jobs as jobs
from database import SessionLocal
from fake_ilgforms import FakeIlgForms

KEY, COMPANY = "e2e-key", 999001
STAMP = time.strftime("%d%m%Y-%H%M%S")
api = httpx.Client(base_url="http://localhost:8000", timeout=30,
                   headers={"Authorization": "Bearer " + os.environ["TOKEN"], "X-Update-Source": "web-ui"})
fake = FakeIlgForms(key=KEY)
T = fake.transport()
ok, passed = True, 0


def check(label, cond, extra=""):
  global ok, passed
  ok = ok and bool(cond)
  passed += bool(cond)
  print(("  PASS " if cond else "  FAIL ") + label, "" if cond else extra)


def run():
  """Run the queue now (writebacks from forms normally wait ~20s first)."""
  with SessionLocal() as db:
    db.execute(text("UPDATE ilgforms_jobs SET next_attempt_at = now() WHERE status = 'pending'"))
    db.commit()
  return jobs.run_due_jobs(limit=500, transport=T)


def form(path, body):
  return httpx.post("http://localhost:8000/api/integrations/ilgforms/" + path, json=body, timeout=30)


def entry(n, answers, **extra):
  return {"ProviderId": COMPANY, "IntegrationKey": KEY,
          "Entry": {"Id": f"{n:032x}", "UserFirstName": "Test", "UserLastName": "Engineer", "AnswersJson": answers, **extra}}


def items(slug):
  return api.get(f"/api/accounts/{ACC}/sections/{slug}/items?limit=200").json()["items"]


def sync(item_id):
  return api.get(f"/api/accounts/{ACC}/items/{item_id}/sync-status").json()["state"]


def sheet(name, **where):
  return [r for r in fake.rows(name) if all(r.get(k) == v for k, v in where.items())]


# ---------------------------------------------------------------- setup
ACC = api.post("/api/accounts", json={"name": f"E2E {STAMP}"}).json()["id"]
with SessionLocal() as db:
  db.execute(text("DELETE FROM ilgforms_integrations WHERE company_id = :c"), {"c": COMPANY})
  INTEG = db.execute(text("""
    INSERT INTO ilgforms_integrations (name, company_id, integration_key) VALUES ('E2E', :c, :k) RETURNING id::text
  """), {"c": COMPANY, "k": KEY}).scalar()
  db.commit()
fake.add("engineers", id="1", department="Electrical", name="Eng One", email1="one@example.com")
fake.add("engineers", id="2", department="Gas", name="Eng Two", email1="two@example.com")
SLUG = f"E2E1-{STAMP}-00000001"

try:
  print("1. Accounts and the account list")
  run()
  check("a new account is sent nowhere until it is linked", not sheet("accountList"))
  api.post(f"/api/admin/ilgforms/integrations/{INTEG}/accounts", json={"account_id": ACC})
  run()
  check("linking adds it to the account list", len(sheet("accountList", **{"Answer Value": ACC})) == 1)
  api.put(f"/api/accounts/{ACC}", json={"name": "E2E Renamed"})
  run()
  check("renaming updates the list", sheet("accountList", **{"Answer Value": ACC})[0]["Display Text"] == "E2E Renamed")
  new_id = "eeeeeeee-eeee-4eee-8eee-" + time.strftime("%H%M%S") + "000000"
  fake.add("accountList", **{"Answer Value": new_id, "Display Text": "Made In ILG Forms"})
  jobs.reconcile(INTEG, transport=T)
  check("a new row in the list is ignored while accepting new accounts is off",
        not [a for a in api.get("/api/me/accounts").json() if a["id"] == new_id])

  print("2. Create an incident and items in the web platform")
  sec = api.post(f"/api/accounts/{ACC}/sections", json={"slug": SLUG, "label": "E2E Street", "detail": "a note", "schema": {}}).json()
  eng = [f for f in sec["schema"]["fields"] if f["key"] == "engineer"]
  check("new incident gets the standard layout", len(sec["schema"]["fields"]) >= 16)
  check("Engineer dropdown comes from the ILG Forms engineers list", eng and list(eng[0]["options"].values()) == ["", "Eng One", "Eng Two"],
        eng and eng[0].get("options"))
  fake.fail_next = 1
  check("datasource lock (500) on the first write: retried, not failed", run()["failed"] == 0)
  run()
  row = sheet("nflList", ID=SLUG)
  check("incident row added, no post code or address yet", len(row) == 1 and row[0]["account"] == ACC and row[0]["postCode"] == "" and row[0]["address"] == "", row)

  prop = api.post(f"/api/accounts/{ACC}/sections/{SLUG}/items", json={"name": "Mrs Test", "data": {
    "houseNo": "7", "address": "E2E Street", "postcode": "TE5 7ST", "telephone": "07000000001", "status": "Faults"}}).json()
  both = api.post(f"/api/accounts/{ACC}/sections/{SLUG}/items", json={"name": "David Smith", "data": {
    "houseNo": "12", "address": "E2E Street", "postcode": "TE5 7ST", "itemMake": "Sony", "engineer": "Eng Two", "status": ""}}).json()
  second = api.post(f"/api/accounts/{ACC}/sections/{SLUG}/items", json={"name": "Mrs Test", "data": {
    "houseNo": "7", "postcode": "TE5 7ST", "itemMake": "Bosch"}}).json()
  run()
  inc = sheet("nflList", ID=SLUG)[0]
  check("incident row took post code and address from its first item", (inc["postCode"], inc["address"]) == ("TE5 7ST", "E2E Street"), inc)
  check("user's Detail text left alone", api.get(f"/api/accounts/{ACC}/sections/{SLUG}").json()["detail"] == "a note")
  check("property item has a property row", len(sheet("nfmain", itemId=prop["id"])) == 1)
  check("item with appliance details gets a property row AND an appliance row",
        len(sheet("nfmain", itemId=both["id"])) == 1 and len(sheet("deviceDB", systemID=both["id"])) == 1)
  check("engineer email on the appliance row comes from the engineers list", sheet("deviceDB", systemID=both["id"])[0]["eEmail"] == "two@example.com")
  check("second appliance at house 7 did not duplicate the property row", len(sheet("nfmain", incdId=SLUG, houseNo="7")) == 1)
  check("items show synced once the sheets are read back", sync(prop["id"])["status"] == "synced" and sync(both["id"])["status"] == "synced")

  api.put(f"/api/accounts/{ACC}/items/{both['id']}", json={"data": {"status": "Repaired", "itemModel": "A1"}})
  api.put(f"/api/accounts/{ACC}/items/{both['id']}", json={"data": {"itemModel": "A2"}})
  run()
  dev = sheet("deviceDB", systemID=both["id"])[0]
  check("edit reaches the appliance row, latest edit wins", (dev["Repair Status"], dev["Model"]) == ("Repaired", "A2"), dev)
  check("repair status does not overwrite the property's first-visit outcome", sheet("nfmain", itemId=both["id"])[0]["initialVisit"] == "Faults")

  print("3. The sheet already holds rows the platform does not know about")
  fake.add("deviceDB", uniq="ENG9-01012026-100000-00000001", systemSectionID=SLUG, houseNoName="9", Make="Hoover", ApplianceType="Dryer", systemID="")
  dryer = api.post(f"/api/accounts/{ACC}/sections/{SLUG}/items", json={"name": "Mr Nine", "data": {"houseNo": "9", "postcode": "TE5 7ST", "itemMake": "Hoover", "itemApplianceType": "Dryer"}}).json()
  run()
  d9 = sheet("deviceDB", systemSectionID=SLUG, houseNoName="9")
  check("appliance the engineer's form already wrote is linked, not duplicated", len(d9) == 1 and d9[0]["uniq"].startswith("ENG9") and d9[0]["systemID"] == dryer["id"], d9)
  fake.add("nfmain", ID="FORM-01012026-100000-00000020", houseNo="20", customerName="From The Form", incdId=SLUG, accountId=ACC, itemId="")
  fake.add("nfmain", ID="FORM-01012026-100000-00000030", houseNo="30", incdId=SLUG, accountId=ACC, itemId="99999999-9999-4999-8999-999999999999")
  h20 = api.post(f"/api/accounts/{ACC}/sections/{SLUG}/items", json={"name": "Web Person", "data": {"houseNo": "20", "postcode": "TE5 7ST"}}).json()
  api.put(f"/api/accounts/{ACC}/items/{h20['id']}", json={"data": {"telephone": "07111111111"}})
  h30 = api.post(f"/api/accounts/{ACC}/sections/{SLUG}/items", json={"name": "Another", "data": {"houseNo": "30", "postcode": "TE5 7ST"}}).json()
  run()
  r20 = sheet("nfmain", incdId=SLUG, houseNo="20")
  check("blank row written by the form is reused, not duplicated", len(r20) == 1 and r20[0]["ID"].startswith("FORM-") and r20[0]["itemId"] == h20["id"])
  check("an edit queued behind it lands on the reused row", r20[0]["teleNo1"] == "07111111111")
  check("a row owned by another item blocks a second row", len(sheet("nfmain", incdId=SLUG, houseNo="30")) == 1 and sync(h30["id"])["status"] == "not_synced")

  print("4. Forms calling the platform")
  loc = {"uniq": "JJ01-01012026-160000-00000055", "uniq_up": "JJ01-01012026-160000-00000055", "customersName": "James Jones", "houseNo": "55",
         "streetName": "E2E Street", "postCodeIn": "TE5 7ST", "telephoneNumber1": "07000000055", "initialVisit": "Faults", "itemId": ""}
  nameless = {"uniq": "NL01-01012026-105916-00000001", "uniq_up": "NL01-01012026-105916-00000001", "customersName": "", "houseNo": "Flat 1",
              "streetName": "E2E Street", "postCodeIn": "TE5 7ST", "telephoneNumber1": "", "initialVisit": "No Faults", "itemId": ""}
  for l in (loc, nameless):
    fake.add("nfmain", ID=l["uniq_up"], houseNo=l["houseNo"], incdId=SLUG, accountId=ACC, itemId="")
  page1 = {"ID": SLUG, "account": ACC, "incd": "E2E Street", "postCode": "TE5 7ST", "locations": [loc, nameless]}
  r = form("incident", entry(1, {"page1": page1})).json()
  jj = r["locations"][0]["item_id"]
  check("incident form creates the properties", r["counts"].get("created") == 2, r.get("counts"))
  check("identical resubmission is ignored", form("incident", entry(1, {"page1": page1})).json().get("duplicate") is True)
  for d in ("JJD1", "JJD2"):
    fake.add("deviceDB", uniq=f"{d}-01012026-160100-00000001", systemAccountID=ACC, systemSectionID=SLUG)
  devices = {"page1": {"accountid": ACC, "incdid": SLUG, "customersName": "James Jones", "houseNoName": "55", "address": "E2E Street", "postCode": "TE5 7ST"},
             "deviceReview": {"devices": [
               {"uniq": "0", "uniq_up": "JJD1-01012026-160100-00000001", "make": "JVC", "applianceType": "Stereo", "repairStatus": "", "photo": "a.jpg", "comments": "Noisy", "systemID": ""},
               {"uniq": "0", "uniq_up": "JJD2-01012026-160100-00000001", "make": "Sony", "applianceType": "TV", "repairStatus": "In Progress", "systemID": ""}]}}
  r = form("devices", entry(2, devices, DSRowId="01234567-89ab-cdef-0123-456789abcdef")).json()
  run()
  one = api.get(f"/api/accounts/{ACC}/items/{jj}").json()
  check("first appliance joins the property item, second gets its own", r["counts"].get("linked") == 1 and r["counts"].get("created") == 1
        and len([i for i in items(SLUG) if str(i["data"].get("houseNo")) == "55"]) == 2, r.get("counts"))
  check("status became the appliance's (blank) repair status, not the property's Faults", one["data"].get("status") == "", one["data"].get("status"))
  check("photo link built and comment added once", one["data"].get("itemPhoto", "").endswith("a.jpg") and one["data"]["itemPhoto"].startswith("https://")
        and len(api.get(f"/api/accounts/{ACC}/items/{jj}/comments").json()) == 1)
  check("one id on both sheets", sheet("nfmain", ID=loc["uniq_up"])[0]["itemId"] == jj and sheet("deviceDB", uniq="JJD1-01012026-160100-00000001")[0]["systemID"] == jj)
  ids = []
  for n in range(3, 7):   # the office edits the incident form all day; the app's itemId column lags behind
    loc["notes"] = f"edit {n}"
    r = form("incident", entry(n, {"page1": page1})).json()
    ids.append(r["locations"][1]["item_id"])
  check("resubmitted with blank item ids four times: nameless flat is still ONE item", len(set(ids)) == 1
        and len([i for i in items(SLUG) if i["data"].get("houseNo") == "Flat 1"]) == 1, len(set(ids)))
  check("...and it did not put Faults back on James Jones", api.get(f"/api/accounts/{ACC}/items/{jj}").json()["data"].get("status") == "")
  fake.add("deviceDB", uniq="NEW7-01012026-170000-00000001", systemAccountID=ACC, systemSectionID=SLUG)
  r = form("devices", entry(9, {"page1": {"accountid": ACC, "incdid": SLUG, "customersName": "Mrs Fort", "houseNoName": "77", "address": "E2E Street", "postCode": "TE5 7ST"},
                                "deviceReview": {"devices": [{"uniq": "0", "uniq_up": "NEW7-01012026-170000-00000001", "make": "Baxi", "applianceType": "Boiler", "repairStatus": "In Progress", "systemID": ""}]}})).json()
  run()
  r77 = sheet("nfmain", incdId=SLUG, houseNo="77")
  check("appliance logged at a house the office never entered: the house gets a property row (Stonesdale)",
        len(r77) == 1 and r77[0]["itemId"] == r["devices"][0]["item_id"] and r77[0]["customerName"] == "Mrs Fort" and r77[0]["initialVisit"] == "Faults", r77)
  r = form("devices", entry(10, {"page1": {"accountid": ACC, "incdid": SLUG, "customersName": "Mrs Fort", "houseNoName": "77", "address": "E2E Street", "postCode": "TE5 7ST"},
                                 "deviceReview": {"devices": [{"uniq": "0", "uniq_up": "NEW8-01012026-170100-00000001", "make": "Bosch", "applianceType": "Oven", "systemID": ""}]}})).json()
  fake.add("deviceDB", uniq="NEW8-01012026-170100-00000001", systemAccountID=ACC, systemSectionID=SLUG)
  run()
  check("a second appliance at that house does not add a second property row", len(sheet("nfmain", incdId=SLUG, houseNo="77")) == 1)
  r = form("engineer-update", entry(7, {"deviceReview": {"uniq": "JJD1-01012026-160100-00000001", "systemID": jj, "systemAccountID": ACC,
                                                        "status": "Repaired on Site", "deviceStatus": "Repaired", "comments": "Replaced relay"}})).json()
  one = api.get(f"/api/accounts/{ACC}/items/{jj}").json()
  check("engineer update applied", r.get("action") == "updated" and one["data"]["status"] == "Repaired" and one["data"]["reportStatus"] == "Repaired on Site")
  r = form("engineer-update", entry(8, {"deviceReview": {"systemID": "11111111-2222-4333-8444-555555555555", "systemAccountID": ACC, "deviceStatus": "Repaired"}}))
  check("engineer update for a deleted item is skipped, not an error", r.status_code == 200 and r.json().get("action") == "skipped")
  check("wrong key is refused", form("incident", {"ProviderId": COMPANY, "IntegrationKey": "nope", "Entry": {}}).status_code == 401)
  run()

  print("5. A busy sheet, batched writebacks, and an appliance form that beats its incident")
  S2 = f"E2E2-{STAMP}-00000002"
  locs = [{"uniq": f"BZ0{n}-01012026-120000-0000000{n}", "uniq_up": f"BZ0{n}-01012026-120000-0000000{n}", "customersName": f"Person {n}", "houseNo": str(50 + n),
           "streetName": "Sorby Way", "postCodeIn": "TE5 7ST", "telephoneNumber1": f"0700000010{n}", "initialVisit": "Faults", "itemId": ""} for n in range(1, 5)]
  for l in locs:
    fake.add("nfmain", ID=l["uniq_up"], houseNo=l["houseNo"], incdId=S2, accountId=ACC, itemId="")
  fake.add("deviceDB", uniq="BZD1-01012026-120300-00000001", systemAccountID=ACC, systemSectionID=S2)
  r = form("devices", entry(20, {"page1": {"accountid": ACC, "incdid": S2, "refNo": "Busy Test", "customersName": "Person 1", "houseNoName": "51",
                                           "address": "Sorby Way", "postCode": "TE5 7ST"},
                                 "deviceReview": {"devices": [{"uniq": "0", "uniq_up": "BZD1-01012026-120300-00000001", "make": "Ninja", "applianceType": "Toaster", "systemID": ""}]}}))
  check("appliance form before its incident exists: accepted, incident created (was a 400)", r.status_code == 200 and r.json()["counts"].get("created") == 1, r.text[:150])
  busy_page = {"ID": S2, "account": ACC, "incd": "Busy Test", "postCode": "TE5 7ST", "address": "Sorby Way", "locations": locs}
  r = form("incident", entry(21, {"page1": busy_page})).json()
  check("incident form then finds it: 3 created, house 51 linked to the appliance item", r["section_created"] is False
        and r["counts"].get("created") == 3 and r["counts"].get("linked") == 1, r.get("counts"))
  with SessionLocal() as db:
    waiting = db.execute(text("SELECT count(*) FROM ilgforms_jobs WHERE status = 'pending' AND section_slug = :s AND next_attempt_at > now() + interval '5 seconds'"), {"s": S2}).scalar()
  check("writebacks from a form wait ~20s so ILG Forms can finish its own update", waiting >= 4, waiting)
  fake.busy_next = 2   # one batched property call + one appliance call
  out = run()
  check("sheet busy (the 29 Sep 400): writebacks retry instead of failing", out["failed"] == 0 and out["retry"] >= 4, out)
  before = len(fake.calls)
  out = run()
  nf = [c for c in fake.calls[before:] if c["ExternalId"] == "nfmain" and c.get("RowColumnUpdates")]
  check("after the wait they all go through", out["failed"] == 0 and out["retry"] == 0, out)
  check("the property writebacks went as ONE batched call", any(len(c["RowColumnUpdates"]) >= 3 for c in nf), [len(c["RowColumnUpdates"]) for c in nf])
  check("every row got its item id", all(sheet("nfmain", ID=l["uniq_up"])[0]["itemId"] for l in locs))
  fake.sheets["nfmain"][:] = [r for r in fake.sheets["nfmain"] if r[0] != locs[1]["uniq_up"]]
  for l in locs:
    l["notes"] = "again"
  form("incident", entry(22, {"page1": busy_page}))
  out = run()
  check("one row missing from the sheet: only that writeback fails, the rest still go", out["failed"] == 1 and out["done"] >= 3, out)
  with SessionLocal() as db:
    db.execute(text("DELETE FROM ilgforms_jobs WHERE section_slug = :s AND status = 'failed'"), {"s": S2})
    stuck = db.execute(text("""
      SELECT count(*) FROM ilgforms_sync_log l WHERE l.account_id = :a AND l.result = 'pending'
        AND NOT EXISTS (SELECT 1 FROM ilgforms_jobs j WHERE j.log_id = l.id)"""), {"a": ACC}).scalar()
    db.commit()
  check("no log rows left stuck as pending", stuck == 0, stuck)

  print("6. Deleting")
  api.delete(f"/api/accounts/{ACC}/items/{second['id']}")
  quick = api.post(f"/api/accounts/{ACC}/sections/{SLUG}/items", json={"name": "Never sent", "data": {"houseNo": "99"}}).json()
  api.delete(f"/api/accounts/{ACC}/items/{quick['id']}")
  before = len(fake.calls)
  run()
  check("deleted item's appliance row removed", not sheet("deviceDB", systemID=second["id"]))
  check("item added and deleted before it was sent: nothing sent for it", not [c for c in fake.calls[before:] if quick["id"] in json.dumps(c)])
  for s in (SLUG, S2):
    api.delete(f"/api/accounts/{ACC}/sections/{s}")
  run()
  left = [r for n in ("nflList", "nfmain", "deviceDB") for r in fake.rows(n)
          if (SLUG in r.values() or S2 in r.values()) and not str(r.get("itemId") or "").startswith("9999")]
  check("deleting the incidents removed their incident, property and appliance rows", not left, [list(r.values())[:3] for r in left])
  check("the row that belongs to someone else's item was left alone", len(sheet("nfmain", ID="FORM-01012026-100000-00000030")) == 1)
  api.delete(f"/api/accounts/{ACC}")
  run()
  check("deleting the account removed it from the account list", not sheet("accountList", **{"Answer Value": ACC}))
  with SessionLocal() as db:
    bad = db.execute(text("SELECT kind, datasource, status, left(last_error, 80) FROM ilgforms_jobs WHERE integration_id = :i AND status <> 'done'"), {"i": INTEG}).all()
  check("no jobs left pending or failed", not bad, bad)
finally:
  api.delete(f"/api/accounts/{ACC}")
  with SessionLocal() as db:
    db.execute(text("DELETE FROM ilgforms_sync_log WHERE integration_id = :i"), {"i": INTEG})
    db.execute(text("DELETE FROM ilgforms_integrations WHERE id = :i"), {"i": INTEG})
    db.commit()

print(f"\nRESULT: {'ALL PASS' if ok else 'FAILURES ABOVE'} ({passed} checks passed)")
sys.exit(0 if ok else 1)
