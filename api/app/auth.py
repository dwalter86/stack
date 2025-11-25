import os, datetime
from jose import jwt
from sqlalchemy import text
from database import SessionLocal

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me")
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", 120))

def create_token(sub: str) -> str:
  now = datetime.datetime.utcnow()
  exp = now + datetime.timedelta(minutes=JWT_EXPIRE_MINUTES)
  return jwt.encode({"sub": sub, "exp": exp}, JWT_SECRET, algorithm="HS256")

def login_and_get_user(email: str, password: str):
  with SessionLocal() as db:
    row = db.execute(
      text("""SELECT id::text, is_active
              FROM users
              WHERE email=:e AND crypt(:p, password_hash) = password_hash
              LIMIT 1"""),
      {"e": email, "p": password}
    ).first()
    if not row or not row.is_active:
      return None
    return row.id

def memberships_for_user(user_id: str):
  with SessionLocal() as db:
    rows = db.execute(text("""
      SELECT a.id::text, a.name
      FROM memberships m JOIN accounts a ON a.id = m.account_id
      WHERE m.user_id = :u
      ORDER BY a.created_at DESC
    """), {"u": user_id}).all()
    return [{"id": r[0], "name": r[1]} for r in rows]
