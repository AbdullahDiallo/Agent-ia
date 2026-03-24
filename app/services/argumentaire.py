from __future__ import annotations

from typing import Any, Dict, List, Optional, Set


# Banque d'arguments orientee etablissement scolaire (FR / EN / WO).
_ARGUMENTS: Dict[str, Dict[str, List[str]]] = {
    "fr": {
        "general": [
            "Nous vous accompagnons de la demande d'information jusqu'a la finalisation du dossier.",
            "Nos reponses sont basees sur les informations officielles de l'etablissement.",
            "Je peux vous orienter rapidement vers la filiere adaptee a votre profil.",
            "Nous pouvons planifier un rendez-vous admission pour accelerer votre inscription.",
        ],
        "programs": [
            "Je peux vous presenter les filieres par niveau: Licence, Licence Professionnelle, Master ou Ingenieur.",
            "Chaque filiere inclut ses modalites, ses certifications et ses debouches.",
            "Je peux vous proposer des alternatives proches selon votre objectif et votre budget.",
        ],
        "admission": [
            "Je peux verifier avec vous les conditions d'acces (BAC, BAC+2, BTS, Licence).",
            "Je peux vous envoyer la liste exacte des pieces a fournir pour gagner du temps.",
            "Une prequalification rapide permet de savoir vers quel parcours vous orienter.",
        ],
        "tuition": [
            "Je peux vous detailler le cout annuel, le droit d'inscription et la mensualite par filiere.",
            "Nous pouvons comparer plusieurs options pour trouver le meilleur compromis cout/parcours.",
            "Je peux vous recapitulatif des frais par email ou WhatsApp.",
        ],
        "appointments": [
            "Je peux reserver un rendez-vous pour l'inscription, le depot de dossier ou l'entretien.",
            "Apres confirmation, vous recevez automatiquement les messages de rappel.",
            "Si besoin, je peux reprogrammer le rendez-vous sur un autre creneau.",
        ],
        "documents": [
            "Je peux vous envoyer les documents requis et les delais associes.",
            "Avant validation, nous verifions que le dossier est complet pour eviter les retards.",
            "Je peux vous rappeler les pieces manquantes avant le rendez-vous.",
        ],
        "objection:trop_cher": [
            "Je comprends. Je peux comparer des filieres proches avec une mensualite plus adaptee.",
            "On peut regarder les modalites presentiel/e-learning selon votre contrainte budgetaire.",
            "Je peux vous aider a prioriser les options selon le rapport cout / objectif professionnel.",
        ],
        "objection:j_hesite": [
            "C'est normal d'hesiter. Je peux vous faire un comparatif simple de 2 ou 3 filieres.",
            "Nous pouvons organiser un rendez-vous court pour repondre aux points qui bloquent.",
            "Je peux vous envoyer un recapitulatif clair pour faciliter la decision.",
        ],
        "objection:delai": [
            "Je peux vous indiquer les delais exacts et les prochaines dates importantes.",
            "Si vous etes presse, nous pouvons preparer le dossier en priorisant les pieces critiques.",
        ],
    },
    "en": {
        "general": [
            "We support you from the first inquiry to final enrollment.",
            "All answers are based on official school information.",
            "I can quickly guide you to the most suitable program.",
            "We can schedule an admissions appointment right away.",
        ],
        "programs": [
            "I can present programs by level: Bachelor, Professional Bachelor, Master, or Engineering.",
            "Each track includes delivery mode, certifications, and outcomes.",
            "I can suggest close alternatives based on your goals and budget.",
        ],
        "admission": [
            "I can check admission requirements with you (high school, BTS, bachelor level).",
            "I can send the exact list of required documents.",
            "A quick pre-screening helps identify the right path.",
        ],
        "tuition": [
            "I can break down annual tuition, enrollment fee, and monthly payment per track.",
            "We can compare options to find the best budget/goal balance.",
            "I can send a fee summary by email or WhatsApp.",
        ],
        "appointments": [
            "I can book an appointment for enrollment, interview, or file submission.",
            "After confirmation, reminders are sent automatically.",
            "If needed, I can reschedule your appointment.",
        ],
        "documents": [
            "I can share required documents and related deadlines.",
            "Before validation, we check that your file is complete to avoid delays.",
            "I can remind you about missing documents before your appointment.",
        ],
        "objection:trop_cher": [
            "I understand. I can suggest nearby tracks with a lower monthly fee.",
            "We can compare onsite and e-learning options based on your budget.",
            "I can help prioritize options with the best cost/outcome ratio.",
        ],
        "objection:j_hesite": [
            "That is normal. I can provide a simple comparison of 2-3 tracks.",
            "We can schedule a short call to address your key concerns.",
            "I can send a concise summary to support your decision.",
        ],
        "objection:delai": [
            "I can provide exact timelines and key upcoming dates.",
            "If you are in a hurry, we can prioritize critical documents first.",
        ],
    },
    "wo": {
        "general": [
            "Danu la ànd ci yoon wi: xibaar ba, bindu, ba ci jeexital dossier bi.",
            "Li may tontu, xibaaru ecole bi la aju ci.",
            "Maa ngi mën la jublu ci filiere bu la gën a jëmal.",
            "Mën nanu def rendez-vous admission bu gaaw.",
        ],
        "programs": [
            "Maa ngi mën la won filiere yi ci niveau yu bari: Licence, Master, Ingenieur.",
            "Filiere bu nekk am na modalite, certification ak debouches.",
            "Mën naa la jox ay alternatif yu jigeen ci sa objectif ak sa budget.",
        ],
        "admission": [
            "Mën nanu seet ndax sa niveau mën naa dugg ci filiere bi.",
            "Maa ngi mën la yónnee li ñuy laaj ci dossier bi.",
            "Prequalification bu gaaw dafay jappale ci orientation.",
        ],
        "tuition": [
            "Mën naa la leeral cout annuel, droit d'inscription ak mensualite.",
            "Mën nanu méngale ay options ngir am bu gën ci sa budget.",
            "Maa ngi mën la yónnee recap frais yi ci WhatsApp walla email.",
        ],
        "appointments": [
            "Mën nanu jël rendez-vous inscription walla entretien.",
            "Bu confirmé, rappels yi di dem automatiquement.",
            "Su ko soxlaee, mën nanu soppi creneau bi.",
        ],
        "documents": [
            "Maa ngi mën la yónnee dokimaa yi ñuy laaj ak delai yi.",
            "Balaa validation, danuy seet ndax dossier bi mat na ngir bañ retard.",
            "Mën naa la fàttali dokimaa yi manquants laata rendez-vous bi.",
        ],
        "objection:trop_cher": [
            "Ma dégg. Mën nanu seet filiere yu jigeen ak mensualite bu gën yomb.",
            "Mën nanu méngale presentiel ak e-learning ci wàllu budget.",
        ],
        "objection:j_hesite": [
            "Dafa normal. Mën naa la def comparaison bu leer ci 2-3 filiere.",
            "Mën nanu def waxtaan bu gàtt ngir wóor li lay sonal.",
        ],
        "objection:delai": [
            "Mën naa la wax delai yi ak bés yu am solo yi.",
            "Bu yàggul, mën nanu jàppale ci dokimaa yu gën a am solo jëkk.",
        ],
    },
}


def _normalize_lang(lang: str) -> str:
    lang = (lang or "").strip().lower()
    if lang in {"fr", "fr-fr", "fr_fr", "french"}:
        return "fr"
    if lang in {"en", "en-us", "en_us", "english"}:
        return "en"
    if lang in {"wo", "wolof"}:
        return "wo"
    return "fr"


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for s in items:
        s2 = s.strip()
        if not s2 or s2 in seen:
            continue
        seen.add(s2)
        out.append(s2)
    return out


def get_arguments(context: Optional[Dict[str, Any]] = None) -> List[str]:
    """Retourne une liste d'arguments metier a injecter dans le prompt IA scolaire."""
    context = context or {}
    lang = _normalize_lang(str(context.get("language") or "fr"))
    bank = _ARGUMENTS.get(lang) or _ARGUMENTS["fr"]

    intention = str(context.get("intention") or "").lower()
    objection = str(context.get("objection") or "").strip().lower()
    urgency = str(context.get("urgency") or "").strip().lower()
    max_args = int(context.get("max_args") or 5)

    args: List[str] = []

    if any(k in intention for k in ["filiere", "formation", "programme", "program", "track"]):
        args.extend(bank.get("programs", []))
    if any(k in intention for k in ["admission", "inscription", "enroll", "apply", "dossier"]):
        args.extend(bank.get("admission", []))
    if any(k in intention for k in ["frais", "cout", "tuition", "mensualite", "price", "fees"]):
        args.extend(bank.get("tuition", []))
    if any(k in intention for k in ["rdv", "rendez", "appointment", "entretien", "interview"]):
        args.extend(bank.get("appointments", []))

    if objection:
        args.extend(bank.get(f"objection:{objection}", []))

    if urgency in {"high", "urgent", "vite", "rapide"}:
        args.extend(bank.get("appointments", []))

    args.extend(bank.get("documents", []))
    args.extend(bank.get("general", []))
    args = _dedupe_keep_order(args)

    if max_args < 3:
        max_args = 3
    if max_args > 7:
        max_args = 7
    return args[:max_args]

