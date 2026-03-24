from app.routers.email_handler import (
    _build_email_thread_key,
    _normalize_email_reply_text,
    _render_email_reply_html,
)


def test_email_thread_key_prefers_references_root():
    data = {
        "References": "<root-thread@example.com> <reply-1@example.com>",
        "In-Reply-To": "<reply-1@example.com>",
        "Message-ID": "<reply-2@example.com>",
    }

    thread_key = _build_email_thread_key(
        clean_email="candidate@example.com",
        subject="Re: Admission Data Science",
        data=data,
    )

    assert thread_key == "email:candidate@example.com:root-thread@example.com"


def test_email_thread_key_falls_back_to_in_reply_to_then_subject():
    in_reply_thread = _build_email_thread_key(
        clean_email="candidate@example.com",
        subject="Re: Admission Data Science",
        data={"In-Reply-To": "<root-thread@example.com>"},
    )
    subject_thread = _build_email_thread_key(
        clean_email="candidate@example.com",
        subject="Re: Admission Data Science",
        data={},
    )

    assert in_reply_thread == "email:candidate@example.com:root-thread@example.com"
    assert subject_thread == "email:candidate@example.com:admission data science"


def test_email_reply_text_normalization_collapses_extra_blank_lines():
    normalized = _normalize_email_reply_text("Bonjour,\n\n\nVoici les points utiles.\n\n\n- Frais\n- Admission\n")

    assert normalized == "Bonjour,\n\nVoici les points utiles.\n\n- Frais\n- Admission"


def test_email_reply_html_renders_simple_lists_and_escapes_html():
    html = _render_email_reply_html("Bonjour,\n\n1. Frais < 1 000 000\n2. Admission & inscription")

    assert "<ul>" in html
    assert "<li>Frais &lt; 1 000 000</li>" in html
    assert "<li>Admission &amp; inscription</li>" in html
    assert "Salma - Admissions" in html
