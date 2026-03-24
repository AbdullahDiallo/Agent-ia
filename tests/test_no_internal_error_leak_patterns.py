from __future__ import annotations

from pathlib import Path


def test_no_detail_str_e_pattern_in_backend():
    root = Path("app")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if "detail=str(e)" in content:
            offenders.append(str(path))
    assert not offenders, f"detail=str(e) still present: {offenders}"
