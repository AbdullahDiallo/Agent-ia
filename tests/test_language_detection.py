from app.services.lang import detect_language


def test_detect_language_french_sentence_not_marked_unknown():
    assert detect_language("je n'ai pas compris") == "fr"


def test_detect_language_mixed_fr_en_prefers_french_when_french_markers_dominate():
    assert detect_language("Hey, quels sont vos programmes ?") == "fr"


def test_detect_language_english_query():
    assert detect_language("Can you share admission fees and required documents?") == "en"


def test_detect_language_wolof_with_mixed_terms():
    assert detect_language("Naka nga def? dama begg xam frais yi") == "wo"


def test_detect_language_wolof_short_with_marker():
    assert detect_language("waaw program yi") == "wo"


def test_detect_language_unknown_for_gibberish():
    assert detect_language("blabla") == "unknown"


def test_detect_language_french_short_track_name():
    assert detect_language("Genie logiciel") == "fr"


def test_detect_language_french_short_confirmation():
    assert detect_language("oui") == "fr"
