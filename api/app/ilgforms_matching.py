"""Pure matching logic for ILG Forms incident submissions.

Decides, for every location in a submission, whether Stack should update a
known item, link the sheet row to an item that already exists, create a new
item, or skip the location. No database or network access lives here so the
rules can be unit-tested on their own.

Ported from the n8n "Add Incident" workflow, with its two dead ends removed:
a possible match is now linked instead of silently dropped.
"""
import re
from datetime import datetime

ACTION_UPDATE = "update"   # location.itemId points at a live item
ACTION_LINK = "link"       # no itemId yet, but the item already exists in Stack
ACTION_CREATE = "create"   # nothing matches: make a new item
ACTION_SKIP = "skip"       # sheet itemId points at an item Stack no longer has

# Status values the office form sets on first visit. An item whose status has
# moved past these (In Progress, Repaired, Completed, ...) belongs to the
# engineers, and a form resubmission must not drag it back.
INITIAL_VISIT_STATUSES = {"", "faults", "no faults", "out", "n/a"}

_ROW_ID_TIME_RE = re.compile(r"^[A-Z0-9]{4}-(\d{2})(\d{2})(\d{4})-(\d{2})(\d{2})(\d{2})-\d+$")
_TITLE_RE = re.compile(r"\b(mr|mrs|ms|miss)\b")


def norm(value) -> str:
  text = str(value if value is not None else "")
  text = re.sub(r"\r?\n", " ", text)
  text = text.replace(",", " ").replace(".", " ")
  return re.sub(r"\s+", " ", text).strip().lower()


def norm_name(value) -> str:
  return re.sub(r"\s+", " ", _TITLE_RE.sub("", norm(value))).strip()


def norm_phone(value) -> str:
  return re.sub(r"\D", "", str(value if value is not None else ""))


def norm_postcode(value) -> str:
  return norm(value).replace(" ", "")


def join_lines(value) -> str:
  """A multi-line address as one line: 'Walk,\nTown' -> 'Walk, Town' (no doubled commas)."""
  text = re.sub(r"\s*\r?\n\s*", ", ", str(value if value is not None else "").strip())
  return re.sub(r",(\s*,)+", ",", text).strip(", ")


def row_id_for(location: dict) -> str:
  """The datasource RowId for a location. `uniq` is "0" on rows added in an
  update, where `uniq_up` carries the real id."""
  for key in ("uniq_up", "uniq"):
    value = str(location.get(key) or "").strip()
    if value and value != "0":
      return value
  return ""


def visit_time_from_row_id(row_id: str) -> datetime | None:
  """Row ids embed when the location was captured: XXXX-DDMMYYYY-HHMMSS-N."""
  match = _ROW_ID_TIME_RE.match(row_id or "")
  if not match:
    return None
  day, month, year, hour, minute, second = (int(g) for g in match.groups())
  try:
    return datetime(year, month, day, hour, minute, second)
  except ValueError:
    return None


def _street_matches(item_address, loc_street) -> bool:
  item, loc = norm(item_address), norm(loc_street)
  if not loc:
    return True
  if not item:
    return False
  return loc in item or item in loc


def is_possible_match(item: dict, location: dict) -> bool:
  data = item.get("data") or {}
  item_name, loc_name = norm_name(item.get("name")), norm_name(location.get("customersName"))
  item_house, loc_house = norm(data.get("houseNo")), norm(location.get("houseNo"))
  item_postcode = norm_postcode(data.get("postcode"))
  loc_postcodes = {norm_postcode(location.get("postCodeOut")), norm_postcode(location.get("postCodeIn"))} - {""}
  item_phone, loc_phone = norm_phone(data.get("telephone")), norm_phone(location.get("telephoneNumber1"))

  house_matches = bool(item_house) and item_house == loc_house
  postcode_matches = bool(item_postcode) and item_postcode in loc_postcodes
  street_matches = _street_matches(data.get("address"), location.get("streetName") or location.get("addressOut"))
  name_matches = bool(item_name) and bool(loc_name) and (item_name in loc_name or loc_name in item_name)
  phone_matches = bool(item_phone) and item_phone == loc_phone

  return house_matches and postcode_matches and street_matches and (name_matches or phone_matches)


def plan_locations(locations: list, section_items: list, links: dict | None = None,
                   known_item_ids: set | None = None) -> list[dict]:
  """Decide an action per location.

  locations      -- page1.locations from the submission, in order
  section_items  -- every item in the incident's section: {id, name, data, created_at}
  links          -- item_id -> datasource row_id already recorded for that item
  known_item_ids -- ids of all items in the account (an itemId living in another
                    section still counts as live, not orphaned)

  An item is only ever handed to one row per submission, and never to a row
  other than the one it is already linked to.
  """
  links = links or {}
  by_id = {str(item["id"]).lower(): item for item in section_items}
  known = {str(i).lower() for i in (known_item_ids or set())} | set(by_id)
  claimed: set[str] = set()
  plans: list[dict] = []

  # Pass 1: rows that already carry an itemId claim their items first, so a
  # blank row can never be linked to an item another row owns.
  for location in locations:
    item_id = str((location or {}).get("itemId") or "").strip().lower()
    if item_id and item_id in known:
      claimed.add(item_id)

  for index, location in enumerate(locations):
    location = location if isinstance(location, dict) else {}
    row_id = row_id_for(location)
    sheet_item_id = str(location.get("itemId") or "").strip().lower()
    plan = {"index": index, "row_id": row_id, "location": location, "item_id": None,
            "candidates": 0, "reason": ""}

    if sheet_item_id:
      if sheet_item_id in known:
        plan.update(action=ACTION_UPDATE, item_id=sheet_item_id, reason="Matched by itemId")
      else:
        plan.update(action=ACTION_SKIP, item_id=sheet_item_id,
                    reason="Sheet itemId does not exist in Stack (orphan row left as is)")
      plans.append(plan)
      continue

    candidates = []
    for item in section_items:
      item_id = str(item["id"]).lower()
      linked_row = links.get(item_id)
      if linked_row and row_id and linked_row != row_id:
        continue  # belongs to a different sheet row
      if item_id in claimed and linked_row != row_id:
        continue
      if is_possible_match(item, location):
        candidates.append(item)

    if candidates:
      # Prefer the item already linked to this row (re-delivery before the
      # writeback landed), then the oldest item: the original property record
      # rather than a later per-device item.
      candidates.sort(key=lambda it: (links.get(str(it["id"]).lower()) != row_id or not row_id,
                                      str(it.get("created_at") or ""), str(it["id"])))
      chosen = str(candidates[0]["id"]).lower()
      claimed.add(chosen)
      plan.update(action=ACTION_LINK, item_id=chosen, candidates=len(candidates),
                  reason=f"Linked to existing item ({len(candidates)} candidate{'s' if len(candidates) != 1 else ''})")
    else:
      plan.update(action=ACTION_CREATE, reason="No existing item matched")
    plans.append(plan)

  return plans


def item_fields(location: dict, *, existing_data: dict | None, now_iso: str) -> tuple[str | None, dict]:
  """Build (name, data) for a location.

  existing_data None means a new item: every field is written, visitDate and
  status included. For an existing item only non-empty form values are sent
  (a blank on the form never wipes what Stack holds), visitDate is left
  alone, and status is only set while the item is still at an initial-visit
  status.
  """
  def clean(value) -> str:
    return str(value if value is not None else "").strip()

  street = join_lines(location.get("streetName") or location.get("addressOut"))
  values = {
    "houseNo": clean(location.get("houseNo")),
    "address": street,
    "postcode": clean(location.get("postCodeIn")) or clean(location.get("postCodeOut")),
    "telephone": clean(location.get("telephoneNumber1")),
    "telephone2": clean(location.get("telephoneNumber2")),
    "email": clean(location.get("email")),
  }
  status = clean(location.get("initialVisit"))
  name = clean(location.get("customersName"))

  if existing_data is None:
    visit = visit_time_from_row_id(row_id_for(location))
    values["visitDate"] = visit.isoformat() if visit else now_iso
    values["status"] = status
    return name, values

  data = {key: value for key, value in values.items() if value}
  current_status = norm(existing_data.get("status"))
  if status and current_status in INITIAL_VISIT_STATUSES and norm(status) != current_status:
    data["status"] = status
  return (name or None), data


# --- appliances (the "Items upload" form) ----------------------------------------------------

PHOTO_BASE_URL = "https://www.ilgforms.com/Files/FormEntry/"


def hyphenate_entry_id(entry_id: str, ds_row_id: str = "") -> str:
  entry_id = str(entry_id or "").strip()
  if "-" in entry_id:
    return entry_id
  if len(entry_id) == 32:
    return "-".join((entry_id[:8], entry_id[8:12], entry_id[12:16], entry_id[16:20], entry_id[20:]))
  return str(ds_row_id or "").strip()


def photo_url(filename, provider_id, entry_ref: str) -> str:
  """ILG Forms stores uploads as <ProviderId>-<entry guid><filename>."""
  name = str(filename or "").strip()
  if not name or re.match(r"^https?://", name, re.I):
    return name
  if provider_id and entry_ref:
    return f"{PHOTO_BASE_URL}{provider_id}-{entry_ref}{name}"
  return f"{PHOTO_BASE_URL}{name}"


def plan_devices(devices: list, base: dict, section_items: list, links: dict | None = None,
                 known_item_ids: set | None = None) -> list[dict]:
  """Decide an action per appliance: update (systemID is live), link (the item
  exists but the sheet does not know its id), create, or skip (dead systemID).

  links -- item_id -> device-datasource row id already recorded for that item.
  The fallback match is the n8n one (customer name + post code + make), but only
  when the appliance has a make: a blank make must not swallow the property item.
  """
  links = links or {}
  by_id = {str(item["id"]).lower(): item for item in section_items}
  known = {str(i).lower() for i in (known_item_ids or set())} | set(by_id)
  claimed = {str((d or {}).get("systemID") or "").strip().lower() for d in devices} & known
  name_key = norm(base.get("customersName"))
  plans = []
  for index, device in enumerate(devices):
    device = device if isinstance(device, dict) else {}
    row_id = row_id_for(device)
    system_id = str(device.get("systemID") or "").strip().lower()
    plan = {"index": index, "row_id": row_id, "device": device, "item_id": None, "reason": ""}
    if system_id:
      if system_id in known:
        plan.update(action=ACTION_UPDATE, item_id=system_id, reason="Matched by systemID")
      else:
        plan.update(action=ACTION_SKIP, item_id=system_id,
                    reason="Sheet systemID does not exist in Stack (orphan row left as is)")
      plans.append(plan)
      continue

    chosen = None
    for item in section_items:                      # re-delivery before the writeback landed
      item_id = str(item["id"]).lower()
      if row_id and links.get(item_id) == row_id:
        chosen = item_id
        break
    make_key = norm(device.get("make"))
    post_key = norm_postcode(base.get("postCode") or device.get("pCode"))
    if not chosen and make_key and name_key:
      for item in section_items:
        item_id = str(item["id"]).lower()
        if item_id in claimed or (links.get(item_id) and links.get(item_id) != row_id):
          continue
        data = item.get("data") or {}
        if (norm(item.get("name")) == name_key and norm_postcode(data.get("postcode")) == post_key
            and norm(data.get("itemMake")) == make_key):
          chosen = item_id
          break
    if chosen:
      claimed.add(chosen)
      plan.update(action=ACTION_LINK, item_id=chosen, reason="Linked to existing item")
    else:
      plan.update(action=ACTION_CREATE, reason="No existing item matched")
    plans.append(plan)
  return plans


def device_item_fields(device: dict, base: dict, *, provider_id, entry_ref: str, existing: bool) -> tuple[str | None, dict]:
  """(name, data) for an appliance. For an existing item only non-empty values
  are sent, so a blank on the form never wipes what Stack holds. The engineer's
  form is the authority on repair status, so that is applied whenever it is set."""
  def clean(value) -> str:
    return str(value if value is not None else "").strip()
  address = join_lines(base.get("address"))
  values = {
    "houseNo": clean(base.get("houseNoName")) or clean(device.get("houseNo")),
    "address": address,
    "postcode": clean(base.get("postCode")) or clean(device.get("pCode")),
    "telephone": clean(base.get("telephoneNumber1")) or clean(device.get("customerNumber")),
    "telephone2": clean(base.get("telephoneNumber2")),
    "email": clean(base.get("email")),
    "visitDate": clean(base.get("dateAndTime")),
    "itemMake": clean(device.get("make")),
    "itemModel": clean(device.get("model")),
    "itemSerialNumber": clean(device.get("serialNumber")),
    "itemApplianceType": clean(device.get("applianceType")),
    "itemAge": clean(device.get("approxAgeYr")),
    "itemPrice": clean(device.get("approxPurchasePrice")),
    "itemPhoto": photo_url(device.get("photo"), provider_id, entry_ref),
    "itemPhoto2": photo_url(device.get("photo2"), provider_id, entry_ref),
    "itemPhoto3": photo_url(device.get("photo3"), provider_id, entry_ref),
    "itemPhoto4": photo_url(device.get("photo4"), provider_id, entry_ref),
    "engineer": clean(device.get("engineer")),
    "reportStatus": clean(device.get("status")),
    "status": clean(device.get("repairStatus")),
  }
  name = clean(base.get("customersName")) or clean(device.get("customerName"))
  if not existing:
    return name, values
  return (name or None), {k: v for k, v in values.items() if v}


def engineer_item_fields(review: dict, *, provider_id, entry_ref: str) -> tuple[str | None, dict]:
  """(name, data) from the single-appliance engineer form. Non-empty values only."""
  def clean(value) -> str:
    return str(value if value is not None else "").strip()
  values = {
    "itemMake": clean(review.get("make")), "itemModel": clean(review.get("model")),
    "itemSerialNumber": clean(review.get("serialNumber")), "itemApplianceType": clean(review.get("applianceType")),
    "itemAge": clean(review.get("approxAgeYr")), "itemPrice": clean(review.get("approxPurchasePrice")),
    "engineer": clean(review.get("engineer")), "reportStatus": clean(review.get("status")),
    "itemPhoto": photo_url(review.get("photo"), provider_id, entry_ref),
    "itemComments": clean(review.get("comments")), "status": clean(review.get("deviceStatus")),
  }
  return (clean(review.get("customername")) or None), {k: v for k, v in values.items() if v}
