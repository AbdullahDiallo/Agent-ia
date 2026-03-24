from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_

from ..models import Document


_TAG_SPLIT_RE = re.compile(r"[\s,;|]+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LANG_CODES = {"fr", "en", "wo"}


def create_document(db: Session, *, title: str, content: str, tags: Optional[str] = None) -> Document:
    doc = Document(title=title, content=content, tags=tags)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def list_documents(db: Session, limit: int = 100) -> List[Document]:
    return db.query(Document).order_by(Document.created_at.desc()).limit(limit).all()


def search_documents(db: Session, query: str, limit: int = 10) -> List[Document]:
    q = f"%{query}%"
    return (
        db.query(Document)
        .filter(or_(Document.title.ilike(q), Document.content.ilike(q), (Document.tags.ilike(q))))
        .order_by(Document.created_at.desc())
        .limit(limit)
        .all()
    )


def parse_document_tags(raw_tags: Optional[str]) -> set[str]:
    raw = str(raw_tags or "").strip().lower()
    if not raw:
        return set()
    return {token for token in _TAG_SPLIT_RE.split(raw) if token}


def _normalize_text(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_like.split())


def _tokenize(value: str) -> set[str]:
    normalized = _normalize_text(value)
    if not normalized:
        return set()
    return {token for token in _TOKEN_RE.findall(normalized) if len(token) > 1}


def _tags_match_language(tags: set[str], preferred_lang: Optional[str]) -> bool:
    lang = str(preferred_lang or "").strip().lower()
    if lang not in _LANG_CODES:
        return False
    return any(
        tag == lang
        or tag == f"lang:{lang}"
        or tag.endswith(f":{lang}")
        or tag.endswith(f"_{lang}")
        for tag in tags
    )


def _tags_declare_other_language(tags: set[str], preferred_lang: Optional[str]) -> bool:
    lang = str(preferred_lang or "").strip().lower()
    if lang not in _LANG_CODES:
        return False
    for candidate in _LANG_CODES - {lang}:
        if _tags_match_language(tags, candidate):
            return True
    return False


def _document_search_score(
    doc: Document,
    *,
    query: str,
    include_tags: set[str],
    preferred_lang: Optional[str],
) -> int:
    tags = parse_document_tags(doc.tags)
    title = str(getattr(doc, "title", "") or "")
    content = str(getattr(doc, "content", "") or "")

    score = 0
    if include_tags:
        score += len(tags & include_tags) * 3

    if _tags_match_language(tags, preferred_lang):
        score += 4
    elif preferred_lang and _tags_declare_other_language(tags, preferred_lang):
        score -= 2

    query_normalized = _normalize_text(query)
    if not query_normalized:
        return score

    title_normalized = _normalize_text(title)
    content_normalized = _normalize_text(content)
    query_tokens = _tokenize(query_normalized)
    title_tokens = _tokenize(title_normalized)
    content_tokens = _tokenize(content_normalized)
    tag_tokens = {_normalize_text(tag) for tag in tags}

    if query_normalized and query_normalized in title_normalized:
        score += 10
    if query_normalized and query_normalized in content_normalized:
        score += 4

    overlap = len(query_tokens & title_tokens)
    if overlap:
        score += overlap * 4
    overlap = len(query_tokens & content_tokens)
    if overlap:
        score += overlap * 2
    overlap = len(query_tokens & tag_tokens)
    if overlap:
        score += overlap * 3
    return score


def search_tagged_documents(
    db: Session,
    *,
    query: str,
    include_tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
    preferred_lang: Optional[str] = None,
    limit: int = 10,
    scan_limit: int = 250,
) -> List[Document]:
    include = {str(tag or "").strip().lower() for tag in (include_tags or []) if str(tag or "").strip()}
    exclude = {str(tag or "").strip().lower() for tag in (exclude_tags or []) if str(tag or "").strip()}

    ranked: list[tuple[int, object, Document]] = []
    for doc in list_documents(db, limit=scan_limit):
        tags = parse_document_tags(doc.tags)
        if include and not (tags & include):
            continue
        if exclude and (tags & exclude):
            continue
        score = _document_search_score(
            doc,
            query=query,
            include_tags=include,
            preferred_lang=preferred_lang,
        )
        if score <= 0 and str(query or "").strip():
            continue
        ranked.append((score, getattr(doc, "created_at", None), doc))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [doc for _, _, doc in ranked[:limit]]


def get_document_by_tag(db: Session, tag: str) -> Optional[Document]:
    """Retourne le document le plus récent dont 'tags' contient le tag donné."""
    pattern = f"%{tag}%"
    return (
        db.query(Document)
        .filter(Document.tags.ilike(pattern))
        .order_by(Document.created_at.desc())
        .first()
    )
