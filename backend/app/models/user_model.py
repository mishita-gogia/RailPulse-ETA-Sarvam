"""User DTO model for authentication (decoupled from SQLAlchemy)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class User:
    id: int
    name: str
    phone: str
    password_hash: str
    role: str  # PASSENGER or RAILWAY_STAFF
    created_at: Optional[datetime] = None
