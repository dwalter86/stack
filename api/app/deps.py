import os
from fastapi import HTTPException, Security, status, Request, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from sqlalchemy import text
from database import SessionLocal

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me")
API_IP_ALLOWLIST = [s.strip() for s in os.environ.get("API_IP_ALLOWLIST", "").split(",") if s.strip()]

bearer_scheme = HTTPBearer(auto_error=False)

async def ip_allowlist(request: Request):
  if not API_IP_ALLOWLIST:
    return
  if request.client.host not in API_IP_ALLOWLIST:
    raise HTTPException(status_code=403, detail="IP not allowed")

async def current_user(credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme)) -> str:
  if not credentials or credentials.scheme.lower() != "bearer" or not credentials.credentials:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
  token = credentials.credentials
  try:
    payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    return payload["sub"]
  except JWTError:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

def _get_user_type(user_id: str) -> str:
  with SessionLocal() as db:
    row = db.execute(text("SELECT COALESCE(user_type, CASE WHEN is_admin THEN 'admin' ELSE 'standard' END) FROM users WHERE id=:u LIMIT 1"), {"u": user_id}).first()
    return row[0] if row else "standard"

async def require_admin(user_id: str = Depends(current_user)) -> dict:
  user_type = _get_user_type(user_id)
  if user_type not in ("admin", "super_admin"):
    raise HTTPException(status_code=403, detail="Admin only")
  return {"id": user_id, "user_type": user_type}

async def require_super_admin(user_id: str = Depends(current_user)) -> dict:
  user_type = _get_user_type(user_id)
  if user_type != "super_admin":
    raise HTTPException(status_code=403, detail="Super admin only")
  return {"id": user_id, "user_type": user_type}
