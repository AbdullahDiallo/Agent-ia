from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.db import get_db


def _request_for_path(path: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 5100),
        "server": ("testserver", 443),
        "scheme": "https",
        "state": {"tenant_id": None},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def test_get_db_allows_public_auth_login_without_tenant():
    request = _request_for_path("/auth/login")
    dependency = get_db(request)
    db = next(dependency)
    try:
        assert db.info.get("tenant_id") is None
        assert db.info.get("allow_unscoped_tenant") is True
    finally:
        dependency.close()


def test_get_db_keeps_private_routes_fail_closed_without_tenant():
    request = _request_for_path("/notifications/recent")
    dependency = get_db(request)
    with pytest.raises(HTTPException) as exc:
        next(dependency)
    assert exc.value.status_code == 403
    assert exc.value.detail == "missing_tenant_scope"
