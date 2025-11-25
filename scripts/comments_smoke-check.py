#!/usr/bin/env python3
"""
Simple end-to-end smoke check for item comments.

This script assumes the stack is running locally (docker-compose up) and uses
ADMIN_EMAIL/ADMIN_PASSWORD credentials from the .env file to log in. It will:
1) Log in to the API and obtain a token.
2) Find an existing account or create a temporary one if none are available.
3) Create a unique section and item under that account.
4) Post a comment to the section-scoped comments endpoint.
5) Fetch the comments back from both the section-scoped and default item routes
   so you can see whether either path is returning 404s.

Run with:
    ADMIN_EMAIL=... ADMIN_PASSWORD=... API_BASE=http://localhost python3 scripts/comments_smoke_check.py

API_BASE defaults to http://localhost if not set. All created data is temporary
and tied to a short, random slug so you can safely remove it later if desired.
"""

import json
import os
import sys
import uuid
import urllib.error
import urllib.request

API_BASE = os.environ.get("API_BASE", "http://localhost")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
TIMEOUT = 15


def _request(path: str, method: str = "GET", token: str | None = None, body: dict | None = None):
    url = API_BASE.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            text = resp.read()
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                return resp.getcode(), json.loads(text.decode() or "{}")
            return resp.getcode(), text.decode()
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            detail = json.loads(payload)
        except Exception:
            detail = payload or exc.reason
        return exc.code, detail
    except Exception as exc:  # pragma: no cover - manual smoke helper
        return None, str(exc)


def _require_creds():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print("ADMIN_EMAIL and ADMIN_PASSWORD environment variables are required")
        sys.exit(1)


def _print_step(title: str, status, detail):
    symbol = "✅" if status and (isinstance(status, int) and 200 <= status < 300) else "❌"
    print(f"{symbol} {title}: {status} -> {detail}")


def main():
    _require_creds()

    status, resp = _request(
        "/api/login",
        method="POST",
        body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    _print_step("Login", status, resp)
    if status != 200 or not isinstance(resp, dict) or "access_token" not in resp:
        sys.exit(1)
    token = resp["access_token"]

    status, accounts = _request("/api/me/accounts", token=token)
    _print_step("Fetch accounts", status, accounts)
    if status == 200 and isinstance(accounts, list) and accounts:
        account_id = accounts[0]["id"]
    else:
        status, account_resp = _request(
            "/api/accounts",
            method="POST",
            token=token,
            body={"name": f"Temp {uuid.uuid4().hex[:8]}"},
        )
        _print_step("Create account", status, account_resp)
        if status != 201:
            sys.exit(1)
        account_id = account_resp["id"]

    slug = f"smoke-{uuid.uuid4().hex[:6]}"
    status, item_resp = _request(
        f"/api/accounts/{account_id}/sections/{slug}/items",
        method="POST",
        token=token,
        body={"name": "Smoke item", "data": {"note": "testing comments"}},
    )
    _print_step("Create section item", status, item_resp)
    if status != 200 and status != 201:
        sys.exit(1)
    item_id = item_resp["id"]

    status, section_item = _request(
        f"/api/accounts/{account_id}/sections/{slug}/items/{item_id}",
        token=token,
    )
    _print_step("Load section item", status, section_item)

    comment_body = f"Smoke test comment for {item_id}"[:200]
    status, comment_resp = _request(
        f"/api/accounts/{account_id}/sections/{slug}/items/{item_id}/comments",
        method="POST",
        token=token,
        body={"body": comment_body},
    )
    _print_step("Post section comment", status, comment_resp)

    status, comments_in_section = _request(
        f"/api/accounts/{account_id}/sections/{slug}/items/{item_id}/comments",
        token=token,
    )
    _print_step("List section comments", status, comments_in_section)

    status, comments_default = _request(
        f"/api/accounts/{account_id}/items/{item_id}/comments",
        token=token,
    )
    _print_step("List default comments", status, comments_default)

    print("\nLatest comment bodies:")
    for label, payload in (
        ("section", comments_in_section),
        ("default", comments_default),
    ):
        if isinstance(payload, list) and payload:
            print(f" - {label}: {payload[0].get('body')}")
        else:
            print(f" - {label}: no comments returned")


if __name__ == "__main__":
    main()