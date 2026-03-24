#!/usr/bin/env python3
"""
Script pour seed les templates d'emails professionnels dans la base de données
"""
import sys
from pathlib import Path

# Ajouter le répertoire parent au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import SessionLocal
from app.services.email_templates import get_all_professional_templates
from app.services.templates import upsert_email_template
from app.logger import get_logger

logger = get_logger(__name__)


def seed_templates():
    """Seed tous les templates d'emails professionnels"""
    db = SessionLocal()
    try:
        templates = get_all_professional_templates()
        logger.info(f"Début du seed de {len(templates)} templates d'emails...")
        
        for tpl in templates:
            try:
                result = upsert_email_template(
                    db,
                    name=tpl["name"],
                    subject_template=tpl["subject_template"],
                    html_template=tpl["html_template"],
                    text_template=tpl["text_template"]
                )
                logger.info(f"✓ Template '{tpl['name']}' créé/mis à jour (ID: {result.id})")
                print(f"✓ Template '{tpl['name']}' créé/mis à jour")
            except Exception as e:
                logger.error(f"✗ Erreur pour le template '{tpl['name']}': {e}")
                print(f"✗ Erreur pour le template '{tpl['name']}': {e}")
        
        print(f"\n {len(templates)} templates créés/mis à jour avec succès")
        logger.info(f"Seed terminé : {len(templates)} templates créés/mis à jour")
        
    except Exception as e:
        logger.error(f"Erreur lors du seed des templates: {e}")
        print(f" Erreur lors du seed des templates: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_templates()
