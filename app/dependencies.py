"""
Dependencies for FastAPI routes
"""
from .security import require_role, get_principal, Principal

__all__ = ['require_role', 'get_principal', 'Principal']
