from __future__ import annotations

from app.services.conversation_orchestrator import ConversationOrchestrator


def _fake_track_search(_db, arguments):
    query = str((arguments or {}).get("query") or "").lower()
    if "genie logiciel" in query:
        return {
            "success": True,
            "items": [
                {
                    "track_id": "t1",
                    "track_name": "Genie Logiciel",
                    "program_name": "Licence Pro",
                    "annual_fee": 1150000,
                    "registration_fee": 250000,
                    "monthly_fee": 100000,
                    "access_level": "Bac +2",
                    "delivery_mode": "onsite",
                    "certifications": "CCNA",
                }
            ],
        }
    return {"success": False, "error": "track_not_found"}


def _fake_track_search_with_duplicates(_db, arguments):
    query = str((arguments or {}).get("query") or "").lower()
    if "genie logiciel" in query:
        return {
            "success": True,
            "items": [
                {
                    "track_id": "gl-lp",
                    "track_name": "Genie Logiciel",
                    "program_name": "Licence Professionnelle",
                    "annual_fee": 1330000,
                    "registration_fee": 250000,
                    "monthly_fee": 100000,
                    "access_level": "Licence",
                    "delivery_mode": "elearning",
                    "certifications": "",
                },
                {
                    "track_id": "gl-l3",
                    "track_name": "Genie Logiciel",
                    "program_name": "Licence Professionnelle (L3)",
                    "annual_fee": 1450000,
                    "registration_fee": 250000,
                    "monthly_fee": 110000,
                    "access_level": "L3",
                    "delivery_mode": "onsite",
                    "certifications": "",
                },
                {
                    "track_id": "gl-master",
                    "track_name": "Genie Logiciel",
                    "program_name": "Master Professionnel",
                    "annual_fee": 1900000,
                    "registration_fee": 300000,
                    "monthly_fee": 160000,
                    "access_level": "Master",
                    "delivery_mode": "hybrid",
                    "certifications": "",
                },
            ],
        }
    if any(word in query for word in ["programme", "programmes", "program"]):
        return {
            "success": True,
            "items": [
                {
                    "track_id": "gl-lp",
                    "track_name": "Genie Logiciel",
                    "program_name": "Licence Professionnelle",
                    "annual_fee": 1330000,
                    "registration_fee": 250000,
                    "monthly_fee": 100000,
                    "access_level": "Licence",
                    "delivery_mode": "elearning",
                    "certifications": "",
                },
                {
                    "track_id": "gl-l3",
                    "track_name": "Genie Logiciel",
                    "program_name": "Licence Professionnelle (L3)",
                    "annual_fee": 1450000,
                    "registration_fee": 250000,
                    "monthly_fee": 110000,
                    "access_level": "L3",
                    "delivery_mode": "onsite",
                    "certifications": "",
                },
                {
                    "track_id": "iage-l1",
                    "track_name": "Informatique Appliquee a la Gestion des Entreprises",
                    "program_name": "Licence (L1, L2)",
                    "annual_fee": 890000,
                    "registration_fee": 250000,
                    "monthly_fee": 80000,
                    "access_level": "L1-L2",
                    "delivery_mode": "onsite",
                    "certifications": "",
                },
            ],
        }
    return {"success": False, "error": "track_not_found"}


def _fake_catalog_modeling_search(_db, arguments):
    query = str((arguments or {}).get("query") or "").lower()
    items = [
        {
            "track_id": "gl-l3",
            "track_name": "Genie Logiciel",
            "program_name": "Licence Professionnelle (L3)",
            "annual_fee": 1450000,
            "registration_fee": 250000,
            "monthly_fee": 110000,
            "access_level": "L3",
            "delivery_mode": "onsite",
            "certifications": "",
        },
        {
            "track_id": "cs-l3",
            "track_name": "Cyber Securite",
            "program_name": "Licence Professionnelle (L3)",
            "annual_fee": 1450000,
            "registration_fee": 250000,
            "monthly_fee": 110000,
            "access_level": "L3",
            "delivery_mode": "onsite",
            "certifications": "",
        },
        {
            "track_id": "cs-master",
            "track_name": "Cyber Securite",
            "program_name": "Master Professionnel",
            "annual_fee": 1900000,
            "registration_fee": 300000,
            "monthly_fee": 160000,
            "access_level": "Master",
            "delivery_mode": "hybrid",
            "certifications": "",
        },
        {
            "track_id": "ri-l12",
            "track_name": "Reseaux Informatiques",
            "program_name": "Licence (L1, L2)",
            "annual_fee": 890000,
            "registration_fee": 250000,
            "monthly_fee": 80000,
            "access_level": "L1-L2",
            "delivery_mode": "onsite",
            "certifications": "",
        },
        {
            "track_id": "md-l3",
            "track_name": "Marketing Digital",
            "program_name": "Licence Professionnelle (L3)",
            "annual_fee": 1450000,
            "registration_fee": 250000,
            "monthly_fee": 110000,
            "access_level": "L3",
            "delivery_mode": "onsite",
            "certifications": "",
        },
        {
            "track_id": "ds-b5",
            "track_name": "Data Science & Intelligence Artificielle",
            "program_name": "DITI - BAC+5",
            "annual_fee": 2200000,
            "registration_fee": 300000,
            "monthly_fee": 180000,
            "access_level": "BAC+5",
            "delivery_mode": "hybrid",
            "certifications": "",
        },
    ]
    if "cyber securite" in query:
        return {"success": True, "items": [item for item in items if item["track_name"].lower() == "cyber securite"]}
    if any(word in query for word in ["programme", "programmes", "program", "filiere", "filieres", "track", "tracks", "catalogue", "catalog"]):
        return {"success": True, "items": items}
    if "genie logiciel" in query:
        return {"success": True, "items": [item for item in items if item["track_name"].lower() == "genie logiciel"]}
    return {"success": False, "error": "track_not_found"}


def test_orchestrator_booking_flow_transitions_are_deterministic():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search)

    history: list[str] = []
    state = None

    t1 = orchestrator.process_message(message="Je suis interesse par Genie Logiciel", history_user_messages=history, state=state)
    assert t1.use_llm is False
    assert t1.state["active_flow"] == "track_selected"
    assert t1.response_strategy == "deterministic_track_details"
    history.append("Je suis interesse par Genie Logiciel")
    state = t1.state

    t2 = orchestrator.process_message(message="oui", history_user_messages=history, state=state)
    assert t2.use_llm is False
    assert t2.state["active_flow"] == "booking_collect_contact"
    assert t2.response_strategy == "deterministic_booking_collect_contact"
    history.append("oui")
    state = t2.state

    t3 = orchestrator.process_message(
        message="Abdoulaye Diallo diabdullah113@gmail.com +221776625059",
        history_user_messages=history,
        state=state,
    )
    assert t3.use_llm is False
    assert t3.state["active_flow"] == "booking_collect_datetime"
    assert t3.response_strategy == "deterministic_booking_collect_datetime"
    history.append("Abdoulaye Diallo diabdullah113@gmail.com +221776625059")
    state = t3.state

    t4 = orchestrator.process_message(message="28/02/2026 à 15h", history_user_messages=history, state=state)
    assert t4.use_llm is False
    assert t4.state["active_flow"] == "booking_confirm"
    assert t4.response_strategy == "deterministic_booking_confirm"
    assert t4.state["language_locked"] == "fr"
    history.append("28/02/2026 à 15h")
    state = t4.state

    t5 = orchestrator.process_message(message="oui confirme", history_user_messages=history, state=state)
    assert t5.use_llm is False
    assert t5.state["active_flow"] == "booking_submitted"
    assert t5.response_strategy == "deterministic_booking_submitted"


def test_orchestrator_catalog_program_request_uses_program_label_not_filiere_header():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search_with_duplicates)
    turn = orchestrator.process_message(
        message="Quels sont vos programmes disponibles ?",
        history_user_messages=[],
        state=None,
    )
    assert turn.use_llm is False
    assert turn.response_strategy == "deterministic_catalog"
    assert "programmes disponibles" in (turn.reply or "").lower()
    assert "Voici les filieres disponibles" not in (turn.reply or "")
    assert "Licence Professionnelle" in (turn.reply or "")
    assert "Licence Professionnelle (L3)" in (turn.reply or "")
    assert "Licence (L1, L2)" in (turn.reply or "")
    assert "Genie Logiciel" not in (turn.reply or "")
    slots = turn.state.get("slots_json") or {}
    assert slots.get("last_catalog_subject") == "program"
    assert isinstance(slots.get("last_catalog_options"), list)
    assert any(item.get("program_name") == "Licence Professionnelle" for item in slots.get("last_catalog_options") or [])


def test_orchestrator_asks_disambiguation_when_track_name_has_multiple_programs():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search_with_duplicates)
    t1 = orchestrator.process_message(message="Genie Logiciel", history_user_messages=[], state=None)
    assert t1.use_llm is False
    assert t1.response_strategy == "deterministic_track_disambiguation"
    assert "plusieurs options" in (t1.reply or "").lower()
    slots = t1.state.get("slots_json") or {}
    assert isinstance(slots.get("pending_track_options"), list)
    assert "Genie Logiciel - Licence Professionnelle | Niveau L3" in (t1.reply or "")

    t2 = orchestrator.process_message(
        message="Licence Professionnelle (L3)",
        history_user_messages=["Genie Logiciel"],
        state=t1.state,
    )
    assert t2.use_llm is False
    assert t2.response_strategy == "deterministic_track_details"
    assert "Genie Logiciel - Licence Professionnelle | Niveau L3" in (t2.reply or "")


def test_orchestrator_handles_non_after_track_details_without_generic_fallback():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search)
    t1 = orchestrator.process_message(message="Genie Logiciel", history_user_messages=[], state=None)
    assert t1.response_strategy == "deterministic_track_details"
    t2 = orchestrator.process_message(message="non pas du tout", history_user_messages=["Genie Logiciel"], state=t1.state)
    assert t2.use_llm is False
    assert t2.response_strategy == "deterministic_track_decline_booking"
    assert "pas de rendez-vous" in (t2.reply or "").lower()


def test_orchestrator_handles_gratitude_after_booking_submitted_without_reconfirming():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search)
    state = {
        "version": 1,
        "language_locked": "fr",
        "active_flow": "booking_submitted",
        "response_strategy": "deterministic_booking_submitted_persisted",
        "slots_json": {
            "track_name": "Genie Logiciel",
            "program_name": "Licence Pro",
            "appointment_id": "abcd1234-0000-0000-0000-000000000000",
            "email": "a@example.com",
            "notification_channel": "email",
            "notification_sent": True,
        },
    }
    turn = orchestrator.process_message(message="ok merci", history_user_messages=["oui"], state=state)
    assert turn.use_llm is False
    assert turn.response_strategy == "deterministic_gratitude_after_booking"
    assert turn.state["active_flow"] == "browsing_catalog"
    assert "email de confirmation" not in (turn.reply or "").lower()


def test_orchestrator_casual_closure_after_persisted_booking_does_not_fill_name_or_reenter_booking():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search)
    state = {
        "version": 1,
        "language_locked": "fr",
        "active_flow": "browsing_catalog",
        "response_strategy": "deterministic_booking_submitted_persisted",
        "appointment_locked": True,
        "slots_json": {
            "track_name": "Genie Logiciel",
            "program_name": "Licence Pro",
            "appointment_id": "abcd1234-0000-0000-0000-000000000000",
            "appointment_status": "pending",
            "full_name": "Diallo Abdoulaye",
            "email": "a@example.com",
            "appointment_date": "28/02/2099",
            "appointment_time": "15h",
        },
    }

    turn = orchestrator.process_message(
        message="ok sebon alors merci",
        history_user_messages=["oui", "Diallo Abdoulaye", "28/02/2099 à 15h"],
        state=state,
    )

    assert turn.use_llm is False
    assert turn.response_strategy == "deterministic_gratitude_after_booking"
    assert turn.state["active_flow"] == "browsing_catalog"
    assert turn.state["appointment_locked"] is True
    assert (turn.state.get("slots_json") or {}).get("full_name") == "Diallo Abdoulaye"
    assert "recapitulatif" not in (turn.reply or "").lower()


def test_orchestrator_routes_program_recommendation_question_to_llm_instead_of_relisting_catalog():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search_with_duplicates)

    first = orchestrator.process_message(
        message="Quels sont vos programmes disponibles ?",
        history_user_messages=[],
        state=None,
    )
    assert first.response_strategy == "deterministic_catalog"

    second = orchestrator.process_message(
        message="ok sans problemes, mais dis moi de tout les programmes que tu m'as propose lequel est mieux ?",
        history_user_messages=["Quels sont vos programmes disponibles ?"],
        state=first.state,
    )
    assert second.use_llm is True
    assert second.response_strategy == "llm_pending"
    assert second.state["active_flow"] == "browsing_catalog"
    # Contextual follow-up detection now catches "lequel" before recommendation check
    assert second.state["pending_open_intent"] in ("recommendation_request", "contextual_followup")
    assert second.reply is None


def test_orchestrator_recommendation_after_recorded_booking_keeps_fresh_browsing_state():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search_with_duplicates)
    state = {
        "version": 1,
        "language_locked": "fr",
        "active_flow": "browsing_catalog",
        "response_strategy": "deterministic_booking_submitted_persisted",
        "slots_json": {
            "track_name": "Genie Logiciel",
            "program_name": "Licence Professionnelle (L3)",
            "appointment_id": "abcd1234-0000-0000-0000-000000000000",
            "appointment_status": "pending",
        },
    }

    turn = orchestrator.process_message(
        message="de toutes ces filieres, laquelle est mieux ?",
        history_user_messages=["Je veux Genie Logiciel"],
        state=state,
    )

    assert turn.use_llm is True
    assert turn.response_strategy == "llm_pending"
    assert turn.state["active_flow"] == "browsing_catalog"


def test_orchestrator_catalog_track_request_returns_only_track_entries():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_catalog_modeling_search)
    turn = orchestrator.process_message(
        message="Quelles sont vos filieres disponibles ?",
        history_user_messages=[],
        state=None,
    )

    assert turn.use_llm is False
    assert turn.response_strategy == "deterministic_catalog"
    assert "filieres disponibles" in (turn.reply or "").lower()
    assert "Genie Logiciel" in (turn.reply or "")
    assert "Cyber Securite" in (turn.reply or "")
    assert "Reseaux Informatiques" in (turn.reply or "")
    assert "Marketing Digital" in (turn.reply or "")
    assert "Licence Professionnelle (L3)" not in (turn.reply or "")
    assert "Master Professionnel" not in (turn.reply or "")
    assert (turn.reply or "").count("Cyber Securite") == 1
    slots = turn.state.get("slots_json") or {}
    assert slots.get("last_catalog_subject") == "track"
    assert isinstance(slots.get("last_catalog_options"), list)
    assert any(item.get("track_name") == "Cyber Securite" for item in slots.get("last_catalog_options") or [])


def test_orchestrator_catalog_grouped_request_returns_programmes_with_their_filieres():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_catalog_modeling_search)
    turn = orchestrator.process_message(
        message="Quels sont vos programmes et filieres disponibles ?",
        history_user_messages=[],
        state=None,
    )

    assert turn.use_llm is False
    assert turn.response_strategy == "deterministic_catalog"
    assert "programmes avec leurs filieres" in (turn.reply or "").lower()
    assert "1. Licence Professionnelle (L3) : Genie Logiciel, Cyber Securite, Marketing Digital" in (turn.reply or "")
    assert "2. Master Professionnel : Cyber Securite" in (turn.reply or "")
    assert "3. Licence (L1, L2) : Reseaux Informatiques" in (turn.reply or "")
    assert "4. DITI - BAC+5 : Data Science & Intelligence Artificielle" in (turn.reply or "")


def test_orchestrator_disambiguates_cyber_securite_across_multiple_programmes():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_catalog_modeling_search)
    first = orchestrator.process_message(
        message="Je veux des details sur Cyber Securite",
        history_user_messages=[],
        state=None,
    )

    assert first.use_llm is False
    assert first.response_strategy == "deterministic_track_disambiguation"
    assert "Cyber Securite - Licence Professionnelle | Niveau L3" in (first.reply or "")
    assert "Cyber Securite - Master Professionnel | Niveau Master" in (first.reply or "")

    second = orchestrator.process_message(
        message="Master Professionnel",
        history_user_messages=["Je veux des details sur Cyber Securite"],
        state=first.state,
    )

    assert second.use_llm is False
    assert second.response_strategy == "deterministic_track_details"
    assert "Cyber Securite - Master Professionnel | Niveau Master" in (second.reply or "")


def test_orchestrator_exits_booking_collect_datetime_when_user_switches_to_program_comparison():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search_with_duplicates)
    state = {
        "version": 1,
        "language_locked": "fr",
        "active_flow": "booking_collect_datetime",
        "response_strategy": "deterministic_booking_no_agent_available",
        "slots_json": {
            "track_name": "Genie Logiciel",
            "program_name": "Licence Professionnelle (L3)",
            "full_name": "Diallo Abdoulaye",
            "email": "diabdullah112@gmail.com",
            "phone": "+221776625059",
            "appointment_date": "28 fevrier 2026",
            "appointment_time": "15h",
        },
    }

    turn = orchestrator.process_message(
        message="ok sans problemes, mais dis moi de tout les programmes que tu m'a propose lequel est mieux ?",
        history_user_messages=["oui", "Aucun agent disponible ?"],
        state=state,
    )
    assert turn.use_llm is True
    assert turn.response_strategy == "llm_pending"
    assert turn.state["active_flow"] == "browsing_catalog"
    assert "recapitulatif" not in (turn.reply or "").lower()


def test_orchestrator_progressive_fallback_unknown_language_then_success_resets_failure_count():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search)

    t1 = orchestrator.process_message(
        message="%%% ???",
        history_user_messages=[],
        state=None,
    )
    assert t1.response_strategy == "fallback_clarify"
    assert t1.state["failure_count"] == 1
    assert t1.state["fallback_stage"] == "clarify"
    assert t1.state["handoff_allowed"] is False

    t2 = orchestrator.process_message(
        message=".....",
        history_user_messages=["%%% ???"],
        state=t1.state,
    )
    assert t2.response_strategy == "fallback_guided"
    assert t2.state["failure_count"] == 2
    assert t2.state["fallback_stage"] == "guided"
    assert t2.state["handoff_allowed"] is False

    t3 = orchestrator.process_message(
        message="Je veux les détails de Genie Logiciel",
        history_user_messages=["%%% ???", "....."],
        state=t2.state,
    )
    assert t3.response_strategy == "deterministic_track_details"
    assert t3.state["failure_count"] == 0
    assert t3.state["clarification_success"] is True
    assert t3.state["fallback_stage"] is None
    assert t3.state["handoff_allowed"] is False


def test_orchestrator_handoff_on_third_understanding_failure():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search)

    t1 = orchestrator.process_message(message="???", history_user_messages=[], state=None)
    t2 = orchestrator.process_message(message="###", history_user_messages=["???"], state=t1.state)
    t3 = orchestrator.process_message(message="...", history_user_messages=["???", "###"], state=t2.state)

    assert t1.response_strategy == "fallback_clarify"
    assert t2.response_strategy == "fallback_guided"
    assert t3.response_strategy == "fallback_handoff"
    assert t3.state["failure_count"] == 3
    assert t3.state["fallback_stage"] == "handoff"
    assert t3.state["handoff_allowed"] is True
    assert t3.state["handoff_trigger_reason"] == "failure_count_threshold"


def test_orchestrator_short_text_inputs_never_trigger_handoff_and_keep_fr_language():
    orchestrator = ConversationOrchestrator(
        db=None,
        track_search_fn=lambda *_args, **_kwargs: {"success": False, "error": "track_not_found"},
    )

    state = None
    history: list[str] = []
    for short_text in ("ok", "oui", "d'accord", "frais"):
        turn = orchestrator.process_message(
            message=short_text,
            history_user_messages=history,
            state=state,
        )
        assert turn.lang == "fr"
        assert turn.state.get("handoff_allowed") is False
        assert turn.response_strategy != "fallback_handoff"
        assert turn.state.get("short_text_flag") is True
        history.append(short_text)
        state = turn.state


def test_orchestrator_flow_escape_commands_apply_controlled_partial_reset():
    orchestrator = ConversationOrchestrator(db=None, track_search_fn=_fake_track_search)

    base_state = {
        "version": 1,
        "language_locked": "fr",
        "active_flow": "booking_collect_datetime",
        "response_strategy": "deterministic_booking_collect_datetime",
        "failure_count": 2,
        "slots_json": {
            "thread_key": "chat:session-123",
            "track_name": "Genie Logiciel",
            "program_name": "Licence Pro",
            "email": "abdoulaye@example.com",
        },
    }

    mapping = {
        "annuler": "flow_escape_cancel",
        "nouvelle question": "flow_escape_new_question",
        "menu": "flow_escape_menu",
    }
    for command, expected_reason in mapping.items():
        turn = orchestrator.process_message(
            message=command,
            history_user_messages=["oui", "28/02/2026 a 15h"],
            state=base_state,
        )
        assert turn.response_strategy == "deterministic_flow_escape"
        assert turn.state["active_flow"] == "browsing_catalog"
        assert turn.state["failure_count"] == 0
        assert turn.state["handoff_allowed"] is False
        assert turn.state["reset_reason"] == expected_reason
        assert (turn.state.get("slots_json") or {}) == {"thread_key": "chat:session-123"}
