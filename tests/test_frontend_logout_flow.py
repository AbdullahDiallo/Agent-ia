from __future__ import annotations

from pathlib import Path


def test_frontend_logout_calls_backend_endpoint():
    source = Path("front/dashboard/src/hooks/useAuth.tsx").read_text(encoding="utf-8")
    assert "/auth/logout" in source
    assert "method: 'POST'" in source
    assert "credentials: 'include'" in source
