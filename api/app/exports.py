"""Helpers for rendering .docx templates and converting to PDF."""

from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
from docx import Document
from docx.enum.text import WD_BREAK
from docxtpl import DocxTemplate
from jinja2 import Environment, StrictUndefined
from jinja2.exceptions import UndefinedError, TemplateSyntaxError, TemplateError


GOTENBERG_URL = os.environ.get("GOTENBERG_URL", "http://gotenberg:3000")
MAX_TEMPLATE_BYTES = 5 * 1024 * 1024
MAX_ITEMS_PER_EXPORT = 500


class TemplateRenderError(Exception):
  """Raised when a docx template cannot be rendered (missing var, bad syntax, ...)."""


class PdfConversionError(Exception):
  """Raised when Gotenberg cannot convert the docx to PDF."""


def validate_docx_bytes(content: bytes) -> None:
  """Reject anything that isn't a real .docx zip with a word/document.xml entry."""
  if not content:
    raise ValueError("Empty file")
  if len(content) > MAX_TEMPLATE_BYTES:
    raise ValueError(f"Template exceeds {MAX_TEMPLATE_BYTES // 1024 // 1024} MB limit")
  try:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
      names = set(zf.namelist())
  except zipfile.BadZipFile as exc:
    raise ValueError("File is not a valid .docx (must be an Office Open XML zip)") from exc
  if "word/document.xml" not in names:
    raise ValueError("Missing word/document.xml — not a Word document")


def _format_dt(value: Any) -> str:
  if isinstance(value, datetime):
    if value.tzinfo is None:
      value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
  return str(value or "")


def build_context(
  items: list[dict],
  section: dict,
  account: dict,
  exported_by: str,
) -> dict:
  """Assemble the variables made available to the template."""
  normalised_items: list[dict] = []
  for it in items:
    normalised_items.append({
      "id": it.get("id", ""),
      "name": it.get("name", ""),
      "data": it.get("data") or {},
      "created_at": _format_dt(it.get("created_at")),
    })

  context: dict[str, Any] = {
    "items": normalised_items,
    "section": {
      "slug": section.get("slug", ""),
      "label": section.get("label", ""),
      "detail": section.get("detail", ""),
    },
    "account": {
      "id": account.get("id", ""),
      "name": account.get("name", ""),
    },
    "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    "exported_by": exported_by or "",
  }

  # Convenience aliases for single-item templates.
  first = normalised_items[0] if normalised_items else {"name": "", "data": {}, "created_at": ""}
  context.setdefault("item", first)
  context.setdefault("name", first["name"])
  context.setdefault("data", first["data"])
  context.setdefault("created_at", first["created_at"])
  return context


def render_template(template_bytes: bytes, context: dict) -> bytes:
  """Render the docx template against the context.

  Uses Jinja's StrictUndefined so typos surface as TemplateRenderError instead of
  silently rendering as empty strings.
  """
  doc = DocxTemplate(io.BytesIO(template_bytes))
  jinja_env = Environment(undefined=StrictUndefined, autoescape=False)
  try:
    doc.render(context, jinja_env=jinja_env)
  except UndefinedError as exc:
    raise TemplateRenderError(f"Unknown variable in template: {exc}") from exc
  except TemplateSyntaxError as exc:
    raise TemplateRenderError(f"Template syntax error: {exc.message} (line {exc.lineno})") from exc
  except TemplateError as exc:
    raise TemplateRenderError(f"Template error: {exc}") from exc
  except Exception as exc:
    # docxtpl raises plain ValueError when a token is split across runs, etc.
    raise TemplateRenderError(f"Failed to render template: {exc}") from exc

  out = io.BytesIO()
  doc.save(out)
  return out.getvalue()


def docx_to_pdf(docx_bytes: bytes, filename: str = "document.docx") -> bytes:
  """Convert docx bytes to PDF bytes using Gotenberg's LibreOffice route."""
  url = f"{GOTENBERG_URL.rstrip('/')}/forms/libreoffice/convert"
  files = {
    "files": (
      filename,
      docx_bytes,
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
  }
  try:
    with httpx.Client(timeout=60.0) as client:
      resp = client.post(url, files=files)
  except httpx.HTTPError as exc:
    raise PdfConversionError(f"PDF service unreachable: {exc}") from exc

  if resp.status_code != 200:
    detail = resp.text.strip() or f"HTTP {resp.status_code}"
    raise PdfConversionError(f"PDF service error: {detail}")
  return resp.content


def _safe_field_keys(schema: Any) -> list[tuple[str, str]]:
  """Return (key, label) pairs for a section's schema, deduped, in declared order."""
  if not isinstance(schema, dict):
    return []
  fields = schema.get("fields") or []
  seen: set[str] = set()
  out: list[tuple[str, str]] = []
  for field in fields:
    if not isinstance(field, dict):
      continue
    key = str(field.get("key") or "").strip()
    if not key or key in seen:
      continue
    seen.add(key)
    label = str(field.get("label") or key)
    out.append((key, label))
  return out


def generate_starter_template(account_name: str, sections: Iterable[dict]) -> bytes:
  """Build a starter .docx that lists every placeholder available for this account."""
  doc = Document()
  doc.add_heading(f"Export template — {account_name}", level=0)

  intro = doc.add_paragraph()
  intro.add_run(
    "This document lists every placeholder you can use in your export template. "
    "Copy and paste into a new Word document, keep what you need, delete what you don't."
  )

  doc.add_heading("Quick reference", level=1)
  syntax_lines = [
    "{{ var }} — insert a value at this spot",
    "{% if data.notes %} ... {% endif %} — show a block conditionally",
    "{%p for item in items %} ... {%p endfor %} — repeat whole paragraphs (one per item)",
    "{%tr for item in items %} ... {%tr endfor %} — repeat a table row (use inside a 1-row table)",
  ]
  for line in syntax_lines:
    p = doc.add_paragraph(line, style="List Bullet")

  doc.add_heading("Global placeholders", level=1)
  globals_table = [
    ("{{ account.name }}", "Account name"),
    ("{{ section.label }}", "Section name"),
    ("{{ section.slug }}", "Section slug"),
    ("{{ section.detail }}", "Section detail/subtitle"),
    ("{{ exported_at }}", "Timestamp the PDF was generated"),
    ("{{ exported_by }}", "User who triggered the export"),
  ]
  table = doc.add_table(rows=1, cols=2)
  table.style = "Table Grid"
  hdr = table.rows[0].cells
  hdr[0].text = "Placeholder"
  hdr[1].text = "Description"
  for token, desc in globals_table:
    row = table.add_row().cells
    row[0].text = token
    row[1].text = desc

  doc.add_heading("Single-item shortcuts", level=1)
  doc.add_paragraph(
    "When exporting one item these aliases resolve to that item; "
    "when exporting many they resolve to the first item."
  )
  for token, desc in [
    ("{{ item.name }}", "Item name (object alias)"),
    ("{{ item.created_at }}", "Item created date (object alias)"),
    ("{{ item.data.<field_key> }}", "Item field value (object alias)"),
    ("{{ name }}", "Item name"),
    ("{{ created_at }}", "Item created date"),
    ("{{ data.<field_key> }}", "Any field on the item — see your sections below"),
  ]:
    p = doc.add_paragraph()
    p.add_run(token).bold = True
    p.add_run(f" — {desc}")

  doc.add_heading("Multi-item loop example", level=1)
  example = doc.add_paragraph()
  example.add_run(
    "{%p for item in items %}\n"
    "Item: {{ item.name }}\n"
    "Created: {{ item.created_at }}\n"
    "Notes: {{ item.data.notes }}\n"
    "{%p endfor %}"
  )

  doc.add_heading("Per-section field placeholders", level=1)
  any_section = False
  for section in sections:
    any_section = True
    label = section.get("label") or section.get("slug") or "Section"
    slug = section.get("slug") or ""
    doc.add_heading(f"{label}", level=2)
    if slug:
      sub = doc.add_paragraph()
      sub.add_run(f"slug: {slug}").italic = True

    fields = _safe_field_keys(section.get("schema"))
    if not fields:
      doc.add_paragraph("(No custom fields defined yet.)")
      continue

    section_table = doc.add_table(rows=1, cols=2)
    section_table.style = "Table Grid"
    hdr = section_table.rows[0].cells
    hdr[0].text = "Placeholder"
    hdr[1].text = "Field"
    for key, friendly in fields:
      row = section_table.add_row().cells
      row[0].text = f"{{{{ data.{key} }}}}"
      row[1].text = friendly

  if not any_section:
    doc.add_paragraph("(This account has no sections yet.)")

  doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
  doc.add_heading("Tips", level=1)
  for tip in [
    "Type each {{ ... }} placeholder in one go — Word can split a token across formatting runs and break rendering.",
    "For repeating table rows, put the {%tr for item in items %} on the first cell of the row and {%tr endfor %} on the last cell of the same row.",
    "Unknown placeholders (typos) will return a 400 error at export time so you know to fix them.",
  ]:
    doc.add_paragraph(tip, style="List Bullet")

  buf = io.BytesIO()
  doc.save(buf)
  return buf.getvalue()
