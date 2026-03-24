from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..models import SchoolAdmissionPolicy, SchoolAdmissionRequirement


def _pick_lang_value(lang: str, fr: Optional[str], en: Optional[str], wo: Optional[str]) -> str:
    key = (lang or "fr").strip().lower()
    if key == "en":
        return en or fr or ""
    if key == "wo":
        return wo or fr or ""
    return fr or en or wo or ""


def get_active_requirements(db: Session) -> list[SchoolAdmissionRequirement]:
    return (
        db.query(SchoolAdmissionRequirement)
        .filter(SchoolAdmissionRequirement.is_active == True)
        .order_by(SchoolAdmissionRequirement.sort_order.asc(), SchoolAdmissionRequirement.created_at.asc())
        .all()
    )


def get_active_policies(db: Session) -> list[SchoolAdmissionPolicy]:
    return (
        db.query(SchoolAdmissionPolicy)
        .filter(SchoolAdmissionPolicy.is_active == True)
        .order_by(SchoolAdmissionPolicy.sort_order.asc(), SchoolAdmissionPolicy.created_at.asc())
        .all()
    )


def format_requirements_for_channel(
    db: Session,
    *,
    lang: str = "fr",
    with_policies: bool = True,
    bullet_prefix: str = "- ",
) -> str:
    requirements = get_active_requirements(db)
    policies = get_active_policies(db) if with_policies else []

    lines: list[str] = []
    if requirements:
        header = {
            "fr": "Pieces a fournir:",
            "en": "Required documents:",
            "wo": "Dokimaa yi ñuy laaj:",
        }.get((lang or "fr").strip().lower(), "Pieces a fournir:")
        lines.append(header)
        for row in requirements:
            title = _pick_lang_value(lang, row.title_fr, row.title_en, row.title_wo)
            details = _pick_lang_value(lang, row.details_fr, row.details_en, row.details_wo)
            if details:
                lines.append(f"{bullet_prefix}{title}: {details}")
            else:
                lines.append(f"{bullet_prefix}{title}")

    if with_policies and policies:
        header = {
            "fr": "Conditions generales:",
            "en": "General conditions:",
            "wo": "Sartu yooyu:",
        }.get((lang or "fr").strip().lower(), "Conditions generales:")
        lines.append("")
        lines.append(header)
        for row in policies:
            text = _pick_lang_value(lang, row.text_fr, row.text_en, row.text_wo)
            if text:
                lines.append(f"{bullet_prefix}{text}")

    return "\n".join([line for line in lines if line is not None]).strip()


def _upsert_requirement(
    db: Session,
    *,
    code: str,
    title_fr: str,
    details_fr: Optional[str],
    sort_order: int,
    title_en: Optional[str] = None,
    title_wo: Optional[str] = None,
    details_en: Optional[str] = None,
    details_wo: Optional[str] = None,
    is_required: bool = True,
) -> SchoolAdmissionRequirement:
    row = db.query(SchoolAdmissionRequirement).filter(SchoolAdmissionRequirement.code == code).first()
    if not row:
        row = SchoolAdmissionRequirement(code=code)
    row.title_fr = title_fr
    row.title_en = title_en
    row.title_wo = title_wo
    row.details_fr = details_fr
    row.details_en = details_en
    row.details_wo = details_wo
    row.sort_order = sort_order
    row.is_required = is_required
    row.is_active = True
    db.add(row)
    db.flush()
    return row


def _upsert_policy(
    db: Session,
    *,
    code: str,
    text_fr: str,
    sort_order: int,
    text_en: Optional[str] = None,
    text_wo: Optional[str] = None,
) -> SchoolAdmissionPolicy:
    row = db.query(SchoolAdmissionPolicy).filter(SchoolAdmissionPolicy.code == code).first()
    if not row:
        row = SchoolAdmissionPolicy(code=code)
    row.text_fr = text_fr
    row.text_en = text_en
    row.text_wo = text_wo
    row.sort_order = sort_order
    row.is_active = True
    db.add(row)
    db.flush()
    return row


def seed_default_admission_rules(db: Session) -> dict:
    # Pieces a fournir (image fournie)
    _upsert_requirement(
        db,
        code="photos_identite",
        title_fr="Photos d'identite",
        details_fr="2 photos d'identite recentes.",
        sort_order=10,
        title_en="ID photos",
        details_en="2 recent passport-size photos.",
        title_wo="Nataal yu identité",
        details_wo="2 nataal yu identité yu bees yi.",
    )
    _upsert_requirement(
        db,
        code="copie_cni",
        title_fr="Copie de CNI",
        details_fr="1 copie de la CNI.",
        sort_order=20,
        title_en="National ID copy",
        details_en="1 copy of national ID card.",
        title_wo="Kopi CNI",
        details_wo="1 kopi CNI bi.",
    )
    _upsert_requirement(
        db,
        code="diplomes_legalises",
        title_fr="Diplomes legalises",
        details_fr="Photocopies legalisees des derniers diplomes.",
        sort_order=30,
        title_en="Certified diplomas",
        details_en="Certified copies of latest diplomas.",
        title_wo="Diploom yu legaliseer",
        details_wo="Fotokopi yu legaliseer ci sa diplôm yu mujj yi.",
    )

    # Conditions generales (image fournie)
    _upsert_policy(
        db,
        code="mensualite_deadline",
        text_fr="Les mensualites sont payables au debut de chaque mois et au plus tard le 05.",
        sort_order=10,
        text_en="Monthly payments are due at the start of each month and no later than the 5th.",
        text_wo="Mensing yi dañuy fey ci tàmbali weer wi te bu yàgg ba ci 05.",
    )
    _upsert_policy(
        db,
        code="tenue_obligatoire",
        text_fr="La tenue est obligatoire pour la 1ere et la 2eme annee (70 000 F).",
        sort_order=20,
        text_en="Uniform is mandatory for 1st and 2nd year (70,000 F).",
        text_wo="Tenue bi war na ci at mi 1e ak 2e (70 000 F).",
    )
    _upsert_policy(
        db,
        code="non_remboursement",
        text_fr="En cas d'abandon ou de desistement, les sommes encaissees ne sont pas remboursables.",
        sort_order=30,
        text_en="In case of withdrawal, paid amounts are non-refundable.",
        text_wo="Su fekkee am na bañe walla jëlal, xaalis yi ñu fey kenn du ko delloo.",
    )
    _upsert_policy(
        db,
        code="laptop_obligatoire",
        text_fr="Ordinateur portable obligatoire.",
        sort_order=40,
        text_en="Laptop is mandatory.",
        text_wo="Ordinater portable war na.",
    )

    db.commit()
    return {
        "requirements": len(get_active_requirements(db)),
        "policies": len(get_active_policies(db)),
    }
