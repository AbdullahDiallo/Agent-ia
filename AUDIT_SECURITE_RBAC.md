# 🔒 AUDIT SÉCURITÉ ET RBAC - Phase 1

**Date**: 13 janvier 2026  
**Objectif**: Analyse complète de l'existant avant implémentation pour éviter duplications

---

## 📊 ÉTAT DES LIEUX

### ✅ Système d'authentification existant

#### Backend (`app/security.py`)
- ✅ **JWT avec cookies httpOnly** : Tokens sécurisés (access_token, refresh_token)
- ✅ **Classe `Principal`** : Contient `sub`, `roles[]`, `permissions[]`
- ✅ **`get_principal()`** : Dependency pour extraire le principal du JWT
- ✅ **`require_role(required: str)`** : Decorator pour vérifier les rôles
  - Support multi-rôles avec `|` (ex: "manager|admin")
  - Admin = super-role (accès à tout)
- ✅ **`require_permission(required: str)`** : Decorator pour permissions granulaires
- ✅ **Blacklist Redis** : Révocation de tokens
- ✅ **`CookieOrBearerAuth`** : Support cookie (priorité) + Authorization header (fallback)

#### Frontend (`hooks/useAuth.tsx`)
- ✅ **AuthContext** : Gestion centralisée de l'authentification
- ✅ **Cookies httpOnly** : Tokens gérés côté serveur
- ✅ **`hasRole(role: string)`** : Vérification rôles avec support multi-rôles (`|`)
- ✅ **`hasPermission(permission: string)`** : Vérification permissions
- ✅ **Admin = super-role** : Accès à tout automatiquement
- ✅ **Chargement profil** : Récupération roles, email, nom, avatar

#### Guards Frontend
- ✅ **`AuthGuard`** : Protège routes nécessitant authentification
- ✅ **`RoleGuard`** : Protège routes par rôle avec message d'accès refusé
- ✅ **`PermissionGuard`** : Protège routes par permission

---

## 🗂️ MODÈLES DE DONNÉES

### Rôles disponibles
```python
# app/models.py
- Role (table: roles) - id, name
- Permission (table: permissions) - id, name
- RolePermission (table: role_permissions) - role_id, permission_id
- User (table: users) - role_id (FK vers roles)
- Agent (table: agents) - user_id (FK vers users)
- Manager (table: managers) - user_id (FK vers users)
- Viewer (table: viewers) - user_id (FK vers users)
```

### Rôles identifiés dans le code
1. **admin** - Super-role, accès complet
2. **manager** - Gestion opérationnelle
3. **agent** - Agents admissions
4. **viewer** - Lecture seule

---

## 📡 AUDIT DES ENDPOINTS BACKEND

### ✅ Endpoints DÉJÀ PROTÉGÉS

#### `/kb` (Knowledge Base)
- **Router global**: `require_role("viewer")` ✅
- **POST /personnes**: `require_role("manager|admin")` ✅
- **PUT /personnes/{id}**: `require_role("manager|admin")` ✅
- **DELETE /personnes/{id}**: `require_role("manager|admin")` ✅
- **POST /filières**: `require_role("manager")` ✅
- **PUT /filières/{id}**: `require_role("manager|admin")` ✅
- **DELETE /filières/{id}**: `require_role("manager|admin")` ✅
- **POST /filières/import-csv**: `require_role("manager|admin")` ✅
- **POST /docs**: `require_role("manager|admin")` ✅
- **POST /conversations**: `require_role("manager")` ✅
- **POST /rendezvous**: `require_role("manager|admin")` ✅
- **PUT /rendezvous/{id}**: `require_role("manager|admin")` ✅
- **DELETE /rendezvous/{id}**: `require_role("manager|admin")` ✅
- **POST /emails-logs**: `require_role("manager|admin")` ✅
- **POST /sms-logs**: `require_role("manager")` ✅

#### `/dashboard`
- **Router global**: `require_role("viewer")` ✅

#### `/calendar`
- **POST /calendars**: `require_role("manager")` ✅
- **GET /calendars**: `require_role("viewer")` ✅
- **GET /stats**: `require_role("viewer")` ✅
- **POST /events**: `require_role("manager")` ✅
- **GET /events**: `require_role("viewer")` ✅
- **GET /availability**: `require_role("viewer")` ✅
- **GET /free-slots**: `require_role("viewer")` ✅

#### `/notifications`
- **Router global**: `require_role("manager")` ✅

#### `/users`
- **Tous les endpoints**: `get_principal` (authentification requise) ✅
- **Vérification admin manuelle** dans chaque endpoint ✅

#### `/school/persons`
- **GET /**: `require_role("agent|viewer|manager|admin")` ✅
- **POST /**: `require_role("agent|manager|admin")` ✅
- **PUT /{id}**: `require_role("agent|manager|admin")` ✅

#### `/dev` (DevTools)
- **Router global**: `require_role("admin")` ✅

#### `/gdpr`
- **POST /delete**: `require_role("admin")` ✅

#### `/outbound_calls`
- **POST /call**: `require_role("manager")` ✅

#### `/ai_features`
- **Tous les endpoints**: `get_principal` (authentification requise) ✅

---

### ⚠️ ENDPOINTS NON PROTÉGÉS (À VÉRIFIER)

#### `/kb` - Endpoints en lecture
- ❌ **GET /conversations** - Pas de protection rôle spécifique (hérite de router "viewer")
- ❌ **GET /personnes** - Pas de protection rôle spécifique (hérite de router "viewer")
- ❌ **GET /personnes/{id}** - Pas de protection rôle spécifique
- ❌ **GET /filières** - Pas de protection rôle spécifique
- ❌ **GET /filières/{id}** - Pas de protection rôle spécifique
- ❌ **GET /rendezvous** - Pas de protection rôle spécifique
- ❌ **GET /agents** - Pas de protection rôle spécifique
- ❌ **GET /personnes/stats** - Pas de protection rôle spécifique
- ❌ **GET /filières/stats** - Pas de protection rôle spécifique
- ❌ **GET /conversations/stats** - Pas de protection rôle spécifique

**Note**: Ces endpoints héritent de `require_role("viewer")` du router global, donc techniquement protégés, mais pas de filtrage par agent_id pour les agents.

#### `/email_handler`
- ⚠️ **POST /email/incoming** - Webhook public (normal)
- ⚠️ **POST /email/send-appointment-confirmation** - Pas de protection visible

#### `/sms`
- ⚠️ **POST /sms/incoming** - Webhook public (normal)

#### `/manual_intervention`
- ❌ **POST /conversations/{id}/take-control** - Pas de protection
- ❌ **POST /conversations/{id}/release-control** - Pas de protection
- ❌ **POST /conversations/{id}/close** - Pas de protection
- ❌ **POST /conversations/{id}/reopen** - Pas de protection
- ❌ **POST /email/send-manual** - Pas de protection
- ❌ **POST /whatsapp/send-manual** - Pas de protection
- ✅ **GET /conversations/pending-review**: `require_role("agent|manager|admin")` ✅
- ❌ **GET /conversations/assigned-to-me** - Pas de protection

#### `/rag`
- ❌ **POST /answer** - Pas de protection

#### `/voice`, `/voice_recording`, `/whatsapp`, `/chat`
- ⚠️ À auditer (webhooks vs endpoints internes)

---

## 🎨 AUDIT FRONTEND

### ✅ Routes DÉJÀ PROTÉGÉES

#### Routes avec `ProtectedRoute` (AuthGuard)
- ✅ `/dashboard` - Tous les utilisateurs authentifiés
- ✅ `/dashboard/persona`
- ✅ `/dashboard/calendar`
- ✅ `/dashboard/personnes`
- ✅ `/dashboard/filières`
- ✅ `/dashboard/rendezvous`
- ✅ `/dashboard/conversations`
- ✅ `/dashboard/conversations/:id`
- ✅ `/dashboard/emails`
- ✅ `/dashboard/calls`
- ✅ `/dashboard/sms`
- ✅ `/dashboard/whatsapp`
- ✅ `/dashboard/notifications`
- ✅ `/dashboard/templates`
- ✅ `/dashboard/documents`
- ✅ `/dashboard/profile`
- ✅ `/dashboard/settings`

#### Routes avec `RoleGuard`
- ✅ `/dashboard/users` - `require_role("admin")` avec message d'accès refusé

---

### ❌ PROBLÈMES IDENTIFIÉS

#### 1. Sidebar (`Layout.tsx`)
- ✅ **Admin navigation** : Filtrée avec `hasRole('admin')`
- ❌ **Navigation principale** : Affichée pour TOUS les utilisateurs authentifiés
- ❌ **Pas de filtrage par rôle** pour les menus (Personnes, Filières, Conversations, etc.)

**Impact**: Un agent voit tous les menus même s'il ne devrait avoir accès qu'à ses propres données.

#### 2. Boutons d'action dans les pages
- ❌ **Pas de vérification de rôle** avant d'afficher les boutons CRUD
- Exemple: Un viewer peut voir les boutons "Créer", "Modifier", "Supprimer" même s'il n'a pas les permissions

#### 3. Pas de dashboards spécifiques par rôle
- ❌ **DashboardPage** : Même vue pour tous les rôles
- ❌ **Pas de dashboard Manager** avec métriques d'équipe
- ❌ **Pas de dashboard Agent** avec métriques personnelles
- ❌ **Pas de dashboard Viewer** en lecture seule

#### 4. Filtrage des données
- ❌ **Backend**: Pas de filtrage automatique par `agent_id` pour les agents
- Exemple: Un agent peut voir TOUS les personnes/rendez-vous au lieu de seulement les siens

---

## 🎯 PLAN D'ACTION

### Phase 1A : Backend - Sécurité stricte

#### 1. Ajouter filtrage automatique par agent_id
```python
# Pour les agents, filtrer automatiquement les données
# Dans chaque endpoint GET de /kb:
if "agent" in principal.roles and "admin" not in principal.roles:
    # Récupérer l'agent_id depuis principal.sub (user_id)
    # Filtrer les résultats par agent_id
```

**Endpoints concernés**:
- GET /kb/personnes
- GET /kb/rendezvous
- GET /kb/conversations
- GET /kb/filières (selon la logique métier)

#### 2. Protéger les endpoints d'intervention manuelle
```python
# /manual_intervention endpoints
@router.post("/conversations/{id}/take-control", dependencies=[Depends(require_role("agent|manager|admin"))])
@router.post("/conversations/{id}/release-control", dependencies=[Depends(require_role("agent|manager|admin"))])
@router.post("/conversations/{id}/close", dependencies=[Depends(require_role("agent|manager|admin"))])
@router.post("/email/send-manual", dependencies=[Depends(require_role("manager|admin"))])
```

#### 3. Ajouter logging des actions sensibles
```python
# Utiliser AuditEvent pour logger:
# - Création/modification/suppression de personnes
# - Création/modification/suppression de filières
# - Création/modification/suppression de rendez-vous
# - Envoi manuel d'emails/SMS
# - Prise de contrôle de conversations
```

#### 4. Créer endpoints dashboards par rôle
```python
# /dashboard/manager/stats - Métriques équipe
# /dashboard/agent/stats - Métriques personnelles
# /dashboard/viewer/stats - Métriques lecture seule
```

---

### Phase 1B : Frontend - UX par rôle

#### 1. Créer composant `<RoleBasedMenu>`
```tsx
// Filtrer les menus de navigation selon le rôle
const getNavigationForRole = (roles: string[]) => {
  if (roles.includes('admin')) return allMenus;
  if (roles.includes('manager')) return managerMenus;
  if (roles.includes('agent')) return agentMenus;
  if (roles.includes('viewer')) return viewerMenus;
  return [];
};
```

#### 2. Créer composant `<ActionButton>`
```tsx
// Bouton qui se masque selon les permissions
<ActionButton 
  requiredRole="manager|admin"
  onClick={handleCreate}
>
  Créer
</ActionButton>
```

#### 3. Créer dashboards spécifiques
```tsx
// DashboardPage.tsx
const DashboardPage = () => {
  const { hasRole } = useAuth();
  
  if (hasRole('admin')) return <AdminDashboard />;
  if (hasRole('manager')) return <ManagerDashboard />;
  if (hasRole('agent')) return <AgentDashboard />;
  return <ViewerDashboard />;
};
```

#### 4. Adapter toutes les routes avec RoleGuard
```tsx
// App.tsx - Ajouter RoleGuard sur chaque route
<Route path="/dashboard/personnes" element={
  <ProtectedRoute>
    <RoleGuard requiredRole="viewer">
      <Layout><PersonnesPage /></Layout>
    </RoleGuard>
  </ProtectedRoute>
} />
```

---

## 📋 MATRICE DES PERMISSIONS

### Admin
- ✅ Accès complet à tout
- ✅ Gestion utilisateurs
- ✅ Paramètres système
- ✅ Tous les CRUD

### Manager
- ✅ Vue d'ensemble équipe
- ✅ CRUD personnes, filières, rendez-vous, conversations
- ✅ Envoi emails/SMS
- ✅ Gestion agents
- ❌ Gestion utilisateurs
- ❌ Paramètres système

### Agent
- ✅ Ses propres personnes
- ✅ Ses propres rendez-vous
- ✅ Ses propres conversations
- ✅ Envoi SMS
- ❌ Modification autres agents
- ❌ Gestion filières (lecture seule)
- ❌ Gestion utilisateurs

### Viewer
- ✅ Lecture seule sur tout
- ❌ Aucune modification
- ❌ Aucune création
- ❌ Aucune suppression

---

## ✅ CONCLUSION

### Points forts existants
1. ✅ Système d'authentification robuste (JWT + cookies httpOnly)
2. ✅ Guards frontend fonctionnels (AuthGuard, RoleGuard)
3. ✅ Système de rôles backend opérationnel
4. ✅ Majorité des endpoints protégés

### Points à améliorer
1. ❌ Filtrage automatique par agent_id manquant
2. ❌ Endpoints d'intervention manuelle non protégés
3. ❌ Sidebar affiche tous les menus pour tous les rôles
4. ❌ Boutons d'action non filtrés par rôle
5. ❌ Pas de dashboards spécifiques par rôle
6. ❌ Logging des actions sensibles incomplet

### Estimation
- **Backend**: 3-4h
- **Frontend**: 4-5h
- **Tests**: 2h
- **Total**: 9-11h

---

**Prochaine étape**: Implémenter le filtrage automatique par agent_id dans les endpoints backend.
