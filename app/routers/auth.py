"""
Auth Router — email sign-up, sign-in, sign-out, and /me.

POST /api/auth/signup  → register new user
POST /api/auth/signin  → authenticate existing user
POST /api/auth/signout → invalidate session  (requires token)
GET  /api/auth/me      → return current user profile (requires token)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from gotrue.errors import AuthApiError

from app.dependencies.auth import get_current_user
from app.models.auth import (
    AuthResponse,
    MessageResponse,
    SignInRequest,
    SignUpRequest,
    UserProfile,
)
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_auth_service() -> AuthService:
    return AuthService()


def _http_from_supabase(exc: AuthApiError) -> HTTPException:
    """Map common Supabase auth errors to meaningful HTTP responses."""
    msg = exc.message.lower()
    if "already registered" in msg or "already exists" in msg:
        return HTTPException(status.HTTP_409_CONFLICT, detail="Email already in use.")
    if "invalid login" in msg or "invalid credentials" in msg or "wrong password" in msg:
        return HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    if "email not confirmed" in msg:
        return HTTPException(status.HTTP_403_FORBIDDEN, detail="Please confirm your email address first.")
    if "rate limit" in msg:
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests. Slow down.")
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message)


# ---------------------------------------------------------------------------
# POST /auth/signup
# ---------------------------------------------------------------------------

@router.post(
    "/signup",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description=(
        "Create a new account with email + password. "
        "Returns a JWT access token if email confirmation is disabled, "
        "otherwise prompts the user to check their inbox."
    ),
)
def sign_up(
    body: SignUpRequest,
    svc: AuthService = Depends(_get_auth_service),
):
    try:
        return svc.sign_up(email=body.email, password=body.password)
    except AuthApiError as exc:
        raise _http_from_supabase(exc)
    except Exception as exc:
        logger.error(f"[Auth] Unexpected sign-up error: {exc}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sign-up failed. Try again.")


# ---------------------------------------------------------------------------
# POST /auth/signin
# ---------------------------------------------------------------------------

@router.post(
    "/signin",
    response_model=AuthResponse,
    summary="Sign in with email & password",
    description="Authenticate and receive a JWT access token + refresh token.",
)
def sign_in(
    body: SignInRequest,
    svc: AuthService = Depends(_get_auth_service),
):
    try:
        return svc.sign_in(email=body.email, password=body.password)
    except AuthApiError as exc:
        raise _http_from_supabase(exc)
    except Exception as exc:
        logger.error(f"[Auth] Unexpected sign-in error: {exc}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sign-in failed. Try again.")


# ---------------------------------------------------------------------------
# POST /auth/signout  (protected)
# ---------------------------------------------------------------------------

@router.post(
    "/signout",
    response_model=MessageResponse,
    summary="Sign out (invalidate session)",
    description="Invalidates the refresh token server-side. Pass the access token in the Authorization header.",
)
def sign_out(
    _: UserProfile = Depends(get_current_user),
    svc: AuthService = Depends(_get_auth_service),
):
    try:
        svc.sign_out()
        return MessageResponse(message="Signed out successfully.")
    except AuthApiError as exc:
        raise _http_from_supabase(exc)


# ---------------------------------------------------------------------------
# GET /auth/me  (protected)
# ---------------------------------------------------------------------------

@router.get(
    "/me",
    response_model=UserProfile,
    summary="Get current user profile",
    description="Returns the profile of the currently authenticated user.",
)
def get_me(current_user: UserProfile = Depends(get_current_user)):
    return current_user
