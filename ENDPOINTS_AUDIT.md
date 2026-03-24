# 🔍 Audit Backend - Inventaire complet des endpoints

**Date :** 7 janvier 2026  
**Objectif :** Identifier tous les endpoints existants et les gaps pour CRUD complet

---

## 📊 Résumé exécutif

### Statistiques globales
- **Total endpoints identifiés :** ~80+
- **Modules audités :** 12
- **Endpoints avec permissions :** ~70%
- **CRUD complets :** 2/10 modules

### État par module

| Module | GET | POST | PUT | DELETE | CRUD Complet | Permissions |
|--------|-----|------|-----|--------|--------------|-------------|
| **Calendrier** | ✅ | ✅ | ❌ | ❌ | ⚠️ 50% | ✅ |
| **Filières (KB)** | ✅ | ✅ | ❌ | ❌ | ⚠️ 50% | ✅ |
| **Personnes** | ✅ | ✅ | ❌ | ❌ | ⚠️ 50% | ⚠️ |
| **Rendez-vous** | ✅ | ✅ | ✅ | ✅ | ✅ 100% | ✅ |
| **Conversations** | ✅ | ✅ | ❌ | ❌ | ⚠️ 50% | ⚠️ |
| **Emails** | ✅ | ✅ | ❌ | ❌ | ⚠️ 50% | ❌ |
| **SMS** | ✅ | ✅ | ❌ | ❌ | ⚠️ 50% | ❌ |
| **Notifications** | ✅ | ✅ | ❌ | ❌ | ⚠️ 50% | ✅ |
| **Appels vocaux** | ✅ | ✅ | ❌ | ❌ | ⚠️ 50% | ❌ |
| **Utilisateurs** | ✅ | ✅ | ✅ | ✅ | ✅ 100% | ✅ |
| **Dashboard** | ✅ | ❌ | ❌ | ❌ | N/A | ✅ |
| **Documents (RAG)** | ✅ | ✅ | ❌ | ❌ | ⚠️ 50% | ✅ |

---

## 📁 Détail par module

### 1. 📅 Calendrier (`calendar.py`)

#### Endpoints existants
```python
POST   /calendars                    # Créer calendrier [manager]
GET    /calendars                    # Liste calendriers [viewer]
GET    /stats                        # Stats calendrier [viewer]
POST   /events                       # Créer événement [manager]
GET    /events                       # Liste événements [viewer]
GET    /availability                 # Disponibilités [viewer]
GET    /free-slots                   # Créneaux libres [viewer]
```

#### ❌ Endpoints manquants
```python
PUT    /calendars/{id}               # Modifier calendrier
DELETE /calendars/{id}               # Supprimer calendrier
PUT    /events/{id}                  # Modifier événement
DELETE /events/{id}                  # Supprimer événement
GET    /events/{id}                  # Détail événement
```

#### 🎯 Actions requises
- [ ] Ajouter PUT/DELETE pour calendriers
- [ ] Ajouter PUT/DELETE pour événements
- [ ] Ajouter GET pour détail événement
- [ ] Permissions : OK (manager/viewer)

---

### 2. 🏠 Filières admissions (`kb.py`)

#### Endpoints existants
```python
GET    /filières                        # Liste filières [viewer]
GET    /filières/{id}                   # Détail bien [viewer]
POST   /filières                        # Créer bien [manager]
POST   /filières/import-csv             # Import CSV [manager|admin]
GET    /filières/stats                  # Stats filières [viewer]
```

#### ❌ Endpoints manquants
```python
PUT    /filières/{id}                   # Modifier bien
DELETE /filières/{id}                   # Supprimer bien
POST   /filières/export-csv             # Export CSV (bonus)
```

#### 🎯 Actions requises
- [ ] **Ajouter PUT /filières/{id}** - Modifier bien
- [ ] **Ajouter DELETE /filières/{id}** - Supprimer bien
- [ ] Permissions : manager|admin
- [ ] Export CSV (optionnel)

---

### 3. 👥 Personnes (`kb.py`)

#### Endpoints existants
```python
GET    /personnes                      # Liste personnes [viewer]
GET    /personnes/{id}                 # Détail client [viewer]
POST   /personnes                      # Créer client [manager|admin]
GET    /personnes/stats                # Stats personnes [viewer]
```

#### ❌ Endpoints manquants
```python
PUT    /personnes/{id}                 # Modifier client
DELETE /personnes/{id}                 # Supprimer client (GDPR)
POST   /personnes/import-csv           # Import CSV
POST   /personnes/export-csv           # Export CSV
```

#### 🎯 Actions requises
- [ ] **Ajouter PUT /personnes/{id}** - Modifier client
- [ ] **Ajouter DELETE /personnes/{id}** - Supprimer (avec GDPR)
- [ ] Permissions : manager|admin pour CRUD
- [ ] Permissions : viewer pour lecture seule (Manager)
- [ ] Import/Export CSV

---

### 4. 📆 Rendez-vous (`kb.py`)

#### ✅ Endpoints existants (CRUD COMPLET)
```python
GET    /rendezvous/{id}              # Détail RDV [viewer]
POST   /rendezvous                   # Créer RDV [manager|admin]
PUT    /rendezvous/{id}              # Modifier RDV [manager|admin]
DELETE /rendezvous/{id}              # Supprimer RDV [manager|admin]
```

#### 🎯 Actions requises
- [ ] **Ajouter GET /rendezvous** - Liste avec filtres
- [ ] **Ajouter GET /rendezvous?view=list|calendar** - Support 2 vues
- [ ] **Ajouter GET /rendezvous?agent_id={id}** - Filtrer par agent
- [ ] Permissions : OK mais ajouter filtrage par agent pour rôle Agent

---

### 5. 💬 Conversations (`kb.py`)

#### Endpoints existants
```python
GET    /conversations                # Liste conversations [viewer]
GET    /conversations/{id}           # Détail conversation [viewer]
GET    /conversations/{id}/messages  # Messages conversation [viewer]
POST   /conversations                # Créer conversation [manager]
GET    /conversations/stats          # Stats conversations [viewer]
```

#### ❌ Endpoints manquants
```python
PUT    /conversations/{id}           # Modifier conversation
DELETE /conversations/{id}           # Supprimer/Archiver conversation
POST   /conversations/{id}/messages  # Ajouter message
PUT    /conversations/{id}/assign    # Réassigner à agent
```

#### 🎯 Actions requises
- [ ] **Ajouter PUT /conversations/{id}** - Modifier (résumé, statut)
- [ ] **Ajouter DELETE /conversations/{id}** - Archiver
- [ ] **Ajouter POST /conversations/{id}/messages** - Ajouter message
- [ ] **Ajouter PUT /conversations/{id}/assign** - Réassigner agent
- [ ] Permissions : agent peut voir/modifier les siennes uniquement

---

### 6. 📧 Emails (`kb.py` + `email_handler.py`)

#### Endpoints existants
```python
POST   /emails-logs                  # Logger email [manager|admin]
POST   /email/incoming               # Webhook email entrant [public]
POST   /email/send-appointment-confirmation  # Confirmation RDV
```

#### ❌ Endpoints manquants
```python
GET    /emails                       # Liste emails
GET    /emails/{id}                  # Détail email
POST   /emails/send                  # Envoyer email
DELETE /emails/{id}                  # Supprimer email
GET    /emails/stats                 # Stats emails
```

#### 🎯 Actions requises
- [ ] **Ajouter GET /emails** - Liste avec filtres
- [ ] **Ajouter POST /emails/send** - Composer et envoyer
- [ ] **Ajouter DELETE /emails/{id}** - Supprimer
- [ ] Permissions : manager|admin pour envoi, viewer pour lecture

---

### 7. 📱 SMS (`kb.py` + `sms.py`)

#### Endpoints existants
```python
POST   /sms-logs                     # Logger SMS [manager]
POST   /sms/incoming                 # Webhook SMS entrant [public]
```

#### ❌ Endpoints manquants
```python
GET    /sms                          # Liste SMS
GET    /sms/{id}                     # Détail SMS
POST   /sms/send                     # Envoyer SMS
DELETE /sms/{id}                     # Supprimer SMS
GET    /sms/stats                    # Stats SMS
```

#### 🎯 Actions requises
- [ ] **Ajouter GET /sms** - Liste avec filtres
- [ ] **Ajouter POST /sms/send** - Envoyer SMS
- [ ] **Ajouter DELETE /sms/{id}** - Supprimer
- [ ] Permissions : manager|agent pour envoi

---

### 8. 🔔 Notifications (`notifications.py`)

#### Endpoints existants
```python
GET    /notifications/recent         # Notifications récentes [manager]
GET    /notifications/templates      # Liste templates [manager]
GET    /notifications/templates/{id} # Détail template [manager]
POST   /notifications/templates      # Créer/Modifier template [manager]
POST   /notifications/templates/{id}/preview  # Prévisualiser [manager]
POST   /notifications/templates/{id}/send-test  # Test email [manager]
```

#### ❌ Endpoints manquants
```python
GET    /notifications                # Liste toutes notifications
PUT    /notifications/{id}/read      # Marquer comme lu
DELETE /notifications/{id}           # Supprimer notification
POST   /notifications/send           # Envoyer notification manuelle
```

#### 🎯 Actions requises
- [ ] **Ajouter GET /notifications** - Liste complète
- [ ] **Ajouter PUT /notifications/{id}/read** - Marquer lu
- [ ] **Ajouter DELETE /notifications/{id}** - Supprimer
- [ ] Permissions : viewer pour lecture, manager pour gestion

---

### 9. 📞 Appels vocaux (`voice.py`)

#### Endpoints existants
```python
GET    /voice/token                  # Token Twilio [public]
POST   /voice/outbound               # Appel sortant [public]
POST   /voice/incoming               # Webhook appel entrant [public]
```

#### ❌ Endpoints manquants
```python
GET    /calls                        # Historique appels
GET    /calls/{id}                   # Détail appel
GET    /calls/stats                  # Stats appels
DELETE /calls/{id}                   # Supprimer enregistrement
```

#### 🎯 Actions requises
- [ ] **Ajouter GET /calls** - Historique avec filtres
- [ ] **Ajouter GET /calls/stats** - Stats appels
- [ ] Permissions : agent voit ses appels, manager voit tous

---

### 10. 👤 Utilisateurs (`users.py`)

#### ✅ Endpoints existants (CRUD COMPLET)
```python
GET    /auth/users                   # Liste utilisateurs [admin]
GET    /auth/roles                   # Liste rôles [admin]
POST   /auth/users                   # Créer utilisateur [admin]
PUT    /auth/users/{id}              # Modifier utilisateur [admin]
DELETE /auth/users/{id}              # Supprimer utilisateur [admin]
```

#### 🎯 Actions requises
- [x] CRUD complet ✅
- [x] Permissions OK ✅
- [ ] Ajouter filtres avancés (par rôle, statut)

---

### 11. 📊 Dashboard (`dashboard.py`)

#### Endpoints existants
```python
GET    /dashboard/metrics/overview   # Vue d'ensemble [viewer]
GET    /dashboard/metrics/notifications  # Métriques notifs [viewer]
GET    /dashboard/overview           # Alias overview [viewer]
GET    /dashboard/notifications-series  # Série notifs [viewer]
GET    /dashboard/notifications/logs # Logs notifs [viewer]
GET    /dashboard/stats/personnes      # Stats personnes [viewer]
GET    /dashboard/stats/filières        # Stats filières [viewer]
GET    /dashboard/stats/conversations  # Stats conversations [viewer]
GET    /dashboard/trends             # Tendances [viewer]
GET    /dashboard/metrics/conversion-rate  # Taux conversion [viewer]
GET    /dashboard/metrics/satisfaction  # Satisfaction [viewer]
GET    /dashboard/metrics/response-time  # Temps réponse [viewer]
```

#### ❌ Endpoints manquants
```python
GET    /dashboard/manager/stats      # Stats globales Manager
GET    /dashboard/agent/stats        # Stats personnelles Agent
GET    /dashboard/stats/calls        # Stats appels
GET    /dashboard/stats/sms          # Stats SMS
GET    /dashboard/stats/emails       # Stats emails
```

#### 🎯 Actions requises
- [ ] **Ajouter GET /dashboard/manager/stats** - Métriques Manager
  - Nombre appels (total, réussis, échoués)
  - Nombre SMS (envoyés, reçus)
  - Nombre emails (envoyés, ouverts)
  - Conversations actives
  - RDV à venir
- [ ] **Ajouter GET /dashboard/agent/stats** - Métriques Agent
  - Mes appels aujourd'hui
  - Mes SMS envoyés
  - Mes conversations actives
  - Mes RDV à venir
- [ ] Permissions : filtrage automatique par rôle

---

### 12. 📚 Documents / RAG (`kb.py`)

#### Endpoints existants
```python
GET    /docs                         # Liste documents [viewer]
GET    /docs/search                  # Recherche documents [viewer]
POST   /docs                         # Créer document [manager|admin]
```

#### ❌ Endpoints manquants
```python
GET    /docs/{id}                    # Détail document
PUT    /docs/{id}                    # Modifier document
DELETE /docs/{id}                    # Supprimer document
```

#### 🎯 Actions requises
- [ ] **Ajouter PUT /docs/{id}** - Modifier document
- [ ] **Ajouter DELETE /docs/{id}** - Supprimer document
- [ ] Permissions : OK

---

## 🚨 Priorités critiques

### P0 - Bloquant pour v1
1. **Filières** : PUT + DELETE
2. **Personnes** : PUT + DELETE
3. **Rendez-vous** : GET liste avec filtres + vue calendrier
4. **Conversations** : PUT + DELETE + réassignation
5. **Dashboard** : Stats Manager + Stats Agent

### P1 - Important
6. **Emails** : GET liste + POST send + DELETE
7. **SMS** : GET liste + POST send + DELETE
8. **Appels** : GET historique + stats
9. **Notifications** : PUT read + DELETE
10. **Calendrier** : PUT + DELETE événements

### P2 - Nice to have
11. Import/Export CSV pour personnes
12. Templates emails avancés
13. Statistiques détaillées par période

---

## 🔐 Audit des permissions

### Permissions actuelles

| Endpoint | Permission actuelle | Permission cible |
|----------|-------------------|------------------|
| `/filières` POST | `manager` | `admin\|manager` ✅ |
| `/filières` PUT | ❌ Manquant | `admin\|manager` |
| `/filières` DELETE | ❌ Manquant | `admin\|manager` |
| `/personnes` POST | `manager\|admin` | `admin\|manager` ✅ |
| `/personnes` PUT | ❌ Manquant | `admin\|manager` |
| `/personnes` DELETE | ❌ Manquant | `admin` (GDPR) |
| `/rendezvous` * | `manager\|admin` | `admin\|manager\|agent` (filtrés) |
| `/conversations` * | `manager` | `admin\|manager\|agent` (filtrés) |
| `/emails` * | ❌ Manquant | `admin\|manager` |
| `/sms` * | `manager` | `admin\|manager\|agent` |
| `/notifications` * | `manager` | `viewer` (lecture), `manager` (gestion) |

### ⚠️ Problèmes identifiés

1. **Emails** : Aucune permission sur endpoints manquants
2. **SMS** : Permissions trop restrictives (manager only)
3. **Appels** : Aucune permission (endpoints publics)
4. **Conversations** : Pas de filtrage par agent
5. **Rendez-vous** : Pas de filtrage par agent

---

## 📝 Plan d'action détaillé

### Semaine 1 : CRUD Filières + Personnes
- [ ] Jour 1-2 : Implémenter PUT/DELETE filières
- [ ] Jour 3-4 : Implémenter PUT/DELETE personnes
- [ ] Jour 5 : Tests et permissions

### Semaine 2 : Rendez-vous + Conversations
- [ ] Jour 1-2 : Liste RDV avec filtres + vue calendrier
- [ ] Jour 3-4 : PUT/DELETE conversations + réassignation
- [ ] Jour 5 : Tests et filtrage par agent

### Semaine 3 : Emails + SMS + Appels
- [ ] Jour 1-2 : CRUD emails complet
- [ ] Jour 3 : CRUD SMS complet
- [ ] Jour 4-5 : Historique appels + stats

### Semaine 4 : Dashboards + Finitions
- [ ] Jour 1-2 : Dashboard Manager avec métriques
- [ ] Jour 3 : Dashboard Agent personnalisé
- [ ] Jour 4-5 : Tests finaux + permissions

---

## ✅ Checklist de validation

### Backend
- [ ] Tous les modules ont CRUD complet
- [ ] Permissions vérifiées sur tous les endpoints
- [ ] Filtrage par agent pour rôle Agent
- [ ] Logs des actions sensibles
- [ ] Tests unitaires pour nouveaux endpoints

### Frontend
- [ ] Pages avec actions CRUD fonctionnelles
- [ ] RoleGuard sur toutes les routes
- [ ] Vue liste + calendrier pour RDV
- [ ] Dashboards personnalisés par rôle
- [ ] Feedback utilisateur sur toutes les actions

### Sécurité
- [ ] Aucun endpoint accessible sans auth
- [ ] Permissions backend ET frontend
- [ ] Pas de fuite de données entre agents
- [ ] GDPR respecté (suppression personnes)

---

## 📊 Métriques de succès

- **CRUD complet** : 10/10 modules ✅
- **Permissions** : 100% des endpoints protégés ✅
- **Dashboards** : 3 vues (Admin, Manager, Agent) ✅
- **Tests** : 80% de couverture ✅
- **Performance** : < 2s temps de chargement ✅

---

**Prochaine étape :** Commencer par les filières admissions (PUT + DELETE)
