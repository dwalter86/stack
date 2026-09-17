"""Unit tests for the ILG Forms matching rules. Pure Python, no database:

    python3 -m unittest discover api/tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import ilgforms_matching as m  # noqa: E402


def loc(**kw):
  base = {"uniq": "EFGH-01012026-101004-00000002", "uniq_up": "EFGH-01012026-101004-00000002",
          "customersName": "Sample", "houseNo": "16", "streetName": "Example Road, Testville",
          "postCodeOut": "ZZ1 1ZZ", "postCodeIn": "ZZ1 1ZZ", "telephoneNumber1": "07000000001",
          "telephoneNumber2": "", "email": "", "initialVisit": "Faults", "itemId": ""}
  base.update(kw)
  return base


def item(id_, name="Sample", created="2026-07-09T10:00:00", **data):
  base = {"houseNo": "16", "address": "Example Road, Testville", "postcode": "ZZ1 1ZZ",
          "telephone": "07000000001", "status": "Faults"}
  base.update(data)
  return {"id": id_, "name": name, "data": base, "created_at": created}


class PlanTests(unittest.TestCase):
  def test_no_items_creates(self):
    plan = m.plan_locations([loc()], [])[0]
    self.assertEqual(plan["action"], m.ACTION_CREATE)
    self.assertEqual(plan["row_id"], "EFGH-01012026-101004-00000002")

  def test_item_id_match_updates(self):
    plan = m.plan_locations([loc(itemId="AAA")], [item("aaa")])[0]
    self.assertEqual((plan["action"], plan["item_id"]), (m.ACTION_UPDATE, "aaa"))

  def test_possible_match_is_linked_not_dropped(self):
    # The n8n flow silently did nothing here; that left itemId blank forever.
    plan = m.plan_locations([loc()], [item("aaa")])[0]
    self.assertEqual((plan["action"], plan["item_id"]), (m.ACTION_LINK, "aaa"))

  def test_orphan_item_id_is_skipped(self):
    plan = m.plan_locations([loc(itemId="dead")], [item("aaa")])[0]
    self.assertEqual(plan["action"], m.ACTION_SKIP)

  def test_item_id_in_other_section_still_updates(self):
    plan = m.plan_locations([loc(itemId="zzz")], [], known_item_ids={"zzz"})[0]
    self.assertEqual(plan["action"], m.ACTION_UPDATE)

  def test_title_and_phone_variations_match(self):
    location = loc(customersName="Mr Sample", telephoneNumber1="07000 000001", postCodeIn="zz11zz")
    self.assertEqual(m.plan_locations([location], [item("aaa")])[0]["action"], m.ACTION_LINK)

  def test_phone_alone_is_enough_when_name_differs(self):
    location = loc(customersName="Tester")
    self.assertEqual(m.plan_locations([location], [item("aaa", name="Mr Testman")])[0]["action"], m.ACTION_LINK)

  def test_different_house_does_not_match(self):
    self.assertEqual(m.plan_locations([loc(houseNo="18")], [item("aaa")])[0]["action"], m.ACTION_CREATE)

  def test_blank_item_name_is_not_a_name_match(self):
    location = loc(telephoneNumber1="")
    self.assertEqual(m.plan_locations([location], [item("aaa", name="")])[0]["action"], m.ACTION_CREATE)

  def test_multiple_devices_links_oldest(self):
    items = [item("new", created="2026-07-10T09:00:00"), item("old", created="2026-07-09T09:00:00")]
    plan = m.plan_locations([loc()], items)[0]
    self.assertEqual((plan["item_id"], plan["candidates"]), ("old", 2))

  def test_one_item_is_never_given_to_two_rows(self):
    second = loc(uniq="0", uniq_up="IJKL-01012026-021520-00000003", customersName="Mr Sample")
    plans = m.plan_locations([loc(), second], [item("aaa")])
    self.assertEqual([p["action"] for p in plans], [m.ACTION_LINK, m.ACTION_CREATE])

  def test_item_owned_by_a_row_with_item_id_is_not_linked_elsewhere(self):
    owner = loc(uniq_up="ROW1-09072026-101004-1", itemId="aaa")
    blank = loc(uniq_up="ROW2-09072026-101004-2")
    plans = m.plan_locations([blank, owner], [item("aaa")])
    self.assertEqual([p["action"] for p in plans], [m.ACTION_CREATE, m.ACTION_UPDATE])

  def test_item_linked_to_another_row_is_excluded(self):
    plan = m.plan_locations([loc()], [item("aaa")], links={"aaa": "OTHER-ROW"})[0]
    self.assertEqual(plan["action"], m.ACTION_CREATE)

  def test_redelivery_relinks_same_item(self):
    items = [item("old", created="2026-07-09T09:00:00"), item("mine", created="2026-07-10T09:00:00")]
    plan = m.plan_locations([loc()], items, links={"mine": "EFGH-01012026-101004-00000002"})[0]
    self.assertEqual(plan["item_id"], "mine")

  def test_row_id_falls_back_from_zero_uniq(self):
    self.assertEqual(m.row_id_for({"uniq": "0", "uniq_up": "7UWM-1"}), "7UWM-1")
    self.assertEqual(m.row_id_for({"uniq": "0", "uniq_up": ""}), "")


class FieldTests(unittest.TestCase):
  def test_new_item_fields(self):
    name, data = m.item_fields(loc(streetName="Ambleside Walk,\nNorth Anston, \nSheffield"),
                               existing_data=None, now_iso="NOW")
    self.assertEqual(name, "Sample")
    self.assertEqual(data["address"], "Ambleside Walk, North Anston, Sheffield")
    self.assertEqual(data["visitDate"], "2026-01-01T10:10:04")  # from the row id, no stray brace
    self.assertEqual(data["status"], "Faults")

  def test_visit_date_falls_back_to_now(self):
    _, data = m.item_fields(loc(uniq="0", uniq_up="x"), existing_data=None, now_iso="NOW")
    self.assertEqual(data["visitDate"], "NOW")

  def test_update_never_reverts_engineer_status(self):
    _, data = m.item_fields(loc(initialVisit="Faults"), existing_data={"status": "Repaired"}, now_iso="NOW")
    self.assertNotIn("status", data)
    self.assertNotIn("visitDate", data)

  def test_update_moves_between_initial_statuses(self):
    _, data = m.item_fields(loc(initialVisit="No Faults"), existing_data={"status": "Out"}, now_iso="NOW")
    self.assertEqual(data["status"], "No Faults")

  def test_incident_form_never_puts_faults_back_on_an_item_that_has_an_appliance(self):
    # status is blank because the appliance's repair status is blank: that is not "unset".
    _, data = m.item_fields(loc(initialVisit="Faults"), existing_data={"status": "", "itemMake": "SteelSeries"}, now_iso="NOW")
    self.assertNotIn("status", data)
    # a bare property with a blank status still takes the first-visit outcome
    _, data = m.item_fields(loc(initialVisit="Faults"), existing_data={"status": ""}, now_iso="NOW")
    self.assertEqual(data["status"], "Faults")

  def test_update_does_not_blank_existing_values(self):
    _, data = m.item_fields(loc(email="", telephoneNumber2=""), existing_data={"email": "a@b.c"}, now_iso="NOW")
    self.assertNotIn("email", data)
    self.assertNotIn("telephone2", data)


if __name__ == "__main__":
  unittest.main()
