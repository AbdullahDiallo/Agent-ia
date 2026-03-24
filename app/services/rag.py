from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .llm import LLMService
from . import docs as docs_service
from ..logger import get_logger

logger = get_logger(__name__)

# In-memory embedding cache to avoid re-embedding documents on every query.
# Key: document id (str), Value: embedding vector (list[float])
_embedding_cache: Dict[str, List[float]] = {}
_cache_version: int = 0


@dataclass
class RankedDocument:
    id: str
    title: str
    content: str
    tags: str | None
    score: float


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def invalidate_cache() -> None:
    """Clear the embedding cache (call after document updates)."""
    global _embedding_cache, _cache_version
    _embedding_cache.clear()
    _cache_version += 1


async def retrieve_documents(
    db: Session,
    question: str,
    top_k: int = 5,
    *,
    min_score: float = 0.3,
    exclude_tags: Optional[set[str]] = None,
) -> List[RankedDocument]:
    """Retrieve the most relevant documents for a question using semantic search.

    Improvements over V1:
    - Uses cosine similarity instead of L2 distance
    - Caches document embeddings to avoid re-computing on every query
    - Filters by minimum relevance score
    - Supports tag-based exclusion
    - Limits document loading to 500 (configurable)
    """
    global _embedding_cache

    svc = LLMService()
    query_vec = await svc.embed_text(question)

    all_docs = docs_service.list_documents(db, limit=500)
    exclude = exclude_tags or set()

    ranked: List[Tuple[RankedDocument, float]] = []

    for d in all_docs:
        # Skip documents with excluded tags
        if exclude:
            doc_tags = docs_service.parse_document_tags(getattr(d, "tags", None))
            if doc_tags & exclude:
                continue

        doc_id = str(d.id)

        # Use cached embedding if available
        if doc_id in _embedding_cache:
            doc_vec = _embedding_cache[doc_id]
        else:
            doc_text = f"{d.title}\n\n{d.content}"
            # Truncate very long documents to avoid excessive embedding costs
            if len(doc_text) > 8000:
                doc_text = doc_text[:8000]
            doc_vec = await svc.embed_text(doc_text)
            _embedding_cache[doc_id] = doc_vec

        similarity = _cosine_similarity(query_vec, doc_vec)

        if similarity >= min_score:
            ranked.append(
                (
                    RankedDocument(
                        id=doc_id,
                        title=d.title,
                        content=d.content,
                        tags=d.tags,
                        score=round(similarity, 4),
                    ),
                    similarity,
                )
            )

    ranked.sort(key=lambda x: x[1], reverse=True)
    results = [rd for rd, _ in ranked[:top_k]]

    if results:
        logger.info(
            "RAG retrieval completed",
            extra={
                "extra_fields": {
                    "query_length": len(question),
                    "total_docs": len(all_docs),
                    "results_count": len(results),
                    "top_score": results[0].score if results else 0.0,
                    "min_score_threshold": min_score,
                }
            },
        )

    return results


async def retrieve_context_for_llm(
    db: Session,
    question: str,
    *,
    top_k: int = 3,
    max_chars_per_doc: int = 1500,
) -> str:
    """Retrieve documents and format them as a context block for LLM injection."""
    docs = await retrieve_documents(db, question, top_k=top_k)
    if not docs:
        return ""

    parts: list[str] = []
    for i, doc in enumerate(docs, 1):
        content = doc.content.strip()
        if len(content) > max_chars_per_doc:
            content = content[:max_chars_per_doc].rstrip() + "\n[...]"
        parts.append(f"{i}. {doc.title} (pertinence: {doc.score})\n{content}")

    return "Documents pertinents trouvés:\n\n" + "\n\n".join(parts)
