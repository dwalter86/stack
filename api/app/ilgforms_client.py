"""Minimal client for the ILG Forms datasource API (https://www.ilgforms.com/api/v2/datasource).

One endpoint does everything (PUT /v2/datasource, per the ILG Forms API explorer):
  GET  ?CompanyId&ExternalId&ReturnRows=true&PageSize&IntegrationKey  -> {DataSource: {Headers, Rows}}
  PUT  {ExternalId, CompanyId, IntegrationKey, and any of:
          RowColumnUpdates: [{RowId, ColumnUpdates: [{Column, Value}]}]   update cells
          NewRows:          [[v1, v2, ...]]   append rows, values in header order
          DeletedRows:      [[rowId]]         remove rows by their key (first column)}
A row's id is the value of the datasource's first column.

ILG Forms intermittently answers 500 CacheLockException when two writers hit
the same datasource; that is retryable and surfaced as IlgFormsRetryable.
"""
import os

import httpx

BASE_URL = os.environ.get("ILGFORMS_BASE_URL", "https://www.ilgforms.com/api/v2")
DEFAULT_PAGE_SIZE = 1250   # the API's maximum
MAX_PAGES = 80             # 100,000 rows


class IlgFormsError(Exception):
  """The request was rejected and retrying will not help."""


class IlgFormsRetryable(IlgFormsError):
  """Transient failure (datasource lock, 5xx, network): try again later."""


class IlgFormsClient:
  def __init__(self, company_id: int, integration_key: str, *, base_url: str = BASE_URL,
               timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
    self.company_id = int(company_id)
    self.integration_key = integration_key
    self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

  def close(self):
    self._http.close()

  def __enter__(self):
    return self

  def __exit__(self, *exc):
    self.close()

  def _send(self, method: str, **kwargs) -> dict:
    try:
      response = self._http.request(method, "/datasource", **kwargs)
    except httpx.HTTPError as exc:
      raise IlgFormsRetryable(f"Network error talking to ILG Forms: {exc}") from exc
    if response.status_code >= 500:
      raise IlgFormsRetryable(f"ILG Forms {response.status_code}: {response.text[:500]}")
    if response.status_code >= 400:
      raise IlgFormsError(f"ILG Forms {response.status_code}: {response.text[:500]}")
    if not response.content:
      return {}
    try:
      return response.json()
    except ValueError:
      return {"raw": response.text[:500]}

  def get_rows(self, external_id: str, page_size: int = DEFAULT_PAGE_SIZE) -> list[dict]:
    """All rows of a datasource as dicts keyed by column name.

    ILG Forms caps a page at 1250 rows and numbers pages from 0 (PageNo). The
    n8n flows only ever read page 0, so rows past 1250 were invisible to them."""
    rows: list[dict] = []
    total = None
    for page in range(MAX_PAGES):
      body = self._send("GET", params={
        "CompanyId": self.company_id, "ExternalId": external_id, "ReturnRows": "true",
        "PageSize": page_size, "PageNo": page, "IntegrationKey": self.integration_key,
      })
      source = body.get("DataSource") or {}
      headers = [h.get("Name") for h in source.get("Headers") or []]
      batch = source.get("Rows") or []
      rows.extend(dict(zip(headers, row)) for row in batch)
      total = source.get("TotalRows") if isinstance(source.get("TotalRows"), int) else total
      if not batch or len(batch) < page_size or (total is not None and len(rows) >= total):
        break
    return rows

  def get_headers(self, external_id: str) -> list[str]:
    """Column names, in order. Needed to lay out NewRows correctly."""
    body = self._send("GET", params={
      "CompanyId": self.company_id, "ExternalId": external_id, "ReturnRows": "true", "PageSize": 1,
      "IntegrationKey": self.integration_key,
    })
    return [h.get("Name") for h in (body.get("DataSource") or {}).get("Headers") or []]

  def insert_row(self, external_id: str, values: dict, headers: list[str] | None = None) -> dict:
    """Append one row. `values` is keyed by column name; columns it does not
    mention are left blank, and the layout follows the datasource's own
    header order so a column added in ILG Forms cannot shift the data."""
    headers = headers or self.get_headers(external_id)
    if not headers:
      raise IlgFormsError(f"Datasource {external_id} has no columns")
    unknown = [c for c in values if c not in headers]
    if unknown:
      raise IlgFormsError(f"Datasource {external_id} has no column(s): {', '.join(unknown)}")
    if not str(values.get(headers[0]) or "").strip():
      raise IlgFormsError(f"Cannot insert into {external_id} without a value for its key column {headers[0]}")
    row = ["" if values.get(h) is None else str(values.get(h)) for h in headers]
    return self._send("PUT", json={"ExternalId": external_id, "NewRows": [row],
                                   "CompanyId": self.company_id, "IntegrationKey": self.integration_key})

  def delete_rows(self, external_id: str, row_ids: list[str]) -> dict:
    row_ids = [str(r).strip() for r in row_ids if str(r or "").strip()]
    if not row_ids:
      raise IlgFormsError("Cannot delete datasource rows without row ids")
    return self._send("PUT", json={"ExternalId": external_id, "DeletedRows": [[r] for r in row_ids],
                                   "CompanyId": self.company_id, "IntegrationKey": self.integration_key})

  def update_cells(self, external_id: str, row_id: str, columns: dict) -> dict:
    """Set one or more columns on a single datasource row."""
    if not row_id:
      raise IlgFormsError("Cannot update a datasource row without a RowId")
    return self._send("PUT", json={
      "ExternalId": external_id,
      "RowColumnUpdates": [{
        "RowId": row_id,
        "ColumnUpdates": [{"Column": column, "Value": "" if value is None else str(value)}
                          for column, value in columns.items()],
      }],
      "CompanyId": self.company_id,
      "IntegrationKey": self.integration_key,
    })
