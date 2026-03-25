"""Générateur de PDF de confirmation de rendez-vous avec QR code.

Produit un PDF professionnel contenant :
- Logo / en-tête de l'établissement
- Détails du rendez-vous (date, heure, filière, programme)
- Pièces à fournir
- QR code avec les infos du RDV (expire après la date du RDV)
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def generate_appointment_pdf(
    *,
    person_name: str,
    person_email: Optional[str] = None,
    person_phone: Optional[str] = None,
    track_name: str,
    program_name: str,
    department_name: str = "",
    appointment_date: str,
    appointment_time: str,
    appointment_id: str,
    agent_name: Optional[str] = None,
    requirements_text: str = "",
    school_name: str = "Établissement Scolaire",
    secret_key: str = "agentia-scolaire-default-key",
) -> bytes:
    """Génère un PDF de confirmation de rendez-vous.

    Returns:
        Contenu du PDF en bytes
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm, mm
        from reportlab.platypus import (
            Image,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        logger.error("reportlab not installed — cannot generate PDF")
        raise

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    elements: list[Any] = []

    # --- Styles personnalisés ---
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=22,
        textColor=colors.HexColor("#1a365d"),
        spaceAfter=6,
        alignment=1,  # centré
    )
    subtitle_style = ParagraphStyle(
        "CustomSubtitle",
        parent=styles["Normal"],
        fontSize=11,
        textColor=colors.HexColor("#4a5568"),
        alignment=1,
        spaceAfter=20,
    )
    section_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=colors.HexColor("#2d3748"),
        spaceBefore=16,
        spaceAfter=8,
        borderWidth=0,
        borderPadding=0,
    )
    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#2d3748"),
        leading=14,
    )
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#a0aec0"),
        alignment=1,
        spaceBefore=30,
    )

    # --- En-tête ---
    elements.append(Paragraph(school_name, title_style))
    elements.append(Paragraph("Confirmation de Rendez-vous Admission", subtitle_style))

    # --- Ligne de séparation ---
    line_data = [["" * 80]]
    line_table = Table(line_data, colWidths=[16 * cm])
    line_table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 2, colors.HexColor("#3182ce")),
    ]))
    elements.append(line_table)
    elements.append(Spacer(1, 12))

    # --- Infos candidat ---
    elements.append(Paragraph("📋 Informations du candidat", section_style))
    candidate_data = [
        ["Nom complet", person_name],
    ]
    if person_email:
        candidate_data.append(["Email", person_email])
    if person_phone:
        candidate_data.append(["Téléphone", person_phone])

    candidate_table = Table(candidate_data, colWidths=[5 * cm, 11 * cm])
    candidate_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#ebf8ff")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#2b6cb0")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bee3f8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(candidate_table)
    elements.append(Spacer(1, 12))

    # --- Détails du RDV ---
    elements.append(Paragraph("📅 Détails du rendez-vous", section_style))
    rdv_data = [
        ["Date", appointment_date],
        ["Heure", appointment_time],
        ["Programme", program_name],
        ["Filière", track_name],
    ]
    if department_name:
        rdv_data.append(["Département", department_name])
    if agent_name:
        rdv_data.append(["Conseiller", agent_name])
    rdv_data.append(["Référence", appointment_id[:8].upper()])

    rdv_table = Table(rdv_data, colWidths=[5 * cm, 11 * cm])
    rdv_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0fff4")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#276749")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c6f6d5")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(rdv_table)
    elements.append(Spacer(1, 12))

    # --- Pièces à fournir ---
    if requirements_text:
        elements.append(Paragraph("📎 Pièces à fournir le jour du rendez-vous", section_style))
        for line in requirements_text.strip().split("\n"):
            line = line.strip()
            if line:
                if line.startswith("•") or line.startswith("-"):
                    elements.append(Paragraph(f"  {line}", body_style))
                else:
                    elements.append(Paragraph(f"  • {line}", body_style))
        elements.append(Spacer(1, 12))

    # --- QR Code ---
    elements.append(Paragraph("🔐 QR Code de vérification", section_style))
    elements.append(Paragraph(
        "Présentez ce QR code le jour de votre rendez-vous. "
        "Il sera invalide après la date du rendez-vous.",
        body_style,
    ))
    elements.append(Spacer(1, 8))

    qr_image = _generate_qr_code(
        appointment_id=appointment_id,
        person_name=person_name,
        track_name=track_name,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        secret_key=secret_key,
    )
    if qr_image:
        elements.append(qr_image)
    elements.append(Spacer(1, 8))

    # --- Footer ---
    elements.append(Paragraph(
        f"Document généré automatiquement par {school_name} — "
        f"Réf: {appointment_id[:8].upper()} — "
        "Ce document est confidentiel et destiné uniquement au candidat nommé ci-dessus.",
        footer_style,
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    logger.info(
        "appointment_pdf_generated",
        extra={"extra_fields": {
            "appointment_id": appointment_id,
            "person_name": person_name,
            "pdf_size_bytes": len(pdf_bytes),
        }},
    )
    return pdf_bytes


def _generate_qr_code(
    *,
    appointment_id: str,
    person_name: str,
    track_name: str,
    appointment_date: str,
    appointment_time: str,
    secret_key: str,
) -> Any:
    """Génère un QR code contenant les infos du RDV + signature HMAC pour vérification.

    Le QR code contient un JSON avec :
    - Les infos du RDV
    - Un champ `expires` (date du RDV)
    - Une signature HMAC pour empêcher la falsification
    """
    try:
        import qrcode
        from reportlab.lib.units import cm
        from reportlab.platypus import Image
    except ImportError:
        logger.warning("qrcode or reportlab not installed")
        return None

    qr_data = {
        "type": "agentia_rdv",
        "id": appointment_id,
        "name": person_name,
        "track": track_name,
        "date": appointment_date,
        "time": appointment_time,
        "expires": appointment_date,  # QR invalide après cette date
    }

    # Signature HMAC pour empêcher la falsification
    payload_str = json.dumps(qr_data, sort_keys=True, ensure_ascii=False)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]
    qr_data["sig"] = signature

    qr_json = json.dumps(qr_data, ensure_ascii=False)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(qr_json)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#1a365d", back_color="white")
    img_buffer = io.BytesIO()
    img.save(img_buffer, format="PNG")
    img_buffer.seek(0)

    return Image(img_buffer, width=5 * cm, height=5 * cm)


def verify_appointment_qr(
    qr_json_str: str,
    *,
    secret_key: str = "agentia-scolaire-default-key",
) -> Dict[str, Any]:
    """Vérifie un QR code de rendez-vous.

    Returns:
        Dict avec 'valid' (bool), 'expired' (bool), 'data' (dict), 'error' (str|None)
    """
    try:
        data = json.loads(qr_json_str)
    except (json.JSONDecodeError, TypeError):
        return {"valid": False, "expired": False, "data": {}, "error": "invalid_json"}

    if data.get("type") != "agentia_rdv":
        return {"valid": False, "expired": False, "data": data, "error": "invalid_type"}

    # Vérifier la signature
    received_sig = data.pop("sig", "")
    payload_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
    expected_sig = hmac.new(
        secret_key.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]

    if not hmac.compare_digest(received_sig, expected_sig):
        return {"valid": False, "expired": False, "data": data, "error": "invalid_signature"}

    # Vérifier l'expiration
    expires_str = data.get("expires", "")
    try:
        expires_date = datetime.strptime(expires_str, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        now = datetime.now(timezone.utc)
        expired = now > expires_date
    except (ValueError, TypeError):
        expired = False

    return {
        "valid": True,
        "expired": expired,
        "data": data,
        "error": "qr_expired" if expired else None,
    }
