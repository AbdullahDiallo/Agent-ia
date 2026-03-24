# 🚀 Feuille de route v1 - Application complète et opérationnelle

## 📊 État actuel

L'application dispose déjà de :
- ✅ Authentification complète (login, OTP, JWT)
- ✅ Système de rôles (Admin, Manager, Agent, Viewer)
- ✅ Dashboard de base
- ✅ Gestion des utilisateurs (Admin)
- ✅ Profil utilisateur avec avatar
- ✅ Notifications
- ✅ Conversations
- ✅ Appels vocaux
- ✅ SMS
- ✅ Emails
- ✅ Calendrier
- ✅ Base de données personnes
- ✅ Fonctionnalités IA (sentiment, scoring admissions, résumés)

## 🎯 Objectif v1

Transformer le projet en **application production-ready** avec :
1. **Permissions strictes** par rôle (Admin, Manager, Agent)
2. **CRUD complet** sur toutes les entités
3. **Dashboards personnalisés** par rôle
4. **UX finalisée** avec vues multiples (liste/calendrier)

---

## 📋 Plan détaillé

### Phase 1 : Audit et inventaire (1-2h)

#### 1.1 Audit Backend
- [ ] Lister tous les endpoints existants par module
- [ ] Identifier les endpoints manquants (CREATE, UPDATE, DELETE)
- [ ] Vérifier les permissions actuelles sur chaque endpoint
- [ ] Documenter les formats d'import/export (CSV, etc.)

**Fichiers à auditer :**
```
app/routers/
├── agents.py          # Gestion agents IA
├── calendar.py        # Rendez-vous
├── chat.py           # Conversations
├── dashboard.py      # Métriques
├── email_handler.py  # Emails
├── kb.py            # Base de connaissances (filières?)
├── school_people.py # Personnes / admissions
├── notifications.py # Notifications
├── sms.py           # SMS
├── voice.py         # Appels vocaux
└── users.py         # Gestion utilisateurs
```

#### 1.2 Audit Frontend
- [ ] Lister toutes les pages existantes
- [ ] Identifier les pages en lecture seule vs CRUD complet
- [ ] Vérifier les guards de rôle sur chaque route

**Pages existantes :**
```
pages/
├── FilièresPage.tsx          # Gestion filières admissions
├── CalendarPage.tsx       # Vue calendrier
├── CallsPage.tsx          # Historique appels
├── PersonnesPage.tsx        # Liste personnes
├── ConversationsPage.tsx  # Conversations
├── DashboardPage.tsx      # Dashboard principal
├── EmailsPage.tsx         # Gestion emails
├── NotificationsPage.tsx  # Notifications
├── SmsPage.tsx           # Gestion SMS
├── UsersManagementPage.tsx # Gestion utilisateurs (Admin)
└── ...
```

---

### Phase 2 : Rôle ADMIN - CRUD complet (3-4h)

#### 2.1 Gestion des Filières
**Backend :**
- [ ] `POST /filières` - Créer un bien
- [ ] `PUT /filières/{id}` - Modifier un bien
- [ ] `DELETE /filières/{id}` - Supprimer un bien
- [ ] `POST /filières/import` - Import CSV (si supporté)
- [ ] Ajouter `@require_role("admin")` sur ces endpoints

**Frontend (FilièresPage.tsx) :**
- [ ] Bouton "Ajouter un bien" → Modal de création
- [ ] Actions "Modifier" et "Supprimer" sur chaque ligne
- [ ] Bouton "Importer CSV" si backend supporte
- [ ] Confirmation avant suppression
- [ ] Toast de succès/erreur

#### 2.2 Gestion des Rendez-vous
**Backend :**
- [ ] `POST /calendar/appointments` - Créer RDV
- [ ] `PUT /calendar/appointments/{id}` - Modifier RDV
- [ ] `DELETE /calendar/appointments/{id}` - Supprimer RDV
- [ ] `GET /calendar/appointments?view=list|calendar` - Support 2 vues

**Frontend (CalendarPage.tsx) :**
- [ ] Toggle "Vue Liste" / "Vue Calendrier"
- [ ] Vue Liste : tableau avec actions CRUD
- [ ] Vue Calendrier : intégration FullCalendar ou react-big-calendar
- [ ] Modal de création/édition de RDV
- [ ] Drag & drop pour déplacer RDV (calendrier)
- [ ] Filtres par agent, statut, date

#### 2.3 Gestion des Emails
**Frontend (EmailsPage.tsx) :**
- [ ] Bouton "Composer un email"
- [ ] Actions "Répondre", "Transférer", "Supprimer"
- [ ] Filtres par statut (envoyé, brouillon, erreur)

#### 2.4 Gestion des SMS
**Frontend (SmsPage.tsx) :**
- [ ] Bouton "Envoyer un SMS"
- [ ] Historique complet avec actions
- [ ] Filtres par statut et date

#### 2.5 Gestion des Conversations
**Frontend (ConversationsPage.tsx) :**
- [ ] Actions "Archiver", "Supprimer"
- [ ] Réassigner à un autre agent
- [ ] Filtres avancés

#### 2.6 Gestion des Notifications
**Frontend (NotificationsPage.tsx) :**
- [ ] Actions "Marquer comme lu", "Supprimer"
- [ ] Filtres par type et date

---

### Phase 3 : Rôle MANAGER (2-3h)

#### 3.1 Dashboard Manager
**Backend :**
- [ ] `GET /dashboard/manager/stats` - Métriques globales
  - Nombre d'appels (total, réussis, échoués)
  - Nombre de SMS (envoyés, reçus)
  - Nombre d'emails (envoyés, ouverts, cliqués)
  - Nombre de conversations actives
  - Nombre de RDV (à venir, passés)
  - Taux de conversion candidatures

**Frontend (DashboardPage.tsx) :**
- [ ] Affichage conditionnel selon rôle
- [ ] Cartes de métriques avec icônes
- [ ] Graphiques (Chart.js ou Recharts)
- [ ] Période sélectionnable (7j, 30j, 90j)

#### 3.2 Permissions Manager
**Backend :**
- [ ] Ajouter `@require_role("admin|manager")` sur :
  - Gestion filières (CRUD)
  - Gestion RDV (CRUD)
  - Gestion emails (CRUD)
  - Gestion conversations (CRUD)
  - Gestion SMS (CRUD)
  - Appels vocaux (lecture/création)
  - Notifications (lecture)
- [ ] `GET /personnes` en lecture seule pour manager

**Frontend :**
- [ ] Afficher les pages avec `<RoleGuard roles={["admin", "manager"]}>`
- [ ] PersonnesPage en lecture seule (masquer boutons CRUD)

---

### Phase 4 : Rôle AGENT (2-3h)

#### 4.1 Dashboard Agent
**Backend :**
- [ ] `GET /dashboard/agent/stats` - Métriques personnelles
  - Mes appels (aujourd'hui, cette semaine)
  - Mes SMS envoyés
  - Mes conversations actives
  - Mes RDV à venir
  - Mes tâches en attente

**Frontend (DashboardPage.tsx) :**
- [ ] Vue personnalisée pour agent
- [ ] Liste des prochains RDV
- [ ] Conversations en attente
- [ ] Notifications importantes

#### 4.2 Gestion des Rendez-vous (Agent)
**Backend :**
- [ ] `GET /calendar/appointments?agent_id={current_user}` - Filtrer par agent
- [ ] `POST /calendar/appointments` - Créer ses RDV
- [ ] `PUT /calendar/appointments/{id}` - Modifier ses RDV (vérifier ownership)
- [ ] `DELETE /calendar/appointments/{id}` - Supprimer ses RDV (vérifier ownership)

**Frontend (CalendarPage.tsx) :**
- [ ] Filtrer automatiquement par agent connecté
- [ ] CRUD complet sur ses propres RDV
- [ ] Notification si RDV assigné par manager

#### 4.3 Permissions Agent
**Backend :**
- [ ] Ajouter `@require_role("admin|manager|agent")` sur :
  - Conversations (lecture/écriture sur les siennes)
  - SMS (envoi)
  - Appels vocaux (création/réception)
  - Notifications (lecture)
- [ ] Filtrer automatiquement les données par `agent_id`

**Frontend :**
- [ ] ConversationsPage : voir uniquement ses conversations
- [ ] SmsPage : voir uniquement ses SMS
- [ ] CallsPage : voir uniquement ses appels

---

### Phase 5 : Verrouillage et sécurité (1-2h)

#### 5.1 Backend - Guards systématiques
- [ ] Ajouter `Principal = Depends(get_principal)` sur TOUS les endpoints protégés
- [ ] Vérifier les permissions sur chaque action CRUD
- [ ] Filtrer les données par `agent_id` pour les agents
- [ ] Logger toutes les actions sensibles (création, modification, suppression)

#### 5.2 Frontend - RoleGuard partout
- [ ] Wrapper toutes les routes avec `<RoleGuard>`
- [ ] Masquer les boutons d'action selon les permissions
- [ ] Afficher des messages clairs si accès refusé
- [ ] Rediriger vers dashboard si route non autorisée

#### 5.3 Tests de permissions
- [ ] Se connecter en tant qu'Admin → vérifier accès complet
- [ ] Se connecter en tant que Manager → vérifier restrictions
- [ ] Se connecter en tant qu'Agent → vérifier isolation des données
- [ ] Tester les tentatives d'accès direct via URL

---

### Phase 6 : UX et finitions (2-3h)

#### 6.1 Composants réutilisables
- [ ] `<DataTable>` avec actions CRUD intégrées
- [ ] `<ConfirmDialog>` pour les suppressions
- [ ] `<ImportModal>` pour les imports CSV
- [ ] `<StatsCard>` pour les métriques dashboard

#### 6.2 Vue Calendrier (rendez-vous)
- [ ] Intégrer `react-big-calendar` ou `FullCalendar`
- [ ] Drag & drop pour déplacer RDV
- [ ] Clic sur un créneau → créer RDV
- [ ] Clic sur un RDV → modal de détails/édition
- [ ] Couleurs par statut (confirmé, en attente, annulé)

#### 6.3 Feedback utilisateur
- [ ] Toast notifications pour toutes les actions
- [ ] Loading spinners sur les actions async
- [ ] Messages d'erreur explicites
- [ ] Confirmations avant suppressions
- [ ] Animations de transition

#### 6.4 Responsive
- [ ] Tester toutes les pages sur mobile
- [ ] Adapter les tableaux (scroll horizontal ou cards)
- [ ] Menu burger fonctionnel
- [ ] Modals adaptés mobile

---

## 🔧 Stack technique recommandée

### Backend (déjà en place)
- FastAPI + SQLAlchemy
- JWT avec cookies httpOnly
- Redis pour cache/sessions
- PostgreSQL

### Frontend (déjà en place)
- React + TypeScript
- TailwindCSS
- Lucide Icons
- React Router

### Nouveaux packages suggérés
```bash
# Frontend
npm install react-big-calendar date-fns
npm install recharts  # Graphiques
npm install react-csv  # Export CSV
npm install react-hot-toast  # Notifications (si pas déjà installé)

# Backend
pip install pandas  # Import CSV
pip install openpyxl  # Import Excel (optionnel)
```

---

## 📊 Estimation totale

| Phase | Durée estimée | Priorité |
|-------|---------------|----------|
| Phase 1 : Audit | 1-2h | 🔴 Critique |
| Phase 2 : Admin CRUD | 3-4h | 🔴 Critique |
| Phase 3 : Manager | 2-3h | 🟠 Haute |
| Phase 4 : Agent | 2-3h | 🟠 Haute |
| Phase 5 : Sécurité | 1-2h | 🔴 Critique |
| Phase 6 : UX | 2-3h | 🟡 Moyenne |
| **TOTAL** | **11-17h** | - |

---

## 🎯 Ordre d'exécution recommandé

1. **Audit complet** (Phase 1) → comprendre l'existant
2. **Sécurité** (Phase 5) → verrouiller les permissions AVANT d'ajouter des fonctionnalités
3. **Admin CRUD** (Phase 2) → fonctionnalités de base
4. **Manager** (Phase 3) → délégation de pouvoir
5. **Agent** (Phase 4) → isolation des données
6. **UX** (Phase 6) → polish final

---

## ✅ Critères de validation v1

### Fonctionnel
- [ ] Admin peut tout créer/modifier/supprimer
- [ ] Manager a accès aux outils de gestion mais pas aux utilisateurs
- [ ] Agent ne voit que ses propres données
- [ ] Dashboards affichent les bonnes métriques par rôle
- [ ] Vue calendrier + vue liste pour les RDV

### Sécurité
- [ ] Aucun endpoint accessible sans authentification
- [ ] Permissions vérifiées côté backend ET frontend
- [ ] Logs des actions sensibles
- [ ] Pas de fuite de données entre agents

### UX
- [ ] Toutes les actions ont un feedback visuel
- [ ] Confirmations avant suppressions
- [ ] Messages d'erreur clairs
- [ ] Application responsive
- [ ] Navigation intuitive

### Performance
- [ ] Temps de chargement < 2s
- [ ] Pas de lag sur les interactions
- [ ] Pagination sur les grandes listes

---

## 🚀 Prochaines étapes

**Commencer par :**
1. Lancer l'audit backend (lister tous les endpoints)
2. Identifier les endpoints manquants
3. Créer un fichier `ENDPOINTS_AUDIT.md` avec l'inventaire complet

**Dis-moi quand tu es prêt et on commence par l'audit !** 🎯
