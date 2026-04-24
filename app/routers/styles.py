"""
Styles Router

Endpoints:
  GET /api/styles — Returns all available mustache styles.
                    Response is cached in-memory (TTL: 5 minutes).
"""

import logging
import time

from fastapi import APIRouter, HTTPException, status

from app.models.style import StyleResponse, StylesListResponse
from app.services.jobs_service import JobsService

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory cache  (data, expires_at_monotonic)
# ---------------------------------------------------------------------------
_cache: tuple[list[StyleResponse], float] | None = None
_CACHE_TTL = 300  # 5 minutes — per system design


@router.get(
    "/styles",
    response_model=StylesListResponse,
    summary="List all mustache styles",
    description=(
        "Returns the 6 available mustache styles with preview thumbnails. "
        "Cached in-memory for 5 minutes."
    ),
)
async def get_styles() -> StylesListResponse:
    global _cache

    now = time.monotonic()

    # Return cached data if still valid
    if _cache is not None:
        cached_styles, expires_at = _cache
        if now < expires_at:
            logger.debug(f"Returning {len(cached_styles)} cached styles")
            return StylesListResponse(styles=cached_styles, count=len(cached_styles))

    # Fetch fresh data from Supabase
    try:
        jobs_service = JobsService()
        raw_styles   = await jobs_service.get_styles()
    except Exception as exc:
        logger.error(f"Failed to fetch styles from Supabase: {exc}")
        # Default mock styles for local testing without DB
        raw_styles = [
            {"id": "chevron", "label": "Chevron", "preview_url": "https://placehold.co/100x100.png", "asset_url": "https://placehold.co/100x100.png"},
            {"id": "handlebar", "label": "Handlebar", "preview_url": "https://placehold.co/100x100.png", "asset_url": "https://placehold.co/100x100.png"},
            {"id": "fu_manchu", "label": "Fu Manchu", "preview_url": "https://placehold.co/100x100.png", "asset_url": "https://placehold.co/100x100.png"},
            {"id": "pencil", "label": "Pencil Thin", "preview_url": "https://placehold.co/100x100.png", "asset_url": "https://placehold.co/100x100.png"},
            {"id": "walrus", "label": "Walrus", "preview_url": "https://placehold.co/100x100.png", "asset_url": "https://placehold.co/100x100.png"},
            {"id": "english", "label": "English", "preview_url": "https://placehold.co/100x100.png", "asset_url": "https://placehold.co/100x100.png"}
        ]

    styles = [StyleResponse(**s) for s in raw_styles]

    # Cache for 5 minutes
    _cache = (styles, now + _CACHE_TTL)
    logger.info(f"Cached {len(styles)} styles (TTL={_CACHE_TTL}s)")

    return StylesListResponse(styles=styles, count=len(styles))
