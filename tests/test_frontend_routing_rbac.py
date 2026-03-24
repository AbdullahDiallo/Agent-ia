from __future__ import annotations

import re
from pathlib import Path


ROUTES_FILE = Path("front/dashboard/src/config/routes.ts")
APP_FILE = Path("front/dashboard/src/App.tsx")


def _route_objects(source: str) -> dict[str, str]:
    pattern = re.compile(r"\{[^{}]*path:\s*'([^']+)'[^{}]*\}", re.DOTALL)
    objects: dict[str, str] = {}
    for match in pattern.finditer(source):
        path = match.group(1)
        objects[path] = match.group(0)
    return objects


def test_sensitive_routes_declare_required_roles():
    source = ROUTES_FILE.read_text(encoding="utf-8")
    route_map = _route_objects(source)

    sensitive_routes = [
        "/dashboard/calls",
        "/dashboard/settings",
        "/dashboard/users",
        "/dashboard/monitoring",
        "/dashboard/persona",
        "/dashboard/notifications",
        "/dashboard/templates",
        "/dashboard/documents",
        "/dashboard/admission",
        "/dashboard/departements",
        "/dashboard/programmes",
        "/dashboard/programmes/:id",
    ]

    for route in sensitive_routes:
        assert route in route_map, f"missing route declaration for {route}"
        assert "requiredRole" in route_map[route], f"missing requiredRole for {route}"


def test_app_builds_protected_routes_from_route_config():
    source = APP_FILE.read_text(encoding="utf-8")
    assert "routeConfigs.filter((route) => route.requiresAuth)" in source
    assert "route.requiredRole ?" in source
    assert "<RoleGuard requiredRole={route.requiredRole}" in source
