# ✅ Correction finale - Chargement des agents

## 🔍 Problème identifié

Il y avait **deux routers différents** pour les agents :

1. **`/agents`** (router principal dans `app/routers/agents.py`) ✅
   - CRUD complet
   - Gestion de la relation avec users via `user_id`
   - Déjà implémenté et fonctionnel

2. **`/kb/agents`** (endpoint dupliqué dans `app/routers/kb.py`) ❌
   - Créé par erreur
   - Doublon inutile

Le frontend appelait `/kb/agents` au lieu de `/agents` !

---

## ✅ Solution appliquée

### 1. Frontend corrigé
**Fichier :** `front/dashboard/src/api/endpoints/agents.ts`

```typescript
// AVANT
const response = await api.get('/kb/agents', { params });

// APRÈS
const response = await api.get('/agents', { params });
```

### 2. Backend nettoyé
**Fichier :** `app/routers/kb.py`

- ❌ Supprimé l'endpoint dupliqué `/kb/agents`
- ❌ Retiré l'import `Agent` inutilisé

---

## 📋 Router agents principal

**Fichier :** `app/routers/agents.py`

### Endpoints disponibles

```python
GET    /agents                          # Liste tous les agents
GET    /agents/{agent_id}               # Détails d'un agent
POST   /agents                          # Créer un agent (admin)
PATCH  /agents/{agent_id}               # Modifier un agent (manager)
DELETE /agents/{agent_id}               # Supprimer un agent (admin)
POST   /agents/{agent_id}/toggle-availability  # Basculer disponibilité
GET    /agents/{agent_id}/workload      # Charge de travail
GET    /agents/{agent_id}/availability  # Créneaux disponibles
```

### Permissions
- **viewer** : Lecture (GET)
- **manager** : Lecture + Modification + Toggle disponibilité
- **admin** : Tous les droits

---

## 🔗 Relation agents-users

### Modèle
```python
class Agent(Base):
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
```

### Fonctionnement
- Un agent **peut** être lié à un utilisateur via `user_id`
- La relation est **optionnelle** (nullable=True)
- Si l'utilisateur est supprimé, `user_id` devient NULL (SET NULL)

### Gestion dans l'interface users
L'interface de gestion des users (`UsersManagementPage.tsx`) permet de :
- Créer des utilisateurs
- Leur assigner des rôles (viewer, manager, admin)
- Les agents sont créés séparément via le router `/agents`

---

## 🎯 Résultat

Maintenant le frontend appelle le **bon endpoint** `/agents` qui :
- ✅ Est correctement protégé (require_role("viewer"))
- ✅ Retourne les agents existants en base de données
- ✅ Fonctionne avec la relation user_id
- ✅ Supporte tous les filtres (disponible_only, specialite, etc.)

---

## 🧪 Test

```bash
# Vérifier que l'endpoint fonctionne
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/agents

# Devrait retourner :
{
  "items": [...agents...],
  "total": X,
  "limit": 100,
  "offset": 0,
  "has_more": false
}
```

---

## 📝 Prochaines étapes

Si les agents ne s'affichent toujours pas :
1. Vérifier qu'il y a des agents en base de données
2. Vérifier que tu es bien authentifié (token valide)
3. Vérifier les logs backend pour voir la requête

**Le problème de doublon de routers est maintenant corrigé !** ✅
