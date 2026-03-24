# Refactoring: Agents, Managers et Viewers

## Problèmes résolus

### 1. **Colonnes dupliquées entre `users` et `agents`**
**Avant:**
- Table `users`: `email`, `phone`, `first_name`, `last_name`
- Table `agents`: `nom`, `email`, `telephone` (redondant!)

**Après:**
- Table `users`: contient toutes les informations personnelles
- Table `agents`: contient uniquement les données métier (spécialité, disponibilité, secteur)
- Relation: `agents.user_id` → `users.id` (CASCADE)

### 2. **Logique de création d'agents cassée**
**Avant:**
- Création d'agent n'enregistrait que dans la table `agents`
- Pas de création automatique dans `users`
- Résultat: aucun agent trouvé car pas d'utilisateur associé

**Après:**
- `create_agent()` crée d'abord un utilisateur avec le rôle 'agent'
- Puis crée l'enregistrement agent lié
- Les deux tables sont synchronisées automatiquement

### 3. **Tables manquantes pour Managers et Viewers**
**Ajouté:**
- Table `managers`: données spécifiques (département, équipe) + `user_id`
- Table `viewers`: données spécifiques (accès limité, départements autorisés) + `user_id`

## Structure de la base de données

```
users (table principale)
├── id (PK)
├── email, first_name, last_name, phone
├── password_hash, role_id
└── mfa_enabled, avatar_url, last_login

agents (données métier)
├── id (PK)
├── user_id (FK → users.id, CASCADE, NOT NULL)
├── specialite
├── disponible
├── max_rdv_par_jour
└── secteur_geographique

managers (données métier)
├── id (PK)
├── user_id (FK → users.id, CASCADE, NOT NULL, UNIQUE)
├── departement
└── equipe

viewers (données métier)
├── id (PK)
├── user_id (FK → users.id, CASCADE, NOT NULL, UNIQUE)
├── acces_limite
└── departements_autorises
```

## Migration de la base de données

### Étape 1: Sauvegarder les données existantes

**IMPORTANT:** Avant d'appliquer la migration, sauvegardez vos agents existants!

```sql
-- Créer une table temporaire pour sauvegarder les agents
CREATE TABLE agents_backup AS SELECT * FROM agents;
```

### Étape 2: Appliquer la migration Alembic

```bash
cd /Users/dialloabdoulaye/Desktop/AgentIA
./venv/bin/alembic upgrade head
```

Cette migration va:
1. Créer les tables `managers` et `viewers`
2. Supprimer les colonnes `nom`, `email`, `telephone` de la table `agents`
3. Modifier `user_id` pour être NOT NULL avec CASCADE
4. Recréer la foreign key avec CASCADE

### Étape 3: Migrer les données existantes

Si vous aviez des agents dans l'ancienne structure, vous devez les migrer manuellement:

```sql
-- Pour chaque agent existant, créer un utilisateur et lier
-- Exemple:
INSERT INTO users (first_name, last_name, email, phone, password_hash, role_id, mfa_enabled)
VALUES ('Jean', 'Dupont', 'jean.dupont@nexallion.com', '+221 77 123 4567', 
        'hash_temporaire', (SELECT id FROM roles WHERE name = 'agent'), false);

-- Puis mettre à jour l'agent avec le user_id
UPDATE agents SET user_id = (SELECT id FROM users WHERE email = 'jean.dupont@nexallion.com')
WHERE id = 'id_de_lagent';
```

**OU** utilisez le script SQL fourni ci-dessous pour migrer automatiquement.

## Utilisation de la nouvelle API

### Créer un agent

**Avant:**
```json
POST /agents
{
  "nom": "Jean Dupont",
  "email": "jean.dupont@example.com",
  "telephone": "+221 77 123 4567",
  "specialite": "Admissions Licence",
  "max_rdv_par_jour": 8
}
```

**Après:**
```json
POST /agents
{
  "first_name": "Jean",
  "last_name": "Dupont",
  "email": "jean.dupont@example.com",
  "phone": "+221 77 123 4567",
  "password": "MotDePasseSecurise123!",
  "specialite": "Admissions Licence",
  "max_rdv_par_jour": 8,
  "secteur_geographique": "Dakar, Plateau"
}
```

### Réponse API

**Avant:**
```json
{
  "id": "uuid",
  "nom": "Jean Dupont",
  "email": "jean.dupont@example.com",
  "telephone": "+221 77 123 4567",
  "specialite": "Admissions Licence",
  "disponible": true,
  "max_rdv_par_jour": 8
}
```

**Après:**
```json
{
  "id": "uuid",
  "user_id": 123,
  "first_name": "Jean",
  "last_name": "Dupont",
  "email": "jean.dupont@example.com",
  "phone": "+221 77 123 4567",
  "specialite": "Admissions Licence",
  "disponible": true,
  "max_rdv_par_jour": 8,
  "secteur_geographique": "Dakar, Plateau",
  "created_at": "2026-01-07T17:00:00Z",
  "updated_at": "2026-01-07T17:00:00Z"
}
```

### Créer un manager

```json
POST /managers
{
  "first_name": "Marie",
  "last_name": "Martin",
  "email": "marie.martin@example.com",
  "phone": "+221 77 234 5678",
  "password": "MotDePasseSecurise123!",
  "departement": "Commercial",
  "equipe": "Équipe Dakar"
}
```

### Créer un viewer

```json
POST /viewers
{
  "first_name": "Pierre",
  "last_name": "Sow",
  "email": "pierre.sow@example.com",
  "phone": "+221 77 345 6789",
  "password": "MotDePasseSecurise123!",
  "acces_limite": true,
  "departements_autorises": "Commercial,Support"
}
```

## Services disponibles

### Agents
- `app/services/agents.py`
  - `create_agent()` - Crée user + agent
  - `get_agent()` - Récupère un agent
  - `list_agents()` - Liste les agents
  - `update_agent()` - Met à jour user + agent
  - `delete_agent()` - Supprime l'agent (cascade sur user)

### Managers
- `app/services/managers.py`
  - `create_manager()` - Crée user + manager
  - `get_manager()` - Récupère un manager
  - `get_manager_by_user_id()` - Récupère par user_id
  - `list_managers()` - Liste les managers
  - `update_manager()` - Met à jour user + manager
  - `delete_manager()` - Supprime le manager

### Viewers
- `app/services/viewers.py`
  - `create_viewer()` - Crée user + viewer
  - `get_viewer()` - Récupère un viewer
  - `get_viewer_by_user_id()` - Récupère par user_id
  - `list_viewers()` - Liste les viewers
  - `update_viewer()` - Met à jour user + viewer
  - `delete_viewer()` - Supprime le viewer

## Script de migration des données

```sql
-- Script pour migrer les agents existants vers la nouvelle structure
-- À exécuter APRÈS avoir appliqué la migration Alembic

DO $$
DECLARE
    agent_record RECORD;
    new_user_id BIGINT;
    agent_role_id BIGINT;
BEGIN
    -- Récupérer l'ID du rôle 'agent'
    SELECT id INTO agent_role_id FROM roles WHERE name = 'agent';
    
    IF agent_role_id IS NULL THEN
        RAISE EXCEPTION 'Le rôle "agent" n''existe pas. Créez-le d''abord.';
    END IF;
    
    -- Pour chaque agent dans la sauvegarde
    FOR agent_record IN SELECT * FROM agents_backup LOOP
        -- Créer un utilisateur si l'email n'existe pas déjà
        IF NOT EXISTS (SELECT 1 FROM users WHERE email = agent_record.email) THEN
            INSERT INTO users (first_name, last_name, email, phone, password_hash, role_id, mfa_enabled)
            VALUES (
                split_part(agent_record.nom, ' ', 1), -- Prénom (première partie du nom)
                substring(agent_record.nom from position(' ' in agent_record.nom) + 1), -- Nom (reste)
                agent_record.email,
                agent_record.telephone,
                '$2b$12$defaulthashchangeme', -- Hash temporaire - À CHANGER!
                agent_role_id,
                false
            )
            RETURNING id INTO new_user_id;
            
            -- Mettre à jour l'agent avec le user_id
            UPDATE agents 
            SET user_id = new_user_id
            WHERE id = agent_record.id;
            
            RAISE NOTICE 'Agent migré: % (user_id: %)', agent_record.nom, new_user_id;
        END IF;
    END LOOP;
END $$;

-- Nettoyer la table de sauvegarde
DROP TABLE agents_backup;
```

## Points importants

1. **Cascade DELETE**: Quand un utilisateur est supprimé, son agent/manager/viewer est automatiquement supprimé
2. **Unicité**: Un utilisateur ne peut être qu'un seul type (agent OU manager OU viewer)
3. **Rôles requis**: Les rôles 'agent', 'manager', 'viewer' doivent exister dans la table `roles`
4. **Mots de passe**: Lors de la création, un mot de passe est requis (min 8 caractères)
5. **Validation**: Les emails sont validés, les téléphones doivent avoir 10-15 chiffres

## Prochaines étapes

1. ✅ Appliquer la migration Alembic
2. ✅ Migrer les données existantes
3. ⏳ Mettre à jour le frontend pour utiliser les nouveaux champs
4. ⏳ Créer les routers pour managers et viewers
5. ⏳ Tester la création/modification/suppression

## Rollback (si nécessaire)

Si vous devez revenir en arrière:

```bash
./venv/bin/alembic downgrade -1
```

Cela restaurera l'ancienne structure avec les colonnes dupliquées.
