from __future__ import annotations

from typing import Literal
import re
import unicodedata

LangCode = Literal["fr", "en", "wo", "unknown"]


TOKEN_RE = re.compile(r"[a-z']+")


FRENCH_KEYWORDS = {
    "bonjour",
    "bonsoir",
    "salut",
    "oui",
    "non",
    "merci",
    "svp",
    "sil",
    "ecole",
    "etablissement",
    "filiere",
    "formation",
    "inscription",
    "admission",
    "dossier",
    "entretien",
    "calendrier",
    "mensualite",
    "frais",
    "scolarite",
    "etudiant",
    "parent",
    "candidat",
    "programme",
    "programmes",
    "genie",
    "logiciel",
    "rendezvous",
    "rdv",
}

FRENCH_STOPWORDS = {
    "je",
    "j",
    "tu",
    "il",
    "elle",
    "nous",
    "vous",
    "vos",
    "votre",
    "quels",
    "quelles",
    "quelle",
    "quel",
    "est",
    "sont",
    "ai",
    "pas",
    "ne",
    "de",
    "du",
    "des",
    "les",
    "le",
    "la",
    "un",
    "une",
    "pour",
    "avec",
    "sur",
    "dans",
}

FRENCH_PHRASES = {
    "s il vous plait",
    "rendez vous",
    "frais d inscription",
    "conditions d admission",
    "pieces a fournir",
    "je n ai pas compris",
    "genie logiciel",
}


ENGLISH_KEYWORDS = {
    "hello",
    "hi",
    "hey",
    "thanks",
    "thank",
    "please",
    "appointment",
    "school",
    "admission",
    "enrollment",
    "program",
    "programs",
    "track",
    "tuition",
    "fees",
    "student",
    "parent",
    "candidate",
    "documents",
    "deadline",
    "available",
    "price",
}

ENGLISH_STOPWORDS = {
    "i",
    "you",
    "your",
    "yours",
    "my",
    "me",
    "we",
    "they",
    "is",
    "are",
    "what",
    "which",
    "how",
    "can",
    "could",
    "do",
    "does",
    "for",
    "with",
    "the",
    "a",
    "an",
    "to",
    "of",
}

ENGLISH_PHRASES = {
    "good morning",
    "good afternoon",
    "good evening",
    "thank you",
    "admission requirements",
    "enrollment fees",
    "required documents",
}


WOLOF_KEYWORDS = {
    "naka",
    "nanga",
    "mangi",
    "maangi",
    "dama",
    "damay",
    "dina",
    "jamm",
    "jerrejef",
    "jerejef",
    "asalaam",
    "salaam",
    "aleekum",
    "maleekum",
    "maalekum",
    "waaleikum",
    "waalekum",
    "waaw",
    "waw",
    "wax",
    "waxtu",
    "xam",
    "dekk",
    "ekool",
    "filiere",
    "inscription",
    "admission",
    "dossier",
    "frais",
    "scolarite",
    "rendezvous",
    "rdv",
    "yalla",
    "baax",
    "begg",
}

WOLOF_STOPWORDS = {
    "nga",
    "yi",
    "ci",
    "ak",
    "te",
    "la",
    "lan",
    "ndax",
    "fi",
    "fii",
    "rekk",
}

WOLOF_PHRASES = {
    "naka nga def",
    "nanga def",
    "mangi fi",
    "maangi fi",
    "jamm rekk",
    "ba beneen yoon",
    "salaam aleekum",
    "asalaam aleekum",
    "ndax frais yi",
    "dossier inscription",
}


def _normalize_text(text: str) -> str:
    lowered = (text or "").lower().replace("’", "'")
    # Remove accents/diacritics to match keyword sets consistently.
    normalized = unicodedata.normalize("NFKD", lowered)
    ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    ascii_like = re.sub(r"[^a-z'\s-]", " ", ascii_like)
    ascii_like = re.sub(r"\s+", " ", ascii_like).strip()
    return ascii_like


def _tokenize(text: str) -> list[str]:
    tokens = [t.strip("'") for t in TOKEN_RE.findall(_normalize_text(text))]
    return [t for t in tokens if t]


def _score_language(
    tokens: list[str],
    phrases: set[str],
    keywords: set[str],
    stopwords: set[str],
) -> float:
    if not tokens:
        return 0.0
    normalized = " ".join(tokens)
    padded = f" {normalized} "
    score = 0.0
    for phrase in phrases:
        if f" {phrase} " in padded:
            score += 1.8
    for token in tokens:
        if token in keywords:
            score += 1.0
        elif token in stopwords:
            score += 0.35
    return score


def _count_hits(tokens: list[str], bag: set[str]) -> int:
    return sum(1 for token in tokens if token in bag)


def detect_language(text: str) -> LangCode:
    """Lightweight FR/EN/WO detection tuned for admissions chatbot inputs.

    Returns "unknown" only when no reliable signal is found.
    """
    tokens = _tokenize(text)
    if not tokens:
        return "unknown"

    fr_score = _score_language(tokens, FRENCH_PHRASES, FRENCH_KEYWORDS, FRENCH_STOPWORDS)
    en_score = _score_language(tokens, ENGLISH_PHRASES, ENGLISH_KEYWORDS, ENGLISH_STOPWORDS)
    wo_score = _score_language(tokens, WOLOF_PHRASES, WOLOF_KEYWORDS, WOLOF_STOPWORDS)

    scores = {"fr": fr_score, "en": en_score, "wo": wo_score}
    best_lang, best_score = max(scores.items(), key=lambda kv: kv[1])

    # Strong Wolof markers should win even in mixed inputs.
    wolof_hits = _count_hits(tokens, WOLOF_KEYWORDS | WOLOF_STOPWORDS)
    if wolof_hits >= 2 and wo_score >= max(fr_score, en_score):
        return "wo"

    # Tie-break FR vs EN using marker counts when scores are close.
    if abs(fr_score - en_score) <= 0.35 and max(fr_score, en_score) > 0:
        fr_hits = _count_hits(tokens, FRENCH_KEYWORDS | FRENCH_STOPWORDS)
        en_hits = _count_hits(tokens, ENGLISH_KEYWORDS | ENGLISH_STOPWORDS)
        return "fr" if fr_hits >= en_hits else "en"

    if best_score <= 0:
        return "unknown"

    # For tiny low-signal messages, keep unknown (prevents random misclassification).
    if best_score < 0.7 and len(tokens) <= 1:
        return "unknown"

    return best_lang  # type: ignore[return-value]


def unsupported_language_message() -> str:
    return (
        "Desole, je ne comprends pas votre langue pour le moment. "
        "Je vais transmettre votre demande au service admission. "
        "Sorry, I don't understand your language yet. I will transfer your request to the admissions team."
    )
