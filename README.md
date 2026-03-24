# Salma School Assistant API

## Démarrage rapide

1. Créer un `.env` en partant de `.env.example`.
2. Installer dépendances:
``` 
pip install -r requirements.txt
```
3. Lancer en local:
```
./scripts/run_backend_dev.sh
```
4. Configurer Twilio pour pointer sur `https://votre-domaine/voice/incoming` (ou via ngrok) et `wss://votre-domaine/media/stream`.

## Sécurité
- Variables d'environnement uniquement, aucune clé en dur.
- Journaux avec masquage PII.
- Champs PII chiffrés côté application (AES-GCM).
- Authentification JWT (RS256) pour endpoints admin.

## À faire
- Intégration STT (Deepgram/Whisper) en streaming.
- Intégration LLM (GPT-5) avec persona et règles.
- TTS (ElevenLabs) en streaming.
- Envoi d'email (SendGrid) post-appel.
- WAF/IDS et monitoring.
