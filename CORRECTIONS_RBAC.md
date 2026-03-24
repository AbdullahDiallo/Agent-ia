# 🔧 Corrections RBAC - Erreurs 403 Agent

**Date**: 13 janvier 2026  
**Problème**: Les agents recevaient des erreurs 403 Forbidden sur tous les endpoints

---

## 🐛 Problèmes Identifiés

### 1. Router `/kb` trop restrictif
**Avant**: `dependencies=[Depends(require_role("viewer"))]`  
**Problème**: Les agents n'ont que le rôle "agent", pas "viewer"  
**Solution**: Accepter tous les rôles authentifiés

### 2. Router `/notifications` limité aux managers
**Avant**: `dependencies=[Depends(require_role("manager"))]`  
**Problème**: Les agents ne pouvaient pas voir leurs notifications  
**Solution**: Accepter agent|manager|admin

### 3. Router `/agents` sans protection
**Avant**: Aucune dépendance globale  
**Problème**: Les agents doivent pouvoir lire la liste des agents (pour les rendez-vous)  
**Solution**: Ajouter dépendance globale agent|viewer|manager|admin

---

## ✅ Corrections Appliquées

### 1. `/app/routers/kb.py` (ligne 26)
```python
# AVANT
router = APIRouter(prefix="/kb", tags=["kb"], dependencies=[Depends(require_role("viewer"))])

# APRÈS
router = APIRouter(prefix="/kb", tags=["kb"], dependencies=[Depends(require_role("agent|viewer|manager|admin"))])
```

### 2. `/app/routers/notifications.py` (ligne 15)
```python
# AVANT
router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(require_role("manager"))])

# APRÈS
router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(require_role("agent|manager|admin"))])
```

### 3. `/app/routers/agents.py` (ligne 21)
```python
# AVANT
router = APIRouter(prefix="/agents", tags=["agents"])

# APRÈS
router = APIRouter(prefix="/agents", tags=["agents"], dependencies=[Depends(require_role("agent|viewer|manager|admin"))])
```

---

## 📋 Clarifications

### Question 1: "Admin redirigé vers ManagerDashboard"
✅ **C'est normal !** L'admin utilise le dashboard Manager car il a besoin de voir les métriques de l'équipe. Le dashboard Manager est le plus complet.

### Question 2: "Pourquoi filtrer les conversations par agent ?"
**Réponse**: Les agents ne doivent voir que **leurs propres conversations** (celles qui leur sont assignées), pas toutes les conversations de l'entreprise. C'est pour la confidentialité et pour ne pas les surcharger d'informations inutiles.

**Exemple**:
- Agent A a 5 conversations assignées → Il voit uniquement ces 5
- Agent B a 3 conversations assignées → Il voit uniquement ces 3
- Manager voit TOUTES les conversations (8 au total)

**Code de filtrage** (`app/routers/kb.py` ligne 134-138):
```python
if should_filter_by_agent(principal):
    agent = get_agent_from_principal(db, principal)
    if agent:
        # Récupérer l'user_id de l'agent
        query = query.filter(Conversation.assigned_to == agent.user_id)
```

### Question 3: "Erreurs 403 pour l'agent"
✅ **Corrigé !** Les 3 routers ont été mis à jour pour accepter le rôle "agent".

---

## 🧪 Tests à Effectuer

### Test 1: Se connecter en tant qu'Agent
```bash
# Redémarrer le backend
cd /Users/dialloabdoulaye/Desktop/AgentIA
source venv/bin/activate
uvicorn app.main:app --reload

# Se connecter en tant qu'agent dans le frontend
# Vérifier:
# ✅ Dashboard Agent s'affiche
# ✅ Pas d'erreurs 403
# ✅ Voir uniquement ses propres RDV/personnes/conversations
```

### Test 2: Vérifier les endpoints
Les agents devraient maintenant pouvoir accéder à:
- ✅ `GET /kb/personnes` (filtrés par agent)
- ✅ `GET /kb/rendezvous` (filtrés par agent)
- ✅ `GET /kb/conversations` (filtrées par agent)
- ✅ `GET /kb/filières` (lecture seule)
- ✅ `GET /agents` (lecture seule)
- ✅ `GET /dashboard/agent/stats` (ses métriques)
- ✅ `GET /notifications/recent` (ses notifications)

---

## 🔒 Matrice des Permissions Mise à Jour

| Endpoint | Agent | Viewer | Manager | Admin |
|----------|-------|--------|---------|-------|
| `GET /kb/personnes` | ✅ (filtrés) | ✅ (tous) | ✅ (tous) | ✅ (tous) |
| `POST /kb/personnes` | ❌ | ❌ | ✅ | ✅ |
| `GET /kb/rendezvous` | ✅ (filtrés) | ✅ (tous) | ✅ (tous) | ✅ (tous) |
| `POST /kb/rendezvous` | ✅ | ❌ | ✅ | ✅ |
| `GET /kb/conversations` | ✅ (filtrées) | ✅ (toutes) | ✅ (toutes) | ✅ (toutes) |
| `GET /kb/filières` | ✅ | ✅ | ✅ | ✅ |
| `POST /kb/filières` | ❌ | ❌ | ✅ | ✅ |
| `GET /agents` | ✅ | ✅ | ✅ | ✅ |
| `POST /agents` | ❌ | ❌ | ❌ | ✅ |
| `GET /notifications/recent` | ✅ | ❌ | ✅ | ✅ |
| `GET /dashboard/agent/stats` | ✅ | ❌ | ✅ | ✅ |
| `GET /dashboard/manager/stats` | ❌ | ❌ | ✅ | ✅ |
| `GET /dashboard/viewer/stats` | ❌ | ✅ | ✅ | ✅ |

---

## 📝 Notes Importantes

1. **Filtrage automatique pour les agents**:
   - Personnes: Uniquement ceux avec qui l'agent a des RDV
   - Rendez-vous: Uniquement ceux assignés à l'agent
   - Conversations: Uniquement celles assignées à l'agent

2. **Pas de filtrage pour Manager/Admin**:
   - Ils voient TOUTES les données

3. **Viewer**:
   - Voit tout mais en lecture seule
   - Ne peut rien créer/modifier/supprimer

---

**Corrections terminées ! L'agent devrait maintenant pouvoir se connecter sans erreurs 403.** 🎉
