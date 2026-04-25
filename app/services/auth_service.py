"""
Auth service — wraps Supabase Auth email sign-up / sign-in.

Uses the ANON key for auth flows (Supabase Auth is designed for this).
The service-role key is ONLY used for storage / DB admin operations.
"""

import logging
from gotrue.errors import AuthApiError
from supabase import Client, create_client

from app.config import get_settings
from app.models.auth import AuthResponse, UserProfile

logger = logging.getLogger(__name__)
settings = get_settings()


def _get_auth_client() -> Client:
    """Supabase client initialised with the ANON key for Auth operations."""
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def _build_auth_response(session, user) -> AuthResponse:
    """Map a Supabase auth session + user into our AuthResponse schema."""
    return AuthResponse(
        access_token=session.access_token,
        token_type="bearer",
        expires_in=session.expires_in,
        refresh_token=session.refresh_token,
        user=UserProfile(
            id=str(user.id),
            email=user.email,
            created_at=user.created_at,
        ),
    )


class AuthService:
    """
    Handles email-based sign-up and sign-in via Supabase Auth.

    One instance per request — no internal state stored.
    """

    def __init__(self) -> None:
        self.client: Client = _get_auth_client()

    # ------------------------------------------------------------------
    # Sign Up
    # ------------------------------------------------------------------
    def sign_up(self, email: str, password: str) -> AuthResponse:
        """
        Register a new user with email + password.

        Supabase automatically:
          - Creates a row in auth.users
          - Fires the on_auth_user_created trigger → inserts into public.users

        Raises:
            AuthApiError: e.g. email already in use, weak password.
        """
        logger.info(f"[Auth] Sign-up attempt: {email}")
        try:
            resp = self.client.auth.sign_up({"email": email, "password": password})
        except AuthApiError as exc:
            logger.warning(f"[Auth] Sign-up failed ({email}): {exc.message}")
            raise

        if not resp.session or not resp.user:
            # Supabase returns session=None when email confirmation is required.
            # Return a partial response so the client knows to check their inbox.
            logger.info(f"[Auth] Sign-up success (email confirmation pending): {email}")
            return AuthResponse(
                access_token="",
                token_type="bearer",
                expires_in=0,
                refresh_token="",
                user=UserProfile(id=str(resp.user.id), email=resp.user.email),
            )

        logger.info(f"[Auth] Sign-up success: {email} ({resp.user.id})")
        return _build_auth_response(resp.session, resp.user)

    # ------------------------------------------------------------------
    # Sign In
    # ------------------------------------------------------------------
    def sign_in(self, email: str, password: str) -> AuthResponse:
        """
        Authenticate an existing user with email + password.

        Returns a short-lived JWT (access_token) and a refresh_token.

        Raises:
            AuthApiError: Invalid credentials or user not found.
        """
        logger.info(f"[Auth] Sign-in attempt: {email}")
        try:
            resp = self.client.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
        except AuthApiError as exc:
            logger.warning(f"[Auth] Sign-in failed ({email}): {exc.message}")
            raise

        logger.info(f"[Auth] Sign-in success: {email} ({resp.user.id})")
        return _build_auth_response(resp.session, resp.user)

    # ------------------------------------------------------------------
    # Sign Out  (optional — invalidates the refresh token server-side)
    # ------------------------------------------------------------------
    def sign_out(self) -> None:
        """Sign out the currently authenticated session."""
        try:
            self.client.auth.sign_out()
            logger.info("[Auth] Sign-out successful")
        except AuthApiError as exc:
            logger.warning(f"[Auth] Sign-out error: {exc.message}")
            raise
