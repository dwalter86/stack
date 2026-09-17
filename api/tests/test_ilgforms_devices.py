"""Appliance matching + field rules (pure): python3 -m unittest discover api/tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import ilgforms_matching as m  # noqa: E402

BASE = {"customersName": "Mr Forms", "postCode": "TE5 7ST", "houseNoName": "9", "address": "Test St,\nTown",
        "telephoneNumber1": "07000000002", "dateAndTime": "2026-09-17 12:00"}


def dev(**kw):
  d = {"uniq": "0", "uniq_up": "DEV1-17092026-120000-11111111", "make": "Hotpoint", "model": "H1",
       "applianceType": "Fridge", "status": "Electrician to Call", "repairStatus": "In Progress", "systemID": ""}
  d.update(kw); return d


def item(id_, name="Mr Forms", make="Hotpoint", postcode="TE5 7ST"):
  return {"id": id_, "name": name, "data": {"postcode": postcode, "itemMake": make}, "created_at": "2026-09-17"}


class DevicePlanTests(unittest.TestCase):
  def test_new_appliance_creates(self):
    self.assertEqual(m.plan_devices([dev()], BASE, [])[0]["action"], m.ACTION_CREATE)

  def test_system_id_updates(self):
    plan = m.plan_devices([dev(systemID="AAA")], BASE, [item("aaa")])[0]
    self.assertEqual((plan["action"], plan["item_id"]), (m.ACTION_UPDATE, "aaa"))

  def test_dead_system_id_skips(self):
    self.assertEqual(m.plan_devices([dev(systemID="dead")], BASE, [item("aaa")])[0]["action"], m.ACTION_SKIP)

  def test_name_postcode_make_links(self):
    self.assertEqual(m.plan_devices([dev()], BASE, [item("aaa")])[0]["action"], m.ACTION_LINK)

  def test_blank_make_never_swallows_the_property_item(self):
    # n8n matched "" == "" here and turned the property record into the appliance.
    self.assertEqual(m.plan_devices([dev(make="")], BASE, [item("prop", make="")])[0]["action"], m.ACTION_CREATE)

  def test_two_identical_appliances_get_two_items(self):
    devices = [dev(), dev(uniq_up="DEV2-17092026-120001-22222222")]
    self.assertEqual([p["action"] for p in m.plan_devices(devices, BASE, [item("aaa")])], [m.ACTION_LINK, m.ACTION_CREATE])

  def test_redelivery_relinks_by_row_id(self):
    plan = m.plan_devices([dev(make="Other")], BASE, [item("aaa")], links={"aaa": "DEV1-17092026-120000-11111111"})[0]
    self.assertEqual((plan["action"], plan["item_id"]), (m.ACTION_LINK, "aaa"))

  def test_item_linked_to_another_row_is_not_reused(self):
    self.assertEqual(m.plan_devices([dev()], BASE, [item("aaa")], links={"aaa": "OTHER"})[0]["action"], m.ACTION_CREATE)


class DeviceFieldTests(unittest.TestCase):
  def test_create_fields_and_photo_url(self):
    name, data = m.device_item_fields(dev(photo="abc.jpg"), BASE, provider_id=50255, entry_ref="guid-1", existing=False)
    self.assertEqual(name, "Mr Forms")
    self.assertEqual(data["address"], "Test St, Town")
    self.assertEqual(data["itemPhoto"], "https://www.ilgforms.com/Files/FormEntry/50255-guid-1abc.jpg")
    self.assertEqual((data["reportStatus"], data["status"]), ("Electrician to Call", "In Progress"))
    self.assertEqual(data["itemPhoto2"], "")

  def test_update_sends_only_non_empty(self):
    _, data = m.device_item_fields(dev(model="", photo=""), BASE, provider_id=1, entry_ref="g", existing=True)
    self.assertNotIn("itemModel", data); self.assertNotIn("itemPhoto", data); self.assertIn("itemMake", data)

  def test_full_photo_url_is_kept(self):
    self.assertEqual(m.photo_url("https://x/y.jpg", 1, "g"), "https://x/y.jpg")

  def test_entry_id_is_hyphenated(self):
    self.assertEqual(m.hyphenate_entry_id("0123456789abcdef0123456789abcdef"), "01234567-89ab-cdef-0123-456789abcdef")
    self.assertEqual(m.hyphenate_entry_id("", "fallback-id"), "fallback-id")

  def test_engineer_fields(self):
    name, data = m.engineer_item_fields({"customername": "Mr F", "deviceStatus": "Repaired", "status": "Repaired on Site",
                                         "comments": "done", "make": ""}, provider_id=1, entry_ref="g")
    self.assertEqual(name, "Mr F")
    self.assertEqual(data, {"status": "Repaired", "reportStatus": "Repaired on Site", "itemComments": "done"})


if __name__ == "__main__":
  unittest.main()
