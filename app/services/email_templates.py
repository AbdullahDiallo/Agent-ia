"""
Templates email metier pour Salma scolaire.
Les noms historiques sont conserves (rdv_confirmation, rdv_reminder_24h, otp_verification,
welcome_client) pour compatibilite ascendante.
"""


def _base_html(title: str, intro: str, body: str, footer: str = "Equipe Admissions") -> str:
    return f"""
<!DOCTYPE html>
<html lang=\"fr\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>{title}</title>
</head>
<body style=\"margin:0;padding:0;background:#f6f8fb;font-family:Arial,sans-serif;color:#0b1220;\">
  <table width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"padding:24px;\">
    <tr>
      <td align=\"center\">
        <table width=\"680\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e4e7ec;\">
          <tr>
            <td style=\"background:#0b1f5e;padding:28px 32px;color:#fff;\">
              <h1 style=\"margin:0;font-size:28px;\">{title}</h1>
              <p style=\"margin:12px 0 0 0;font-size:15px;opacity:0.95;\">{intro}</p>
            </td>
          </tr>
          <tr>
            <td style=\"padding:28px 32px;line-height:1.6;font-size:15px;\">{body}</td>
          </tr>
          <tr>
            <td style=\"padding:18px 32px;background:#f9fafb;border-top:1px solid #eceff3;font-size:13px;color:#667085;\">{footer}</td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def get_rdv_created_template() -> dict:
    return {
        "name": "rdv_created",
        "subject_template": "Demande de rendez-vous enregistree - {{ event.title }}",
        "html_template": _base_html(
            "Demande de rendez-vous enregistree",
            "Votre demande a bien ete prise en compte.",
            """
            <p>Bonjour <strong>{{ client_name }}</strong>,</p>
            <p>Nous confirmons la reception de votre demande de rendez-vous pour <strong>{{ event.title }}</strong>.</p>
            <p>Date proposee: <strong>{{ event.start_at | format_datetime }}</strong>.</p>
            <p>Nous reviendrons vers vous pour confirmation definitive.</p>
            """,
        ),
        "text_template": "Bonjour {{ client_name }}, votre demande de rendez-vous pour {{ event.title }} est enregistree ({{ event.start_at | format_datetime }}).",
    }


def get_rdv_confirmation_template() -> dict:
    return {
        "name": "rdv_confirmation",
        "subject_template": "Rendez-vous confirme - {{ event.title }}",
        "html_template": _base_html(
            "Rendez-vous confirme",
            "Votre rendez-vous est valide.",
            """
            <p>Bonjour <strong>{{ client_name }}</strong>,</p>
            <p>Votre rendez-vous pour <strong>{{ event.title }}</strong> est confirme.</p>
            <p>Date: <strong>{{ event.start_at | format_datetime }}</strong>.</p>
            <p>Merci de vous presenter 10 minutes a l'avance avec vos pieces necessaires.</p>
            """,
        ),
        "text_template": "Bonjour {{ client_name }}, votre rendez-vous {{ event.title }} est confirme le {{ event.start_at | format_datetime }}.",
    }


def get_rdv_reminder_24h_template() -> dict:
    return {
        "name": "rdv_reminder_24h",
        "subject_template": "Rappel J-1 - {{ event.title }}",
        "html_template": _base_html(
            "Rappel de rendez-vous (J-1)",
            "Votre rendez-vous est prevu demain.",
            """
            <p>Bonjour <strong>{{ client_name }}</strong>,</p>
            <p>Rappel: votre rendez-vous <strong>{{ event.title }}</strong> a lieu demain.</p>
            <p>Horaire: <strong>{{ event.start_at | format_datetime }}</strong>.</p>
            <p>Besoin de modifier? Contactez l'administration de l'etablissement.</p>
            """,
        ),
        "text_template": "Rappel J-1: RDV {{ event.title }} demain a {{ event.start_at | format_datetime }}.",
    }


def get_rdv_reminder_dayof_template() -> dict:
    return {
        "name": "rdv_reminder_j0",
        "subject_template": "Rappel aujourd'hui - {{ event.title }}",
        "html_template": _base_html(
            "Rappel de rendez-vous (J0)",
            "Votre rendez-vous a lieu aujourd'hui.",
            """
            <p>Bonjour <strong>{{ client_name }}</strong>,</p>
            <p>Votre rendez-vous <strong>{{ event.title }}</strong> est prevu aujourd'hui.</p>
            <p>Horaire: <strong>{{ event.start_at | format_datetime }}</strong>.</p>
            """,
        ),
        "text_template": "Rappel J0: RDV {{ event.title }} aujourd'hui a {{ event.start_at | format_datetime }}.",
    }


def get_rdv_followup_template() -> dict:
    return {
        "name": "rdv_followup",
        "subject_template": "Suivi de votre entretien - {{ event.title }}",
        "html_template": _base_html(
            "Suivi apres entretien",
            "Merci pour votre echange.",
            """
            <p>Bonjour <strong>{{ client_name }}</strong>,</p>
            <p>Merci pour votre entretien concernant <strong>{{ event.title }}</strong>.</p>
            <p>Si besoin, vous pouvez repondre a cet email pour obtenir la suite de la procedure.</p>
            """,
        ),
        "text_template": "Merci pour votre entretien {{ event.title }}. Repondez a cet email pour la suite.",
    }


def get_otp_verification_template() -> dict:
    return {
        "name": "otp_verification",
        "subject_template": "Votre code de verification - Aelixoria AI",
        "html_template": _base_html(
            "Code de verification",
            "Utilisez ce code pour finaliser votre connexion.",
            """
            <p>Bonjour,</p>
            <p>Votre code de verification est:</p>
            <p style=\"font-size:30px;font-weight:700;letter-spacing:6px;\">{{ otp_code }}</p>
            <p>Ce code expire rapidement. Ne le partagez jamais.</p>
            """,
            "Aelixoria AI - Securite de connexion",
        ),
        "text_template": "Votre code OTP Aelixoria AI: {{ otp_code }}. Expiration rapide.",
    }


def get_welcome_client_template() -> dict:
    """Compatibilite: conserve le nom historique 'welcome_client'."""
    return {
        "name": "welcome_client",
        "subject_template": "Bienvenue - {{ client_name }}",
        "html_template": _base_html(
            "Bienvenue",
            "Votre compte contact est actif.",
            """
            <p>Bonjour <strong>{{ client_name }}</strong>,</p>
            <p>Bienvenue sur la plateforme de communication de l'etablissement.</p>
            <p>Nous vous accompagnerons pour vos demandes d'informations, de rendez-vous et de suivi.</p>
            """,
        ),
        "text_template": "Bienvenue {{ client_name }}. Votre compte est actif sur la plateforme Aelixoria AI.",
    }


def get_admission_documents_template() -> dict:
    return {
        "name": "admission_documents",
        "subject_template": "Pieces requises pour votre dossier - {{ event.title }}",
        "html_template": _base_html(
            "Pieces requises",
            "Liste des documents a preparer.",
            """
            <p>Bonjour <strong>{{ client_name }}</strong>,</p>
            <p>Voici la liste des pieces requises pour votre dossier <strong>{{ event.title }}</strong>:</p>
            <p>{{ documents_list or 'Piece d\'identite, releves, photos, formulaire complete.' }}</p>
            """,
        ),
        "text_template": "Documents requis pour {{ event.title }}: {{ documents_list }}",
    }


def get_all_professional_templates() -> list[dict]:
    """Retourne le set standard de templates scolaire V1."""
    return [
        get_rdv_created_template(),
        get_rdv_confirmation_template(),
        get_rdv_reminder_24h_template(),
        get_rdv_reminder_dayof_template(),
        get_rdv_followup_template(),
        get_admission_documents_template(),
        get_otp_verification_template(),
        get_welcome_client_template(),
    ]
