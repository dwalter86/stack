"""Tests for the ILG Forms client and the pure reconcile planner.

Needs httpx + sqlalchemy, so run inside the API image:
    docker compose run --rm -v "$PWD/api/tests:/tests" api python -m unittest discover /tests
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

try:
  import httpx
  from ilgforms_client import IlgFormsClient, IlgFormsError, IlgFormsRetryable
  import ilgforms_jobs as jobs
  HAVE_DEPS = True
except Exception:  # noqa: BLE001 - running outside the API image
  HAVE_DEPS = False


@unittest.skipUnless(HAVE_DEPS, "needs the API image (httpx, sqlalchemy)")
class ClientTests(unittest.TestCase):
  def client(self, handler):
    return IlgFormsClient(50255, "k3y", base_url="https://ilg.test/api/v2", transport=httpx.MockTransport(handler))

  def test_update_cells_sends_the_shape_ilg_forms_expects(self):
    seen = {}
    def handler(request):
      seen["method"], seen["path"], seen["body"] = request.method, request.url.path, json.loads(request.content)
      return httpx.Response(200, json={})
    self.client(handler).update_cells("nfmain", "ROW-1", {"itemId": "abc"})
    self.assertEqual((seen["method"], seen["path"]), ("PUT", "/api/v2/datasource"))
    self.assertEqual(seen["body"], {
      "ExternalId": "nfmain", "CompanyId": 50255, "IntegrationKey": "k3y",
      "RowColumnUpdates": [{"RowId": "ROW-1", "ColumnUpdates": [{"Column": "itemId", "Value": "abc"}]}]})

  def test_get_rows_zips_headers(self):
    def handler(request):
      self.assertEqual(request.url.params["ExternalId"], "nfmain")
      self.assertEqual(request.url.params["ReturnRows"], "true")
      return httpx.Response(200, json={"DataSource": {"Headers": [{"Name": "ID"}, {"Name": "itemId"}],
                                                     "Rows": [["R1", "a"], ["R2", ""]]}})
    self.assertEqual(self.client(handler).get_rows("nfmain"), [{"ID": "R1", "itemId": "a"}, {"ID": "R2", "itemId": ""}])

  def test_cache_lock_500_is_retryable(self):
    handler = lambda request: httpx.Response(500, text='{"ResponseStatus":{"ErrorCode":"CacheLockException"}}')
    with self.assertRaises(IlgFormsRetryable):
      self.client(handler).update_cells("nfmain", "ROW-1", {"itemId": "abc"})

  def test_400_is_not_retryable(self):
    handler = lambda request: httpx.Response(400, text="No Rows Found")
    with self.assertRaises(IlgFormsError) as ctx:
      self.client(handler).update_cells("nfmain", "ROW-1", {"itemId": "abc"})
    self.assertNotIsInstance(ctx.exception, IlgFormsRetryable)

  def test_missing_row_id_never_reaches_the_network(self):
    def handler(request):
      raise AssertionError("should not be called")
    with self.assertRaises(IlgFormsError):
      self.client(handler).update_cells("nfmain", "", {"itemId": "abc"})


@unittest.skipUnless(HAVE_DEPS, "needs the API image (httpx, sqlalchemy)")
class ReconcilePlanTests(unittest.TestCase):
  COLS = dict(item_id_column="itemId", row_id_column="ID", account_id_column="accountId")

  def test_synced_orphan_and_blank(self):
    rows = [
      {"ID": "R1", "itemId": "AAA", "accountId": "acc1"},    # live item -> synced (case-insensitive)
      {"ID": "R2", "itemId": "dead", "accountId": "acc1"},   # our account, no such item -> orphan
      {"ID": "R3", "itemId": "", "accountId": "acc1"},       # blank -> nothing
      {"ID": "R4", "itemId": "zzz", "accountId": "other"},   # someone else's account -> ignored
      {"ID": "R5", "itemId": "bbb", "accountId": "acc1"},    # item lives in acc2: still synced, under acc2
    ]
    plan = jobs.plan_reconcile(rows, account_items={"acc1": {"aaa"}, "acc2": {"bbb"}}, **self.COLS)
    self.assertEqual(plan["synced"], {"acc1": {"aaa": "R1"}, "acc2": {"bbb": "R5"}})
    self.assertEqual([o["row_id"] for o in plan["orphans"]], ["R2"])

  def test_outbound_is_off_by_default(self):
    os.environ.pop("ILGFORMS_OUTBOUND", None)
    self.assertFalse(jobs.outbound_enabled())
    self.assertEqual(jobs.run_due_jobs(), {"enabled": False})


if __name__ == "__main__":
  unittest.main()


@unittest.skipUnless(HAVE_DEPS, "needs the API image (httpx, sqlalchemy)")
class InsertDeletePagingTests(unittest.TestCase):
  def setUp(self):
    sys.path.insert(0, os.path.dirname(__file__))
    from fake_ilgforms import FakeIlgForms
    self.fake = FakeIlgForms(key="k3y", max_page=2)
    self.client = IlgFormsClient(50255, "k3y", base_url="https://ilg.test/api/v2", transport=self.fake.transport())

  def test_insert_lays_values_out_in_header_order_and_blanks_the_rest(self):
    self.client.insert_row("nflList", {"account": "acc", "ID": "S1", "incd": "Street"})
    self.assertEqual(self.fake.sheets["nflList"], [["S1", "Street", "", "", "", "acc", ""]])
    self.assertEqual(self.fake.calls[-1]["NewRows"], [["S1", "Street", "", "", "", "acc", ""]])

  def test_insert_refuses_unknown_column_and_missing_key(self):
    with self.assertRaises(IlgFormsError):
      self.client.insert_row("nflList", {"ID": "S1", "nope": "x"})
    with self.assertRaises(IlgFormsError):
      self.client.insert_row("nflList", {"incd": "no key"})

  def test_delete_sends_row_keys(self):
    self.fake.add("nflList", ID="S1"); self.fake.add("nflList", ID="S2")
    self.client.delete_rows("nflList", ["S1"])
    self.assertEqual(self.fake.calls[-1]["DeletedRows"], [["S1"]])
    self.assertEqual([r["ID"] for r in self.fake.rows("nflList")], ["S2"])

  def test_get_rows_reads_every_page(self):
    for i in range(5):
      self.fake.add("nflList", ID=f"S{i}")
    self.assertEqual([r["ID"] for r in self.client.get_rows("nflList", page_size=2)], ["S0", "S1", "S2", "S3", "S4"])


@unittest.skipUnless(HAVE_DEPS, "needs the API image (httpx, sqlalchemy)")
class ColumnMapTests(unittest.TestCase):
  def test_appliance_vs_property(self):
    import ilgforms_outbound as out
    self.assertTrue(out.is_appliance({"itemMake": "Bosch"}))
    self.assertFalse(out.is_appliance({"houseNo": "7", "status": "Faults", "itemMake": " "}))

  def test_device_columns(self):
    import ilgforms_outbound as out
    item = {"id": "i1", "name": "Mrs T", "data": {"houseNo": "7", "itemPrice": "£250", "engineer": "Carl", "status": "Repaired", "reportStatus": "Repaired on Site"}}
    cols = out.device_columns(item, "acc", {"slug": "S1", "label": "Street"}, {"Carl": "carl@example.com"}, for_insert=True)
    self.assertEqual((cols["uniq"], cols["systemID"], cols["systemSectionID"], cols["incidNo"]), ("i1", "i1", "S1", "Street"))
    self.assertEqual((cols["ApproxPrice"], cols["eEmail"], cols["Repair Status"], cols["Status"]), ("250", "carl@example.com", "Repaired", "Repaired on Site"))
    self.assertNotIn("uniq", out.device_columns(item, "acc", {}, {}, for_insert=False))

  def test_new_row_id_shape(self):
    import ilgforms_outbound as out
    self.assertRegex(out.new_row_id(), r"^[A-Z0-9]{4}-\d{8}-\d{6}-\d{8}$")

  def test_property_columns_first_visit_outcome(self):
    import ilgforms_outbound as out
    sec = {"slug": "S1", "label": "Street"}
    appliance = {"id": "i1", "name": "D Smith", "data": {"houseNo": 12, "itemMake": "Sony", "status": ""}}
    cols = out.property_columns(appliance, "acc", sec, for_insert=True, row_id="R1", has_appliance=True)
    self.assertEqual((cols["ID"], cols["itemId"], cols["incdId"], cols["houseNo"], cols["initialVisit"]), ("R1", "i1", "S1", "12", "Faults"))
    # a repair status belongs to the device sheet: it must not overwrite the first-visit outcome
    appliance["data"]["status"] = "Repaired"
    self.assertNotIn("initialVisit", out.property_columns(appliance, "acc", sec, for_insert=False, has_appliance=True))
    # a property-only item keeps the old behaviour: its status is the sheet's initialVisit
    prop = {"id": "i2", "name": "P", "data": {"status": "No Faults"}}
    self.assertEqual(out.property_columns(prop, "acc", sec, for_insert=False)["initialVisit"], "No Faults")

  def test_new_incident_row_has_no_post_code_or_address(self):
    # They are not typed on the incident: the first item to have them fills them in.
    import ilgforms_outbound as out
    cols = out.incident_columns("acc", {"slug": "S1", "label": "1239494-DW", "detail": "free text a user typed"})
    self.assertEqual((cols["ID"], cols["incd"], cols["postCode"], cols["address"], cols["account"]), ("S1", "1239494-DW", "", "", "acc"))


@unittest.skipUnless(HAVE_DEPS, "needs the API image (httpx, sqlalchemy)")
class PropertyInsertPlanTests(unittest.TestCase):
  REUSE = {"incident_column": "incdId", "incident": "INC1", "house_column": "houseNo", "house": "12",
           "item_column": "itemId", "item_id": "ITEM-1"}

  def plan(self, rows):
    return jobs.plan_property_insert(rows, self.REUSE, "ID")

  def test_no_row_for_the_house_inserts(self):
    self.assertEqual(self.plan([{"ID": "R1", "incdId": "INC1", "houseNo": "14", "itemId": ""}])["action"], "insert")

  def test_blank_row_from_the_form_is_reused_not_duplicated(self):
    rows = [{"ID": "R1", "incdId": "INC1", "houseNo": " 12 ", "itemId": ""}]
    self.assertEqual(self.plan(rows), {"action": "reuse", "row_id": "R1"})

  def test_row_owned_by_another_item_blocks_a_second_row(self):
    rows = [{"ID": "R1", "incdId": "INC1", "houseNo": "12", "itemId": "someone-else"}]
    self.assertEqual(self.plan(rows), {"action": "taken", "row_id": "R1"})

  def test_blank_row_is_preferred_over_a_taken_one(self):
    rows = [{"ID": "R1", "incdId": "INC1", "houseNo": "12", "itemId": "someone-else"},
            {"ID": "R2", "incdId": "INC1", "houseNo": "12", "itemId": None}]
    self.assertEqual(self.plan(rows), {"action": "reuse", "row_id": "R2"})

  def test_same_house_in_another_incident_is_ignored(self):
    self.assertEqual(self.plan([{"ID": "R1", "incdId": "OTHER", "houseNo": "12", "itemId": ""}])["action"], "insert")

  def test_retried_insert_finds_its_own_row(self):
    rows = [{"ID": "R9", "incdId": "INC1", "houseNo": "12", "itemId": "item-1"}]
    self.assertEqual(self.plan(rows), {"action": "present", "row_id": "R9"})

  def test_no_house_number_never_matches(self):
    reuse = dict(self.REUSE, house="")
    self.assertEqual(jobs.plan_property_insert([{"ID": "R1", "incdId": "INC1", "houseNo": "", "itemId": ""}], reuse, "ID")["action"], "insert")


@unittest.skipUnless(HAVE_DEPS, "needs the API image (httpx, sqlalchemy)")
class AccountListPlanTests(unittest.TestCase):
  A, B, NEW = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

  def test_listed_missing_and_new(self):
    rows = [{"Answer Value": self.A.upper(), "Display Text": "Alpha"},
            {"Answer Value": self.NEW, "Display Text": "Brand New"},
            {"Answer Value": "not-an-id", "Display Text": "Typed by hand"},
            {"Answer Value": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "Display Text": ""}]
    plan = jobs.plan_accounts(rows, linked={self.A, self.B}, existing={self.A, self.B})
    self.assertEqual(plan["listed"], {self.A})
    self.assertEqual(plan["missing"], {self.B})
    self.assertEqual(plan["new"], [(self.NEW, "Brand New")])   # needs a real id AND a name

  def test_an_account_that_exists_but_is_not_linked_is_not_new(self):
    plan = jobs.plan_accounts([{"Answer Value": self.B, "Display Text": "Other customer"}], linked={self.A}, existing={self.A, self.B})
    self.assertEqual(plan["new"], [])


@unittest.skipUnless(HAVE_DEPS, "needs the API image (httpx, sqlalchemy)")
class EngineerTests(unittest.TestCase):
  ROWS = [{"id": "1", "department": "Electrical", "name": " Eng One ", "email1": "one@example.com", "email2": "", "Reason": "Electrician to Call|Alarm Engineer to Call"},
          {"id": "2", "department": "Gas", "name": "Eng Two", "email1": "", "email2": "", "Reason": ""},
          {"id": "3", "department": "Gas", "name": "eng two", "email1": "dupe@example.com", "email2": "", "Reason": ""},
          {"id": "4", "department": "", "name": "", "email1": "x@example.com", "email2": "", "Reason": ""}]

  def test_plan_engineers(self):
    plan = jobs.plan_engineers(self.ROWS)
    self.assertEqual([e["name"] for e in plan], ["Eng One", "Eng Two"])   # trimmed, blank + duplicate names dropped
    self.assertEqual(plan[0]["reasons"], ["Electrician to Call", "Alarm Engineer to Call"])
    self.assertEqual(plan[0]["email"], "one@example.com")

  def test_dropdown_options_are_injected_only_into_the_engineer_field(self):
    import ilgforms_sync as sync
    schema = {"fields": [{"key": "engineer", "type": "dropdown", "options": {"option1": ""}},
                         {"key": "status", "type": "dropdown", "options": {"option1": "", "option2": "Repaired"}}], "status_summary": {}}
    out = sync.with_engineer_options(schema, ["Eng One", "Eng Two"])
    self.assertEqual(out["fields"][0]["options"], {"option1": "", "option2": "Eng One", "option3": "Eng Two"})
    self.assertEqual(out["fields"][1]["options"], {"option1": "", "option2": "Repaired"})
    self.assertEqual(schema["fields"][0]["options"], {"option1": ""})        # the stored layout is not mutated
    self.assertIs(sync.with_engineer_options(schema, None), schema)           # no integration: untouched
