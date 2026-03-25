"""Service de lecture du contenu des pièces jointes (PDF, images, documents).

Extrait le texte des fichiers reçus pour que l'agent IA puisse les comprendre.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Types MIME supportés pour l'extraction de texte
READABLE_CONTENT_TYPES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/html",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

MAX_EXTRACT_CHARS = 3000  # Limite pour ne pas surcharger le prompt LLM


def extract_text_from_file(file_path: str, content_type: str) -> Optional[str]:
    """Extrait le texte lisible d'un fichier stocké.

    Args:
        file_path: Chemin absolu vers le fichier sur disque
        content_type: Type MIME du fichier

    Returns:
        Texte extrait ou None si non supporté / erreur
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning(f"attachment_reader: file not found: {file_path}")
        return None

    ctype = (content_type or "").strip().lower()

    try:
        if ctype == "application/pdf":
            return _extract_pdf(path)
        if ctype in ("text/plain", "text/csv"):
            return _extract_text(path)
        if ctype == "text/html":
            return _extract_html(path)
        if ctype in (
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ):
            return _extract_docx(path)
        # Images — on ne peut pas extraire de texte sans OCR
        if ctype.startswith("image/"):
            return "[Image reçue — contenu visuel non lisible par l'agent]"
        return None
    except Exception as exc:
        logger.error(f"attachment_reader: extraction failed for {file_path}: {exc}", exc_info=True)
        return None


def _extract_pdf(path: Path) -> Optional[str]:
    """Extrait le texte d'un PDF avec pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed — cannot read PDF")
        return None

    text_parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages[:20]:  # Limite à 20 pages
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text.strip())
    full_text = "\n\n".join(text_parts).strip()
    if not full_text:
        return "[PDF reçu mais aucun texte extractible (peut-être un scan/image)]"
    return full_text[:MAX_EXTRACT_CHARS]


def _extract_text(path: Path) -> Optional[str]:
    """Lit un fichier texte brut."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return content.strip()[:MAX_EXTRACT_CHARS] or None
    except Exception:
        return None


def _extract_html(path: Path) -> Optional[str]:
    """Extrait le texte d'un fichier HTML."""
    import re
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        # Supprime les tags HTML
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:MAX_EXTRACT_CHARS] or None
    except Exception:
        return None


def _extract_docx(path: Path) -> Optional[str]:
    """Extrait le texte d'un fichier .docx (ZIP contenant du XML)."""
    import zipfile
    import re
    try:
        with zipfile.ZipFile(str(path)) as zf:
            if "word/document.xml" not in zf.namelist():
                return None
            xml_content = zf.read("word/document.xml").decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", " ", xml_content)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:MAX_EXTRACT_CHARS] or None
    except Exception:
        return None


def format_attachment_content_for_llm(filename: str, content_type: str, extracted_text: Optional[str]) -> str:
    """Formate le contenu extrait pour injection dans le prompt LLM.

    Args:
        filename: Nom du fichier
        content_type: Type MIME
        extracted_text: Texte extrait (ou None)

    Returns:
        Bloc de texte formaté pour le LLM
    """
    if not extracted_text:
        return f"[PIÈCE JOINTE: {filename} ({content_type}) — contenu non lisible]"

    return (
        f"[PIÈCE JOINTE: {filename} ({content_type})]\n"
        f"--- Contenu extrait ---\n"
        f"{extracted_text}\n"
        f"--- Fin du contenu ---"
    )
