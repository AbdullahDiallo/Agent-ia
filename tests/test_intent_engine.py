from app.services.intent_engine import detect_intent, escalation_message


def test_detect_intent_infos_filiere():
    decision = detect_intent(
        "Bonjour, je cherche une filière en informatique.",
        lang="fr",
    )
    assert decision.intent == "infos_filiere"
    assert decision.action == "respond"
    assert decision.score > 0


def test_detect_intent_tarifs():
    decision = detect_intent(
        "Can you share tuition fees and payment options?",
        lang="en",
    )
    assert decision.intent == "tarifs"
    assert decision.action == "respond"
    assert decision.score > 0


def test_detect_intent_admission():
    decision = detect_intent(
        "Quelles sont les conditions d'admission pour candidater ?",
        lang="fr",
    )
    assert decision.intent == "admission"
    assert decision.action == "respond"


def test_detect_intent_documents_requis():
    decision = detect_intent(
        "Quels documents requis dois-je fournir dans mon dossier ?",
        lang="fr",
    )
    assert decision.intent == "documents_requis"
    assert decision.action == "respond"


def test_detect_intent_calendrier():
    decision = detect_intent(
        "What are the key calendar dates and deadlines?",
        lang="en",
    )
    assert decision.intent == "calendrier"
    assert decision.action == "respond"


def test_detect_intent_prise_rdv():
    decision = detect_intent(
        "Je veux prendre un rendez-vous demain matin",
        lang="fr",
    )
    assert decision.intent == "prise_rdv"
    assert decision.action == "respond"


def test_detect_intent_suivi():
    decision = detect_intent(
        "Je veux le suivi de mon dossier d'inscription",
        lang="fr",
    )
    assert decision.intent == "suivi"
    assert decision.action == "respond"


def test_detect_intent_escalade_humaine():
    decision = detect_intent(
        "Je veux parler à un responsable humain immédiatement",
        lang="fr",
    )
    assert decision.intent == "escalade_humaine"
    assert decision.action == "escalate_human"


def test_detect_intent_sensitive_forces_escalation():
    decision = detect_intent(
        "Je veux porter plainte, c'est urgent",
        lang="fr",
    )
    assert decision.action == "escalate_human"
    assert decision.intent in {
        "escalade_humaine",
        "infos_filiere",
        "tarifs",
        "admission",
        "documents_requis",
        "calendrier",
        "prise_rdv",
        "suivi",
    }


def test_detect_intent_unknown_is_clarification():
    decision = detect_intent(
        "blabla",
        lang="fr",
    )
    # Low-confidence / no-match defaults to "respond" to let LLM handle it
    assert decision.action == "respond"


def test_escalation_message_multilang():
    assert "conseiller admissions" in escalation_message("fr").lower()
    assert "admissions advisor" in escalation_message("en").lower()
