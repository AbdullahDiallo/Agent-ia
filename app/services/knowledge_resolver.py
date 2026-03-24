from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from . import docs as docs_service
from .admission_requirements import get_active_policies, get_active_requirements
from .llm_tools import handle_get_track_tuition
from ..models import SchoolDepartment, SchoolProgram, SchoolTrack

FAQ_TAGS = frozenset({
    "faq",
    "snippet",
    "admission_faq",
    "policy_explainer",
    "calendar_faq",
})
RETRIEVAL_TAGS = frozenset({
    "retrieval",
    "longform",
    "guide",
    "handbook",
})
AUTHORITATIVE_DOC_TAGS = frozenset({
    "authoritative",
    "admission_fact",
    "policy_fact",
    "calendar_fact",
})
EXCLUDED_DOC_TAGS = frozenset({
    "persona_core",
    "arguments_commerciaux",
})

_CATALOG_KEYWORDS = (
    "frais",
    "cout",
    "coût",
    "tuition",
    "fee",
    "fees",
    "cost",
    "price",
    "tarif",
    "program",
    "programme",
    "programmes",
    "programs",
    "catalog",
    "catalogue",
    "filiere",
    "filière",
    "filieres",
    "formation",
    "formations",
    "track",
    "tracks",
)
_FOLLOWUP_COST_KEYWORDS = (
    "combien",
    "how much",
    "cost",
    "price",
    "tarif",
    "fees",
    "frais",
)
_REQUIREMENTS_KEYWORDS = (
    "admission",
    "inscription",
    "apply",
    "application",
    "requirements",
    "requirement",
    "conditions",
    "condition",
    "eligibility",
    "eligible",
    "documents",
    "document",
    "pieces",
    "pièces",
    "dossier",
)
_POLICY_KEYWORDS = (
    "policy",
    "policies",
    "rule",
    "rules",
    "refund",
    "refundable",
    "uniform",
    "laptop",
    "mensualite",
    "mensualités",
    "payment",
    "payments",
    "rembourse",
    "tenue",
)
_CALENDAR_KEYWORDS = (
    "calendar",
    "calendrier",
    "dates",
    "date limite",
    "deadline",
    "deadlines",
    "schedule",
    "timeline",
    "planning",
    "rentree",
    "rentrée",
    "waxtu",
)
_RETRIEVAL_HINTS = (
    "comment",
    "how",
    "process",
    "procedure",
    "procédure",
    "details",
    "détails",
    "detail",
    "explain",
    "explique",
    "expliquez",
    "difference",
    "différence",
    "compare",
    "comparison",
    "curriculum",
    "overview",
)
_CATALOG_STOPWORDS = frozenset({
    "quels",
    "quelles",
    "what",
    "which",
    "are",
    "the",
    "de",
    "des",
    "du",
    "pour",
    "for",
    "les",
    "vos",
    "your",
    "sont",
    "frais",
    "fees",
    "fee",
    "tuition",
    "cost",
    "price",
    "program",
    "programme",
    "track",
    "filiere",
    "filière",
    "admission",
})


def _pick_lang_value(lang: str, fr: Optional[str], en: Optional[str], wo: Optional[str]) -> str:
    key = str(lang or "fr").strip().lower()
    if key == "en":
        return en or fr or wo or ""
    if key == "wo":
        return wo or fr or en or ""
    return fr or en or wo or ""


def _normalize_text(value: Any) -> str:
    return docs_service._normalize_text(str(value or ""))


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    haystack = _normalize_text(text)
    return any(_normalize_text(marker) in haystack for marker in markers)


def _conversation_slots(session_state: Optional[Dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(session_state, dict):
        return {}
    slots = session_state.get("conversation_slots")
    if isinstance(slots, dict):
        return slots
    slots = session_state.get("slots_json")
    if isinstance(slots, dict):
        return slots
    return {}


def _preferred_lang(session_state: Optional[Dict[str, Any]]) -> str:
    for key in ("lang_detected", "response_language", "preferred_language"):
        value = str((session_state or {}).get(key) or "").strip().lower()
        if value in {"fr", "en", "wo"}:
            return value
    return "fr"


def _detect_domains(user_text: str, session_state: Optional[Dict[str, Any]]) -> list[str]:
    normalized = _normalize_text(user_text)
    slots = _conversation_slots(session_state)
    has_program_context = bool(
        str(slots.get("track_name") or "").strip()
        or str(slots.get("program_name") or "").strip()
        or str(slots.get("desired_program") or "").strip()
    )

    domains: list[str] = []
    if _contains_any(normalized, _CATALOG_KEYWORDS) or (
        has_program_context and _contains_any(normalized, _FOLLOWUP_COST_KEYWORDS)
    ):
        domains.append("catalog")
    if _contains_any(normalized, _REQUIREMENTS_KEYWORDS):
        domains.append("requirements")
    if _contains_any(normalized, _POLICY_KEYWORDS):
        domains.append("policies")
    if _contains_any(normalized, _CALENDAR_KEYWORDS):
        domains.append("calendar")
    return domains


def _needs_retrieval_support(user_text: str) -> bool:
    return _contains_any(user_text, _RETRIEVAL_HINTS) or len(str(user_text or "").strip()) >= 80


@dataclass
class KnowledgeSnippet:
    title: str
    content: str
    source: str
    source_kind: str
    authoritative: bool = False
    tags: tuple[str, ...] = ()

    def prompt_text(self, *, max_chars: int) -> str:
        title = str(self.title or "").strip() or "Source"
        content = str(self.content or "").strip()
        if len(content) > max_chars:
            content = content[:max_chars].rstrip() + "\n[...]"
        return f"- {title} [{self.source}]\n{content}"


@dataclass
class KnowledgeContext:
    authoritative_facts: list[KnowledgeSnippet] = field(default_factory=list)
    faq_snippets: list[KnowledgeSnippet] = field(default_factory=list)
    retrieval_snippets: list[KnowledgeSnippet] = field(default_factory=list)
    critical_domains: list[str] = field(default_factory=list)

    def source_summary(self) -> dict[str, Any]:
        return {
            "critical_domains": list(self.critical_domains),
            "authoritative_count": len(self.authoritative_facts),
            "faq_count": len(self.faq_snippets),
            "retrieval_count": len(self.retrieval_snippets),
            "authoritative_sources": [item.source for item in self.authoritative_facts],
            "faq_sources": [item.source for item in self.faq_snippets],
            "retrieval_sources": [item.source for item in self.retrieval_snippets],
        }

    def to_prompt_block(self) -> str:
        parts: list[str] = []
        if self.authoritative_facts:
            parts.append(
                "STRUCTURED_TRUTH / AUTHORITATIVE_FACTS:\n"
                + "\n\n".join(item.prompt_text(max_chars=1200) for item in self.authoritative_facts)
            )
        if self.faq_snippets:
            parts.append(
                "CURATED_FAQ_SNIPPETS:\n"
                + "\n\n".join(item.prompt_text(max_chars=900) for item in self.faq_snippets)
            )
        if self.retrieval_snippets:
            parts.append(
                "RETRIEVAL_SUPPORT (supporting context only):\n"
                + "\n\n".join(item.prompt_text(max_chars=1100) for item in self.retrieval_snippets)
            )
        if self.critical_domains and not self.authoritative_facts:
            parts.append(
                "CRITICAL_FACT_STATUS:\n"
                + "- No authoritative facts were found for these critical domains: "
                + ", ".join(self.critical_domains)
                + ". Do not invent missing facts."
            )
        return "\n\n".join(parts).strip()


def _format_catalog_items(items: list[dict[str, Any]], *, lang: str) -> str:
    lines: list[str] = []
    for item in items[:3]:
        track_name = str(item.get("track_name") or "").strip()
        program_name = str(item.get("program_name") or "").strip()
        annual_fee = item.get("annual_fee")
        registration_fee = item.get("registration_fee")
        monthly_fee = item.get("monthly_fee")
        if lang == "en":
            lines.append(
                f"Track: {track_name} | Program: {program_name} | Annual fee: {annual_fee} F CFA | "
                f"Registration fee: {registration_fee} F CFA | Monthly fee: {monthly_fee} F CFA"
            )
        elif lang == "wo":
            lines.append(
                f"Filiere: {track_name} | Program: {program_name} | Cout annuel: {annual_fee} F CFA | "
                f"Droit d'inscription: {registration_fee} F CFA | Mensualite: {monthly_fee} F CFA"
            )
        else:
            lines.append(
                f"Filiere: {track_name} | Programme: {program_name} | Frais annuels: {annual_fee} F CFA | "
                f"Droit d'inscription: {registration_fee} F CFA | Mensualite: {monthly_fee} F CFA"
            )
    return "\n".join(lines)


def _search_catalog_items(db: Session, query: str) -> list[dict[str, Any]]:
    result = handle_get_track_tuition(db, {"query": query})
    if result.get("success"):
        return list(result.get("items") or [])

    query_tokens = {
        token for token in docs_service._tokenize(query)
        if token not in _CATALOG_STOPWORDS
    }
    if not query_tokens:
        return []

    rows = (
        db.query(SchoolTrack, SchoolProgram, SchoolDepartment)
        .join(SchoolProgram, SchoolTrack.program_id == SchoolProgram.id)
        .join(SchoolDepartment, SchoolProgram.department_id == SchoolDepartment.id)
        .filter(SchoolTrack.is_active == True, SchoolProgram.is_active == True)
        .all()
    )
    ranked: list[tuple[int, dict[str, Any]]] = []
    for track, program, department in rows:
        searchable = " ".join(
            [
                _normalize_text(track.name),
                _normalize_text(program.name),
                _normalize_text(department.name),
            ]
        )
        searchable_tokens = set(searchable.split())
        overlap = len(query_tokens & searchable_tokens)
        if overlap <= 0:
            continue
        score = overlap * 4
        if _normalize_text(track.name) in _normalize_text(query):
            score += 10
        if _normalize_text(program.name) in _normalize_text(query):
            score += 6
        ranked.append(
            (
                score,
                {
                    "track_id": str(track.id),
                    "track_name": track.name,
                    "program_name": program.name,
                    "department_name": department.name,
                    "delivery_mode": program.delivery_mode,
                    "annual_fee": float(track.annual_fee),
                    "registration_fee": float(track.registration_fee),
                    "monthly_fee": float(track.monthly_fee),
                    "certifications": track.certifications,
                    "access_level": program.access_level,
                },
            )
        )

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in ranked[:3]]


def _resolve_catalog_facts(
    db: Session,
    *,
    user_text: str,
    session_state: Optional[Dict[str, Any]],
    lang: str,
) -> list[KnowledgeSnippet]:
    slots = _conversation_slots(session_state)
    query = (
        str(slots.get("track_name") or "").strip()
        or str(slots.get("program_name") or "").strip()
        or str(slots.get("desired_program") or "").strip()
        or str(user_text or "").strip()
    )
    if not query:
        return []
    items = _search_catalog_items(db, query)
    if not items:
        return []
    title = {
        "fr": "Catalogue et frais admissions",
        "en": "Programs and tuition",
        "wo": "Program yi ak frais yi",
    }.get(lang, "Catalogue et frais admissions")
    return [
        KnowledgeSnippet(
            title=title,
            content=_format_catalog_items(items, lang=lang),
            source="school_catalog",
            source_kind="structured",
            authoritative=True,
        )
    ]


def _format_requirement_lines(db: Session, *, lang: str) -> str:
    rows = get_active_requirements(db)
    lines: list[str] = []
    for row in rows:
        title = _pick_lang_value(lang, row.title_fr, row.title_en, row.title_wo)
        details = _pick_lang_value(lang, row.details_fr, row.details_en, row.details_wo)
        lines.append(f"• {title}: {details}" if details else f"• {title}")
    return "\n".join(lines).strip()


def _format_policy_lines(db: Session, *, lang: str, deadline_only: bool = False) -> str:
    rows = get_active_policies(db)
    lines: list[str] = []
    for row in rows:
        text = _pick_lang_value(lang, row.text_fr, row.text_en, row.text_wo)
        code = _normalize_text(getattr(row, "code", ""))
        normalized_text = _normalize_text(text)
        if deadline_only and not (
            "deadline" in code
            or "deadline" in normalized_text
            or "date limite" in normalized_text
            or "05" in normalized_text
        ):
            continue
        if text:
            lines.append(f"• {text}")
    return "\n".join(lines).strip()


def _resolve_structured_admission_facts(
    db: Session,
    *,
    lang: str,
    include_requirements: bool,
    include_policies: bool,
    include_calendar: bool,
) -> list[KnowledgeSnippet]:
    facts: list[KnowledgeSnippet] = []
    if include_requirements:
        content = _format_requirement_lines(db, lang=lang)
        if content:
            facts.append(
                KnowledgeSnippet(
                    title={
                        "fr": "Documents requis",
                        "en": "Required documents",
                        "wo": "Dokimaa yi ñuy laaj",
                    }.get(lang, "Documents requis"),
                    content=content,
                    source="school_admission_requirements",
                    source_kind="structured",
                    authoritative=True,
                )
            )
    if include_policies:
        content = _format_policy_lines(db, lang=lang)
        if content:
            facts.append(
                KnowledgeSnippet(
                    title={
                        "fr": "Politiques et conditions",
                        "en": "Policies and conditions",
                        "wo": "Politik ak sart yi",
                    }.get(lang, "Politiques et conditions"),
                    content=content,
                    source="school_admission_policies",
                    source_kind="structured",
                    authoritative=True,
                )
            )
    if include_calendar:
        content = _format_policy_lines(db, lang=lang, deadline_only=True)
        if content:
            facts.append(
                KnowledgeSnippet(
                    title={
                        "fr": "Dates et echeances connues",
                        "en": "Known dates and deadlines",
                        "wo": "Dates ak deadlines yu nu xam",
                    }.get(lang, "Dates et echeances connues"),
                    content=content,
                    source="school_admission_calendar_policies",
                    source_kind="structured",
                    authoritative=True,
                )
            )
    return facts


def _doc_to_snippet(doc, *, source_kind: str, source: str, authoritative: bool) -> KnowledgeSnippet:
    tags = tuple(sorted(docs_service.parse_document_tags(getattr(doc, "tags", None))))
    return KnowledgeSnippet(
        title=str(getattr(doc, "title", "") or "").strip() or "Document",
        content=str(getattr(doc, "content", "") or "").strip(),
        source=source,
        source_kind=source_kind,
        authoritative=authoritative,
        tags=tags,
    )


def resolve_knowledge_context(
    db: Session,
    *,
    user_text: str,
    session_state: Optional[Dict[str, Any]] = None,
) -> KnowledgeContext:
    lang = _preferred_lang(session_state)
    critical_domains = _detect_domains(user_text, session_state)
    authoritative_facts: list[KnowledgeSnippet] = []
    faq_snippets: list[KnowledgeSnippet] = []
    retrieval_snippets: list[KnowledgeSnippet] = []
    seen_doc_ids: set[str] = set()

    if "catalog" in critical_domains:
        authoritative_facts.extend(
            _resolve_catalog_facts(db, user_text=user_text, session_state=session_state, lang=lang)
        )

    if any(domain in critical_domains for domain in {"requirements", "policies", "calendar"}):
        authoritative_facts.extend(
            _resolve_structured_admission_facts(
                db,
                lang=lang,
                include_requirements=("requirements" in critical_domains),
                include_policies=("policies" in critical_domains),
                include_calendar=("calendar" in critical_domains),
            )
        )

    if any(domain in critical_domains for domain in {"policies", "calendar"}):
        authoritative_docs = docs_service.search_tagged_documents(
            db,
            query=user_text,
            include_tags=AUTHORITATIVE_DOC_TAGS,
            exclude_tags=EXCLUDED_DOC_TAGS,
            preferred_lang=lang,
            limit=2,
        )
        for doc in authoritative_docs:
            doc_id = str(getattr(doc, "id", ""))
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            authoritative_facts.append(
                _doc_to_snippet(
                    doc,
                    source_kind="authoritative_doc",
                    source="knowledge_documents_authoritative",
                    authoritative=True,
                )
            )

    faq_docs = docs_service.search_tagged_documents(
        db,
        query=user_text,
        include_tags=FAQ_TAGS,
        exclude_tags=EXCLUDED_DOC_TAGS | AUTHORITATIVE_DOC_TAGS,
        preferred_lang=lang,
        limit=2,
    )
    for doc in faq_docs:
        doc_id = str(getattr(doc, "id", ""))
        if doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc_id)
        faq_snippets.append(
            _doc_to_snippet(
                doc,
                source_kind="faq",
                source="knowledge_documents_faq",
                authoritative=False,
            )
        )

    if _needs_retrieval_support(user_text):
        retrieval_docs = docs_service.search_tagged_documents(
            db,
            query=user_text,
            include_tags=RETRIEVAL_TAGS,
            exclude_tags=EXCLUDED_DOC_TAGS | AUTHORITATIVE_DOC_TAGS,
            preferred_lang=lang,
            limit=2,
        )
        for doc in retrieval_docs:
            doc_id = str(getattr(doc, "id", ""))
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            retrieval_snippets.append(
                _doc_to_snippet(
                    doc,
                    source_kind="retrieval",
                    source="knowledge_documents_retrieval",
                    authoritative=False,
                )
            )

    return KnowledgeContext(
        authoritative_facts=authoritative_facts,
        faq_snippets=faq_snippets,
        retrieval_snippets=retrieval_snippets,
        critical_domains=critical_domains,
    )
