from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import exception_logging_middleware
from app.services.tenant_context import tenant_context_middleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.middleware("http")(exception_logging_middleware)

    @app.get("/protected")
    def protected():
        return {"ok": True}

    return app


def test_missing_tenant_scope_returns_403_not_500():
    app = _build_app()
    with TestClient(app) as client:
        res = client.get("/protected")
    assert res.status_code == 403
    assert res.json().get("detail") == "missing_tenant_scope"
