"""
Script pour initialiser la base de données depuis les modèles SQLAlchemy
"""
from app.init_db import init_db

print("Création de toutes les tables et tenant par défaut...")
init_db()
print("✅ Base initialisée avec succès!")
