# UAT Checklist (V1)

References:
- PV template: `tests/UAT_PV_TEMPLATE.md`
- Live scenario 4 channels: `tests/UAT_SCENARIO_4_CHANNELS.md`

1. Login admin avec OTP valide et accès dashboard.
2. Vérifier refus login après erreurs répétées (lockout).
3. Vérifier refus webhook non signé (Meta WhatsApp, email inbound, Twilio).
4. Vérifier parcours chat widget: question admission -> réponse cohérente.
5. Vérifier parcours SMS/WhatsApp: réponse + log conversation.
6. Vérifier création RDV + relance via scheduler.
7. Vérifier dashboard admin: KPI sans valeur statique.
8. Vérifier absence de termes legacy de l’ancien domaine dans l’UI principale.
9. Vérifier endpoint sensible (`/auth/test-email`, `/seed/*`) inaccessible si `ENABLE_DEV_ENDPOINTS=false`.
10. Vérifier RBAC: viewer ne peut pas accéder aux routes manager/admin.
