"""
Application settings loaded from environment variables.
Uses Pydantic Settings for type-safe config with .env support.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ---- Application ----
    app_name: str = "AI Mustache Generator API"
    app_version: str = "1.0.0"
    debug: bool = False

    # ---- Supabase ----
    supabase_url: str
    supabase_service_key: str   # Server-side only. Full DB + Storage access.
    supabase_anon_key: str

    # ---- Storage Buckets ----
    uploads_bucket: str = "uploads"
    results_bucket: str = "results"
    assets_bucket: str = "assets"

    # ---- File Validation ----
    max_file_size_mb: int = 5
    max_image_dimension: int = 4000
    allowed_content_types: list[str] = ["image/jpeg", "image/png"]

    # ---- Rate Limiting ----
    rate_limit: str = "5/minute"

    # ---- CORS ----
    cors_origins: list[str] = ["*"]

    # ---- Signed URL Expiry ----
    signed_url_expiry_seconds: int = 3600  # 1 hour

    # ---- Gemini AI ----
    gemini_api_key: str = ""


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton. Call this everywhere instead of instantiating Settings directly."""
    return Settings()
