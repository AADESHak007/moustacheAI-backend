"""
FastAPI dependency — resolves the current authenticated user from a Bearer JWT.

Usage in any router:
    from app.dependencies.auth import get_current_user
    from app.models.auth import UserProfile

    @router.get("/me")
    async def me(user: UserProfile = Depends(get_current_user)):
        return user
"""

import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import create_client

from app.config import get_settings
from app.models.auth import UserProfile

logger = logging.getLogger(__name__)
settings = get_settings()

# FastAPI security scheme — extracts "Authorization: Bearer <token>" header
_bearer = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> UserProfile:
    """
    Validate the JWT sent in the Authorization header against Supabase Auth.

    Returns:
        UserProfile of the authenticated user.

    Raises:
        401 — missing/expired/invalid token.
    """
    token = credentials.credentials

    try:
        # Use the ANON client to verify the user's JWT with Supabase Auth
        client = create_client(settings.supabase_url, settings.supabase_anon_key)
        resp = client.auth.get_user(token)
    except Exception as exc:
        logger.warning(f"[Auth] Token validation error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not resp or not resp.user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = resp.user
    return UserProfile(
        id=str(user.id),
        email=user.email,
        created_at=user.created_at,
    )
