"""Audit logging: ASGI middleware that records every mutating API request
(and all login attempts) into the audit_log table, plus query helpers for
the super-admin audit endpoint.

The middleware wraps receive/send so it observes the request body and
response status without consuming them. Audit writes are best-effort:
a failure to write must never break the actual request.
"""
import json
import re
import traceback

from jose import jwt, JWTError
from sqlalchemy import text

from database import SessionLocal
from deps import JWT_SECRET

MAX_BODY_CHARS = 10_000
REDACTED = "[redacted]"
SENSITIVE_KEY_RE = re.compile(r"password|secret|token", re.IGNORECASE)

# Read-only usage worth recording. GETs not matching these are not logged
# (login/session chatter like /api/me would be pure noise). First match wins.
VIEW_RULES = [
  (r"^/api/accounts/[^/]+/sections/[^/]+/items", "section.view"),
  (r"^/api/accounts/[^/]+/items/[^/]+/comments$", "comments.view"),
  (r"^/api/accounts/[^/]+/items/[^/]+$",          "item.view"),
  (r"^/api/accounts/[^/]+/items$",                "section.view"),
  (r"^/api/accounts/[^/]+/sections/[^/]+/notes$", "notes.view"),
  (r"^/api/accounts/[^/]+/sections$",             "account.view"),
  (r"^/api/accounts/[^/]+/template/file$",        "template.download"),
  (r"^/api/accounts/[^/]+/template/starter$",     "template.download"),
  (r"^/api/me/accounts$",                         "home.view"),
]

# (method, path regex) -> action name. First match wins.
ACTION_RULES = [
  ("POST",   r"^/api/login$",                                    "login"),
  ("POST",   r"^/api/accounts/[^/]+/sections/[^/]+/items",       "item.create"),
  ("PUT",    r"^/api/accounts/[^/]+/items/[^/]+$",               "item.update"),
  ("DELETE", r"^/api/accounts/[^/]+/items/[^/]+$",               "item.delete"),
  ("POST",   r"^/api/accounts/[^/]+/items/[^/]+/comments$",      "comment.create"),
  ("POST",   r"^/api/accounts/[^/]+/items$",                     "item.create"),
  ("POST",   r"^/api/accounts/[^/]+/sections/[^/]+/notes$",      "note.create"),
  ("POST",   r"^/api/accounts/[^/]+/sections/[^/]+/export$",     "export.run"),
  ("POST",   r"^/api/accounts/[^/]+/sections$",                  "section.create"),
  ("PUT",    r"^/api/accounts/[^/]+/sections/[^/]+$",            "section.update"),
  ("DELETE", r"^/api/accounts/[^/]+/sections/[^/]+$",            "section.delete"),
  ("PUT",    r"^/api/accounts/[^/]+/template$",                  "template.upload"),
  ("DELETE", r"^/api/accounts/[^/]+/template$",                  "template.delete"),
  ("POST",   r"^/api/accounts$",                                 "account.create"),
  ("PUT",    r"^/api/accounts/[^/]+$",                           "account.update"),
  ("DELETE", r"^/api/accounts/[^/]+$",                           "account.delete"),
  ("POST",   r"^/api/admin/users$",                              "user.create"),
  ("PUT",    r"^/api/admin/users/[^/]+$",                        "user.update"),
  ("DELETE", r"^/api/admin/users/[^/]+$",                        "user.delete"),
  ("PUT",    r"^/api/me/preferences$",                           "preferences.update"),
]

ACCOUNT_ID_RE = re.compile(r"^/api/accounts/([0-9a-f-]{36})(/|$)")
UUID_RE = re.compile(r"^[0-9a-f-]{36}$")


def derive_action(method: str, path: str, status: int) -> str:
  if method == "GET":
    return derive_view_action(path) or "api.read"
  for m, pattern, action in ACTION_RULES:
    if m == method and re.match(pattern, path):
      if action == "login":
        return "login.success" if status < 400 else "login.failed"
      return action
  return f"api.write ({method})"


def derive_view_action(path: str) -> str | None:
  for pattern, action in VIEW_RULES:
    if re.match(pattern, path):
      return action
  return None


def redact(value):
  if isinstance(value, dict):
    return {k: (REDACTED if SENSITIVE_KEY_RE.search(k) else redact(v)) for k, v in value.items()}
  if isinstance(value, list):
    return [redact(v) for v in value]
  return value


def summarize_body(raw: bytes, content_type: str, keep_sensitive: bool = False):
  if not raw:
    return None
  if "application/json" not in content_type:
    return {"note": f"body not captured (content-type: {content_type or 'unknown'}, {len(raw)} bytes)"}
  try:
    parsed = json.loads(raw.decode("utf-8", errors="replace"))
  except (ValueError, UnicodeDecodeError):
    return {"note": f"unparseable body ({len(raw)} bytes)"}
  cleaned = parsed if keep_sensitive else redact(parsed)
  serialized = json.dumps(cleaned)
  if len(serialized) > MAX_BODY_CHARS:
    return {"note": f"body truncated ({len(serialized)} chars)", "preview": serialized[:MAX_BODY_CHARS]}
  return cleaned


def client_ip(scope) -> str:
  headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
  forwarded = headers.get("x-forwarded-for", "")
  if forwarded:
    return forwarded.split(",")[0].strip()
  client = scope.get("client")
  return client[0] if client else ""


def user_from_scope(scope) -> str | None:
  headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
  auth = headers.get("authorization", "")
  if not auth.startswith("Bearer "):
    return None
  try:
    payload = jwt.decode(auth.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"])
    return payload.get("sub")
  except JWTError:
    return None


def write_audit(*, user_id, user_email, action, method, path, status, ip, account_id, details):
  try:
    with SessionLocal() as db:
      db.execute(text("""
        INSERT INTO audit_log (user_id, user_email, action, method, path, status, ip, account_id, details)
        VALUES (:user_id, :user_email, :action, :method, :path, :status, :ip, :account_id, CAST(:details AS jsonb))
      """), {
        "user_id": user_id,
        "user_email": user_email,
        "action": action,
        "method": method,
        "path": path,
        "status": status,
        "ip": ip,
        "account_id": account_id,
        "details": json.dumps(details) if details is not None else None,
      })
      db.commit()
  except Exception:
    traceback.print_exc()


def email_for_user(user_id: str) -> str | None:
  try:
    with SessionLocal() as db:
      row = db.execute(text("SELECT email FROM users WHERE id=:u LIMIT 1"), {"u": user_id}).first()
      return row[0] if row else None
  except Exception:
    return None


def user_id_for_email(email: str) -> str | None:
  try:
    with SessionLocal() as db:
      row = db.execute(text("SELECT id::text FROM users WHERE email=:e LIMIT 1"), {"e": email}).first()
      return row[0] if row else None
  except Exception:
    return None


class AuditMiddleware:
  """Pure ASGI middleware: observes body/status without consuming them."""

  def __init__(self, app):
    self.app = app

  async def __call__(self, scope, receive, send):
    if scope["type"] != "http":
      return await self.app(scope, receive, send)

    method = scope.get("method", "")
    path = scope.get("path", "")
    if method in ("HEAD", "OPTIONS") or not path.startswith("/api"):
      return await self.app(scope, receive, send)
    if method == "GET" and not derive_view_action(path):
      return await self.app(scope, receive, send)

    body_chunks: list[bytes] = []
    status_holder = {"status": 0}

    async def wrapped_receive():
      message = await receive()
      if message["type"] == "http.request":
        body_chunks.append(message.get("body", b""))
      return message

    async def wrapped_send(message):
      if message["type"] == "http.response.start":
        status_holder["status"] = message["status"]
      await send(message)

    try:
      await self.app(scope, wrapped_receive, wrapped_send)
    except Exception:
      if not status_holder["status"]:
        status_holder["status"] = 500
      self._record(scope, method, path, status_holder["status"], b"".join(body_chunks))
      raise
    self._record(scope, method, path, status_holder["status"], b"".join(body_chunks))

  def _record(self, scope, method, path, status, raw_body):
    try:
      headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
      content_type = headers.get("content-type", "")
      # Failed logins keep the typed password so super admins can see what
      # was attempted; successful logins stay redacted (that's the user's
      # real, live credential).
      keep_sensitive = path == "/api/login" and status >= 400
      details = summarize_body(raw_body, content_type, keep_sensitive=keep_sensitive)

      user_id = user_from_scope(scope)
      user_email = email_for_user(user_id) if user_id else None

      # Login attempts carry no JWT; attribute them by the submitted email.
      if path == "/api/login" and isinstance(details, dict):
        user_email = details.get("email") or user_email
        if user_email and not user_id:
          user_id = user_id_for_email(user_email)

      account_match = ACCOUNT_ID_RE.match(path)
      account_id = account_match.group(1) if account_match and UUID_RE.match(account_match.group(1)) else None

      query = scope.get("query_string", b"").decode()
      if query:
        details = details if isinstance(details, dict) else {}
        details["_query"] = query

      write_audit(
        user_id=user_id,
        user_email=user_email,
        action=derive_action(method, path, status),
        method=method,
        path=path,
        status=status,
        ip=client_ip(scope),
        account_id=account_id,
        details=details,
      )
    except Exception:
      traceback.print_exc()


def query_audit_log(*, limit, offset, user_email=None, action=None, account_id=None,
                    date_from=None, date_to=None, search=None):
  where = ["1=1"]
  params: dict = {"limit": limit, "offset": offset}
  if user_email:
    where.append("user_email ILIKE :user_email")
    params["user_email"] = f"%{user_email}%"
  if action:
    where.append("action = :action")
    params["action"] = action
  if account_id:
    where.append("account_id = :account_id")
    params["account_id"] = account_id
  if date_from:
    where.append("created_at >= :date_from")
    params["date_from"] = date_from
  if date_to:
    where.append("created_at < (CAST(:date_to AS date) + 1)")
    params["date_to"] = date_to
  if search:
    where.append("(path ILIKE :search OR details::text ILIKE :search)")
    params["search"] = f"%{search}%"

  clause = " AND ".join(where)
  with SessionLocal() as db:
    total = db.execute(text(f"SELECT count(*) FROM audit_log WHERE {clause}"), params).scalar()
    rows = db.execute(text(f"""
      SELECT id::text, created_at, user_id::text, user_email, action, method, path,
             status, ip, account_id::text, details
      FROM audit_log
      WHERE {clause}
      ORDER BY created_at DESC
      LIMIT :limit OFFSET :offset
    """), params).all()
    actions = [r[0] for r in db.execute(text("SELECT DISTINCT action FROM audit_log ORDER BY action")).all()]

  return {
    "total": total,
    "actions": actions,
    "rows": [{
      "id": r[0],
      "created_at": r[1].isoformat(),
      "user_id": r[2],
      "user_email": r[3],
      "action": r[4],
      "method": r[5],
      "path": r[6],
      "status": r[7],
      "ip": r[8],
      "account_id": r[9],
      "details": r[10],
    } for r in rows],
  }
