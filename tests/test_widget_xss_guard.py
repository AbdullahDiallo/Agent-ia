from __future__ import annotations

from pathlib import Path


WIDGET_FILES = [
    p for p in [
        Path("chatbot-widget.js"),
        Path("front/dashboard/public/chatbot-widget.js"),
        Path("front/dashboard/public/chatbot-widget-voice.js"),
    ] if p.exists()
]


def _extract_add_message_block(source: str) -> str:
    start = source.find("function addMessage(role, content)")
    assert start >= 0, "addMessage function not found"
    end = source.find("function showTyping()", start)
    assert end > start, "showTyping marker not found after addMessage"
    return source[start:end]


def test_widget_add_message_uses_textcontent_only():
    payload = '<img src=x onerror=alert(1)>'
    assert "<img" in payload  # sanity check on malicious payload used for regression intent

    for file_path in WIDGET_FILES:
        source = file_path.read_text(encoding="utf-8")
        block = _extract_add_message_block(source)
        assert "innerHTML" not in block, f"unsafe HTML rendering still present in {file_path}"
        safe_rendering = (
            "textContent = normalizedContent" in block
            or "appendSafeRichText(contentDiv, normalizedContent)" in block
        )
        assert safe_rendering, f"safe message rendering guard missing in {file_path}"
        assert "String(content)" in block, f"content normalization missing in {file_path}"
