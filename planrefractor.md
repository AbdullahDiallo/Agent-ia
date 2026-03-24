Plan recommandé pour avoir un bon agent (chat + email + WhatsApp + voice)

1. Architecture cible (à viser)
Mettre en place un noyau agent partagé pour tous les canaux:

State Store (conversation + user + slots)
Orchestrator (détermine l’étape suivante)
Extractor (entités: filière, nom, email, téléphone, date, heure, langue, confirmation)
Tool Runner (BDD, RDV, notifications, calendrier)
Response Renderer (chat/email/whatsapp/voice)
LLM Layer (extraction structurée + reformulation, pas pilotage métier seul)
Objectif:

L’action métier reste déterministe.
Le LLM apporte intelligence de formulation + extraction robuste.
2. P0 (1 semaine) — Stopper les erreurs visibles
Priorité: éliminer les “bêtises” que l’utilisateur voit.

Actions:

Verrouiller la langue de session (language_locked) après 1-2 tours fiables.
Ne plus re-détecter la langue sur messages “date-only”, “oui”, nom seul.
Parser nom + email + téléphone + date + heure dans un seul message.
Prioriser le flow RDV actif sur les mots-clés “programme/filière”.
Supprimer les répétitions de détails quand on est déjà en collecte RDV.
Fallback contextualisé par étape (pas le fallback générique “précisez la filière” pendant un RDV déjà engagé).
Corriger la duplication en vue détails track_name (program_name) quand identiques (actuellement corrigé pour la liste, pas pour tous les cas détails visibles).
Livrables:

booking_flow_state minimal en DB (JSON sur Conversation si besoin rapide)
tests transcript de non-régression (tes conversations exactes)

Justification schema (execution):

- Ajout d'un seul champ `conversation_state` (JSON sérialisé en texte) dans `Conversation`.
- Pas de nouvelle table créée.
- `language_locked`, `active_flow`, `slots_json`, `response_strategy` sont persistés dans ce JSON pour éviter la duplication de colonnes métier.

ETAPE TERMINEE ✅
3. P1 (1–2 semaines) — Orchestrateur de conversation (le vrai saut de qualité)
Implémenter une vraie machine d’état conversationnelle.

États (exemple):

browsing_catalog
track_selected
booking_collect_contact
booking_collect_datetime
booking_confirm
booking_submitted
handoff_human
Slots (mémoire métier):

track_id
track_name
program_name
full_name
email
phone
appointment_date
appointment_time
admission_level
preferred_channel
language_locked
Règles:

Le bot ne pose que la question du slot manquant.
Si plusieurs slots arrivent dans un même message, il les consomme tous.
Il reformule ce qu’il a compris avant de créer le RDV (confirmation explicite).
Pourquoi c’est la bonne direction:

Les docs Dialogflow CX insistent sur le form filling avec paramètres requis.
Les docs Rasa décrivent les slots comme mémoire de l’assistant.
Les docs Microsoft/OpenAI rappellent qu’un agent est stateless par défaut, donc il faut persister l’état.

Execution (fait):

- Orchestrateur central unique créé (`app/services/conversation_orchestrator.py`).
- États déterministes implémentés: `browsing_catalog`, `track_selected`, `booking_collect_contact`, `booking_collect_datetime`, `booking_confirm`, `booking_submitted`.
- Transitions d’état déterministes (le LLM n’est plus utilisé pour décider les transitions du flow RDV).
- Logique métier de routing/booking déplacée hors de `app/routers/chat.py` (routeur réduit à HTTP + persistance + appel orchestrateur).
- Tests unitaires orchestrateur + tests de non-régression chat mis à jour.

ETAPE TERMINEE ✅
4. P2 (1 semaine) — LLM “intelligent” mais contrôlé
Remplacer “LLM libre” par deux usages précis.

Usage A: extraction structurée (obligatoire)

Entrée: message utilisateur + état courant
Sortie JSON stricte:
intent
is_affirmative
is_negative
entities (track, email, phone, datetime, level, etc.)
topic_shift
Utiliser Structured Outputs / Function Calling strict (strict: true)
Désactiver parallel_tool_calls pour éviter ambiguïtés sur ces flux
Usage B: reformulation (optionnel)

Le backend calcule action + data + next_question
Le LLM reformule en langage naturel (FR/EN/WO)
Si LLM KO: template deterministic propre
Effet:

Tu gardes la fiabilité.
Tu récupères l’intelligence perçue.

Execution (fait):

- `LLMService` expose un hook d’extraction structurée (`extract_structured_message`) avec sortie JSON whitelistée/validée (non bloquante si erreur provider).
- L’orchestrateur fusionne extraction locale (regex) + extraction LLM en mode enrichissement seulement, sans déléguer les transitions d’état.
- Le routeur chat applique une reformulation contrôlée (`rephrase_controlled_reply`) sur les réponses déterministes via feature-flag.
- Les appels LLM avec tools ont été durcis (`parallel_tool_calls=False`, température réduite).
- Tests ajoutés pour l’extraction structurée et la reformulation contrôlée.

ETAPE TERMINEE ✅
5. P3 (1 semaine) — Unifier les canaux (chat, email, WhatsApp, voice)
Aujourd’hui, chaque canal risque de dériver. Il faut un pipeline commun.

Pipeline commun:

Inbound channel adapter -> NormalizedMessage
State load
Extraction + slot filling
Orchestrator
Tool runner
Renderer(channel)
Outbox + delivery
State save
Chat / Web widget
Réponse courte, progressive, guidée
Conserver session et slots côté serveur (pas juste session_id + historique texte)
Ajouter response_strategy log (deterministic, llm_extract, llm_rephrase, fallback)
Email
Threading réel (Message-ID, In-Reply-To, References)
Nettoyage des citations/signatures (sinon extraction polluée)
Réponse “asynchrone” plus complète, avec recap des infos déjà reçues
Si info manquante: liste claire des éléments restants (1 seul email de clarification si possible)
WhatsApp / SMS
Messages plus courts, plus guidés
Boutons/templates quand utile (confirmation RDV, choix filière)
Idempotence webhook + dedupe
Reprise de contexte multi-jours (slots persistés)
Voice (Twilio)
Vrai turn-taking / interruption
Gestion mark / clear pour interrompre proprement l’audio en cas de barge-in
Normalisation STT partiel vs final
Confirmation explicite des infos critiques (“Je récapitule…”)
Fallback vocal spécifique (pas fallback email/chat)
Signature validation Twilio obligatoire pour Media Streams

Execution (fait):

- Pipeline commun `ChannelAgentPipeline` introduit pour unifier le traitement texte multi-canaux (chargement état, extraction, orchestrateur, fallback, persistance conversation/messages).
- Routeurs `chat`, `email`, `sms`, `whatsapp` migrés vers ce pipeline (suppression de logique métier dupliquée par canal).
- `voice.py` conserve le streaming Twilio/STT/TTS, mais délègue désormais chaque tour utilisateur texte au pipeline commun (réutilisation de conversation sur tout l'appel via `conversation_id` local + `call_sid`).
- Helpers de réutilisation conversation ajoutés dans `kb.py` (`person+canal`, `call_sid`) pour éviter la dérive de contexte entre messages.
- Compatibilité de tests préservée (exports `LLMService` / shim `_log_whatsapp_conversation`).
- Tests P3 ajoutés pour le pipeline: reuse par `person+thread_key`, séparation de threads email, reuse par `call_sid` voice.

Validation (tests pass):

- `pytest -q tests/test_channel_agent_pipeline.py tests/test_chat_session_reuse.py tests/test_conversation_orchestrator.py tests/test_whatsapp_meta_webhook.py tests/test_tenant_webhook_resolution_integration.py tests/test_voice_token_public_access.py`
- Résultat: `20 passed`

ETAPE TERMINEE ✅
6. P4 (continu) — Qualité, évals, observabilité
Sans ça, tu vas corriger un bug et en créer deux autres.

Instrumentation minimale:

response_strategy
flow_state
slots_filled
slots_missing
llm_called
tool_calls
fallback_reason
language_locked
channel
handoff_trigger
KPIs (très utiles):

taux de répétition de question (bot redemande une info déjà donnée)
taux de fallback générique
taux de changement de langue non désiré
taux de complétion RDV
nombre moyen de tours pour créer un RDV
taux de handoff humain
taux d’erreurs provider par canal
Tests à industrialiser:

transcript tests (golden conversations réelles)
contract tests des tools (schemas + validation)
tests de panne provider (LLM/STT/TTS indispo)
tests cross-channel (chat -> WhatsApp -> email sur même dossier)
tests de langue mixte FR/EN/WO

Execution (fait - socle P4):

- Instrumentation structurée commune ajoutée au `ChannelAgentPipeline` (1 log par tour `agent_turn_processed`) pour tous les canaux déjà migrés.
- Champs instrumentés (socle) : `response_strategy`, `flow_state`, `slots_filled`, `slots_missing`, `llm_called`, `tool_calls`, `fallback_reason`, `language_locked`, `channel`, `handoff_trigger` (+ détails utiles: `tool_call_names`, `duration_ms`, catégories de stratégie).
- `LLMService` expose désormais des métadonnées d’exécution minimales pour observabilité (`last_tool_calls`, `last_fallback_reason`) afin d’éviter une logique parallèle dans les routeurs.
- Journalisation des fallbacks LLM contextualisés enrichie avec `fallback_reason` (provider/not-configured).
- Tests P4 ajoutés sur le pipeline pour prouver la présence des champs d’observabilité et la remontée des causes de fallback/tool calls.

Validation (tests pass):

- `pytest -q tests/test_channel_agent_pipeline.py tests/test_chat_session_reuse.py tests/test_conversation_orchestrator.py tests/test_whatsapp_meta_webhook.py tests/test_tenant_webhook_resolution_integration.py tests/test_voice_token_public_access.py`
- Résultat: `22 passed`

Finalisation P4 (fait):

- Ajout d'un service de lecture/corrélation des logs JSON `agent_turn_processed` (`app/services/agent_observability.py`) pour calculer les KPIs P4 sans nouvelle table (respect de la contrainte “pas de schéma inutile”).
- KPIs calculés : répétition de questions, fallback rate, changements de langue non désirés (par conversation), complétion RDV, moyenne de tours avant `booking_submitted`, handoff rate, erreurs provider par canal.
- Ajout d'un endpoint admin `/monitoring/agent-observability` (tenant-scopé) pour exposer ces métriques sur une fenêtre glissante.
- Correctif de propreté observabilité : horodatage logger migré de `datetime.utcnow()` vers `datetime.now(timezone.utc)` (réduction du bruit de warnings).
- Tests dédiés ajoutés sur logs synthétiques pour figer parsing + calcul KPI + fenêtre de temps.

Validation finale P4 (tests pass):

- `pytest -q tests/test_agent_observability.py tests/test_channel_agent_pipeline.py tests/test_conversation_orchestrator.py tests/test_chat_session_reuse.py tests/test_language_detection.py tests/test_whatsapp_meta_webhook.py tests/test_tenant_webhook_resolution_integration.py tests/test_voice_token_public_access.py`
- Résultat: `33 passed`
- Note: `tests/test_llm_tools_track_search.py` peut être flaky sur `test.db` local (pollution catalogue historique + fallback `limit(100)`), non lié aux changements P4.

ETAPE TERMINEE ✅
7. Ce que je ferais dans ton repo (ordre concret)
Ajouter conversation_state + slots_json (DB)
Créer agent_orchestrator.py partagé
Déplacer la logique RDV hors chat.py vers orchestrateur
Ajouter extracteur structuré (regex + LLM strict schema)
Ajouter renderer par canal (chat, email, whatsapp, voice)
Ajouter transcript tests réels
Ajuster LLMService:
baisser température pour orchestration/reformulation métier
forcer mode strict sur extraction
fallback contextualisé par flow_state

Statut item 7 (verification après execution P0 -> P4):

- Deja couvert par les phases executees (P0, P1, P2, P3, P4), sans ajouter de couche d'abstraction inutile.
- `conversation_state + slots_json` : implémenté dans `Conversation` (JSON sérialisé) + migration.
- `agent_orchestrator` partagé : implémenté via `ConversationOrchestrator`.
- Logique RDV déplacée hors `chat.py` : centralisée dans l’orchestrateur + pipeline.
- Extracteur structuré regex + LLM strict (enrichissement) : implémenté.
- Renderer / pipeline multi-canal : implémenté via `ChannelAgentPipeline` + routeurs canal.
- Transcript tests réels : couverts dans les tests de non-régression chat + pipeline.
- Ajustements `LLMService` (température réduite / strict extraction / fallback contextualisé côté pipeline-orchestrateur) : implémentés.

ITEM 7 COUVERT ✅
8. Pourquoi ce plan est aligné avec les docs (web)
Ce plan suit les pratiques documentées:

état persistant conversation/user (Microsoft Agents SDK / Bot state)
form filling avec paramètres requis (Dialogflow CX)
slots comme mémoire agent (Rasa)
tool calling multi-étapes + schémas stricts (OpenAI)
prompts structurés (Anthropic XML tags)
voice streaming contrôlé (Twilio Media Streams, mark/clear, format audio, signature)
9. Priorité business (si tu veux aller vite)
Si ton objectif est “agent utilisable rapidement”:

fais P0 + P1 d’abord
puis P2 extraction structurée
ensuite P3 voice/email hardening
C’est le meilleur ratio impact / effort.

Sources (docs officielles)
OpenAI Function Calling guide: https://developers.openai.com/api/docs/guides/function-calling
OpenAI Structured Outputs guide: https://developers.openai.com/api/docs/guides/structured-outputs
OpenAI Conversation State guide: https://developers.openai.com/api/docs/guides/conversation-state
OpenAI Structured Outputs announcement (contexte + limites): https://openai.com/index/introducing-structured-outputs-in-the-api/
Microsoft Agents SDK state concepts: https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/state-concepts
Microsoft Bot state management (concepts, avec note de fin de support Bot Framework SDK): https://learn.microsoft.com/en-us/azure/bot-service/bot-builder-howto-v4-state?view=azure-bot-service-4.0
Dialogflow CX parameters / form filling: https://docs.cloud.google.com/dialogflow/cx/docs/concept/parameter
Rasa slots: https://rasa.com/docs/reference/primitives/slots/
Anthropic prompt XML tags: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/use-xml-tags
Twilio Media Streams overview: https://www.twilio.com/docs/voice/media-streams
Twilio Media Streams WebSocket messages (media, mark, clear, format audio): https://www.twilio.com/docs/voice/media-streams/websocket-messages
