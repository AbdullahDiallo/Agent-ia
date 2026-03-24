from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.middleware import exception_logging_middleware
from app.services.tenant_context import tenant_context_middleware


ALLOWED_ORIGIN = "http://localhost:5173"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.middleware("http")(exception_logging_middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ALLOWED_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/chat/chat")
    def chat():
        return {"ok": True}

    return app


def test_cors_preflight_options_on_fail_closed_public_path_is_allowed():
    app = _build_app()
    with TestClient(app) as client:
        res = client.options(
            "/chat/chat",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN


def test_cors_header_is_present_even_when_tenant_fail_closed_rejects_request():
    app = _build_app()
    with TestClient(app) as client:
        res = client.post(
            "/chat/chat",
            headers={"Origin": ALLOWED_ORIGIN},
            json={"message": "hello"},
        )
    assert res.status_code == 403
    assert res.json().get("detail") == "missing_provider_key"
    assert res.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
