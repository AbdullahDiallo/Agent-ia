"""Module de logging structuré pour l'application.

Fournit un logger configuré avec rotation de fichiers et formatage JSON structuré.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
import json
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    """Formatter qui produit des logs en JSON structuré."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Ajouter les champs extra si présents
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)
        
        # Ajouter l'exception si présente
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, ensure_ascii=False)


def setup_logger(
    name: str = "agentia",
    level: str = "INFO",
    log_dir: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> logging.Logger:
    """Configure et retourne un logger structuré.
    
    Args:
        name: Nom du logger
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Répertoire pour les fichiers de log (None = logs/)
        max_bytes: Taille max d'un fichier de log avant rotation
        backup_count: Nombre de fichiers de backup à conserver
    
    Returns:
        Logger configuré
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Éviter les handlers dupliqués
    if logger.handlers:
        return logger
    
    # Handler console (stdout) avec format simple pour dev
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_format = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # Handler fichier avec rotation et format JSON structuré
    if log_dir is None:
        log_dir = "logs"
    
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    file_handler = RotatingFileHandler(
        log_path / f"{name}.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(StructuredFormatter())
    logger.addHandler(file_handler)
    
    # Handler pour les erreurs critiques (fichier séparé)
    error_handler = RotatingFileHandler(
        log_path / f"{name}_errors.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(StructuredFormatter())
    logger.addHandler(error_handler)
    
    return logger


# Logger global de l'application
app_logger = setup_logger()


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Retourne un logger pour un module spécifique.
    
    Args:
        name: Nom du module (utilise __name__ généralement)
    
    Returns:
        Logger configuré
    """
    if name:
        return logging.getLogger(f"agentia.{name}")
    return app_logger


def log_with_context(logger: logging.Logger, level: str, message: str, **kwargs):
    """Log un message avec contexte additionnel.
    
    Args:
        logger: Logger à utiliser
        level: Niveau de log (debug, info, warning, error, critical)
        message: Message à logger
        **kwargs: Champs additionnels à inclure dans le log
    """
    log_method = getattr(logger, level.lower(), logger.info)
    
    # Créer un LogRecord avec extra_fields
    extra = {"extra_fields": kwargs}
    log_method(message, extra=extra)
