"""
Pydantic models for Style-related response schemas.
"""

from pydantic import BaseModel


class StyleResponse(BaseModel):
    """A single mustache style entry."""
    id:          str   # e.g. "handlebar"
    label:       str   # e.g. "Handlebar"
    preview_url: str   # Thumbnail shown in the style picker
    asset_url:   str   # Full 600x200px transparent PNG asset path


class StylesListResponse(BaseModel):
    """Response for GET /api/styles."""
    styles: list[StyleResponse]
    count:  int
