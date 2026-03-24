"""Schémas Pydantic pour validation et sérialisation des données."""
from typing import Optional, Generic, TypeVar, List
from pydantic import BaseModel, Field, validator, EmailStr
from typing import Optional
from datetime import datetime
import re
from uuid import UUID


# Schémas de pagination
T = TypeVar('T')


class PaginatedResponse(BaseModel, Generic[T]):
    """Réponse paginée générique."""
    items: List[T]
    total: int
    limit: int
    offset: int
    has_more: bool
    
    @classmethod
    def create(cls, items: List[T], total: int, limit: int, offset: int):
        """Crée une réponse paginée."""
        return cls(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items)) < total
        )


# Schémas Agent
class AgentCreate(BaseModel):
    """Schéma pour créer un agent."""
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    phone: Optional[str] = None
    password: str = Field(..., min_length=8)
    specialite: Optional[str] = Field(None, max_length=100)
    max_rdv_par_jour: int = Field(8, ge=1, le=20)
    secteur_geographique: Optional[str] = Field(None, max_length=500)
    
    @validator('phone')
    def validate_phone(cls, v):
        if v and not re.match(r'^\+?[0-9]{10,15}$', v.replace(' ', '').replace('-', '')):
            raise ValueError('Format de téléphone invalide (10-15 chiffres)')
        return v


class AgentUpdate(BaseModel):
    """Schéma pour mettre à jour un agent."""
    first_name: Optional[str] = Field(None, min_length=1, max_length=120)
    last_name: Optional[str] = Field(None, min_length=1, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    specialite: Optional[str] = Field(None, max_length=100)
    disponible: Optional[bool] = None
    max_rdv_par_jour: Optional[int] = Field(None, ge=1, le=20)
    secteur_geographique: Optional[str] = Field(None, max_length=500)
    
    @validator('phone')
    def validate_phone(cls, v):
        if v and not re.match(r'^\+?[0-9]{10,15}$', v.replace(' ', '').replace('-', '')):
            raise ValueError('Format de téléphone invalide (10-15 chiffres)')
        return v


class AgentResponse(BaseModel):
    """Schéma de réponse pour un agent."""
    id: UUID
    user_id: int
    first_name: str
    last_name: str
    email: str
    phone: Optional[str]
    specialite: Optional[str]
    disponible: bool
    max_rdv_par_jour: int
    secteur_geographique: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# Schémas Calendar
class CalendarCreate(BaseModel):
    """Schéma pour créer un calendrier."""
    name: str
    owner: Optional[str] = None
    timezone: Optional[str] = "UTC"


class CalendarResponse(BaseModel):
    """Schéma de réponse pour un calendrier."""
    id: str
    name: str
    owner: Optional[str]
    timezone: Optional[str]
    is_active: bool
    created_at: datetime


class EventCreate(BaseModel):
    """Schéma pour créer un événement."""
    calendar_id: UUID
    title: str
    start_at: datetime
    end_at: datetime
    attendees: Optional[str] = None
    description: Optional[str] = None
    status: str = "confirmed"
    resource_key: Optional[str] = None


class EventResponse(BaseModel):
    """Schéma de réponse pour un événement."""
    id: str
    calendar_id: str
    title: str
    start_at: datetime
    end_at: datetime
    attendees: Optional[str]
    description: Optional[str]
    status: str
    resource_key: Optional[str]
    created_at: datetime


# Schémas Document
class DocumentCreate(BaseModel):
    """Schéma pour créer un document."""
    title: str
    content: str
    tags: Optional[str] = None


class DocumentResponse(BaseModel):
    """Schéma de réponse pour un document."""
    id: str
    title: str
    content: str
    tags: Optional[str]
    created_at: datetime
