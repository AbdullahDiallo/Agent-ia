-- Migration pour ajouter la colonne delivery_mode au catalogue scolaire
-- Date: 2026-01-07

-- 1) Ajouter la colonne si absente
ALTER TABLE school_tracks ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR(20);

-- 2) Initialiser les valeurs existantes
UPDATE school_tracks
SET delivery_mode = 'onsite'
WHERE delivery_mode IS NULL;

-- 3) Documenter la colonne
COMMENT ON COLUMN school_tracks.delivery_mode IS 'Modalite de formation: onsite, hybrid, online';

-- 4) Contrainte conseillee
-- ALTER TABLE school_tracks
-- ADD CONSTRAINT check_tracks_delivery_mode
-- CHECK (delivery_mode IN ('onsite', 'hybrid', 'online'));
