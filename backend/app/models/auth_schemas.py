"""Authentication request/response schemas."""

from pydantic import BaseModel, Field, field_validator
from typing import Optional
import re

ALLOWED_ROLES = ["PASSENGER", "RAILWAY_STAFF"]


def normalize_phone(phone: str) -> str:
    """Normalize phone number to just digits."""
    return re.sub(r'\D', '', phone)


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="User's full name")
    phone: str = Field(..., description="User's phone number (10 digits)")
    password: str = Field(..., min_length=6, max_length=128, description="Password")
    confirm_password: str = Field(..., min_length=6, max_length=128, description="Confirm password")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        normalized = normalize_phone(v)
        if len(normalized) < 10 or len(normalized) > 15:
            raise ValueError("Phone number must be between 10 and 15 digits")
        return normalized

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        password = info.data.get("password")
        if password and v != password:
            raise ValueError("Passwords do not match")
        return v


class LoginRequest(BaseModel):
    phone: str = Field(..., description="User's phone number")
    password: str = Field(..., description="Password")
    
    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return normalize_phone(v)


class UserResponse(BaseModel):
    id: int
    name: str
    phone: str
    role: str
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    message: str
    user: UserResponse
    access_token: Optional[str] = None


class MessageResponse(BaseModel):
    message: str
