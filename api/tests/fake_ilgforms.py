"""An in-memory stand-in for the ILG Forms datasource API, for tests.
Implements what the real one does: paged GET, and PUT with NewRows,
DeletedRows and RowColumnUpdates (row id = first column)."""
import json

import httpx

LAYOUTS = {
  "nflList": ["ID", "incd", "postCode", "date", "colour", "account", "address"],
  "nfmain": ["ID", "incd", "postCode", "houseNo", "customerName", "streetName", "initialVisit", "notes", "incdId",
             "accountId", "teleNo1", "teleNo2", "email", "itemId"],
  "deviceDB": ["uniq", "incidNo", "houseNoName", "Postcode", "Make", "Model", "SerialNumber", "ApplianceType",
               "ApproxAge", "ApproxPrice", "Status", "Engineer", "Photo", "comments", "Repair Status", "eEmail",
               "systemID", "systemAccountID", "systemSectionID", "photo2", "photo3", "photo4", "Name",
               "Contact Number", "address"],
}


class FakeIlgForms:
  def __init__(self, key="k3y", max_page=1250):
    self.key, self.max_page = key, max_page
    self.sheets = {name: [] for name in LAYOUTS}
    self.calls = []
    self.fail_next = 0   # answer the next N writes with the datasource lock error

  def transport(self):
    return httpx.MockTransport(self.handle)

  def rows(self, name):
    return [dict(zip(LAYOUTS[name], r)) for r in self.sheets[name]]

  def add(self, name, **values):
    self.sheets[name].append([str(values.get(h, "")) for h in LAYOUTS[name]])

  def handle(self, request):
    if request.method == "GET":
      q = request.url.params
      if q.get("IntegrationKey") != self.key:
        return httpx.Response(401, text="bad key")
      name = q["ExternalId"]
      size = min(int(q.get("PageSize", self.max_page)), self.max_page); page = int(q.get("PageNo", 0))
      data = self.sheets[name]
      return httpx.Response(200, json={"DataSource": {"ExternalId": name, "TotalRows": len(data),
        "Headers": [{"Name": h} for h in LAYOUTS[name]], "Rows": data[page * size:(page + 1) * size]}})
    body = json.loads(request.content)
    self.calls.append(body)
    if body.get("IntegrationKey") != self.key:
      return httpx.Response(401, text="bad key")
    if self.fail_next:
      self.fail_next -= 1
      return httpx.Response(500, text='{"ResponseStatus":{"ErrorCode":"CacheLockException","Message":"Missing or expired lock"}}')
    name = body["ExternalId"]; data = self.sheets[name]; width = len(LAYOUTS[name])
    for update in body.get("RowColumnUpdates") or []:
      target = [r for r in data if r[0] == update["RowId"]]
      if not target:
        return httpx.Response(400, text='{"ResponseStatus":{"ErrorCode":"Predicate","Message":"No Rows Found"}}')
      for col in update["ColumnUpdates"]:
        target[0][LAYOUTS[name].index(col["Column"])] = col["Value"]
    for row in body.get("NewRows") or []:
      assert len(row) == width, f"{name}: row has {len(row)} values, sheet has {width} columns"
      data.append([str(v) for v in row])
    for row in body.get("DeletedRows") or []:
      data[:] = [r for r in data if r[0] != row[0]]
    return httpx.Response(200, json={})
