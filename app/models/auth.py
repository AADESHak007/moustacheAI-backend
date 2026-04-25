"""
Pydantic models for Auth-related request/response schemas.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, UUID4


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class SignUpRequest(BaseModel):
    email: EmailStr
    password: str

    model_config = {"json_schema_extra": {"example": {"email": "user@example.com", "password": "supersecret123"}}}


class SignInRequest(BaseModel):
    email: EmailStr
    password: str

    model_config = {"json_schema_extra": {"example": {"email": "user@example.com", "password": "supersecret123"}}}


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class UserProfile(BaseModel):
    """Public user profile returned after auth."""
    id: str
    email: str
    created_at: Optional[datetime] = None


class AuthResponse(BaseModel):
    """Returned on successful sign-up or sign-in."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int           # seconds
    refresh_token: str
    user: UserProfile


class MessageResponse(BaseModel):
    message: str
