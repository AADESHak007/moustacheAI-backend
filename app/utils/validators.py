"""
Input validation for uploaded images.

Rules (strictly per system design):
  - Content type: image/jpeg or image/png only
  - File size:    ≤ 5MB
  - Dimensions:   ≤ 4000×4000px
"""

import io
import logging

from fastapi import HTTPException, UploadFile, status
from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_MAX_BYTES = settings.max_file_size_mb * 1024 * 1024  # 5 242 880 bytes


async def validate_image(file: UploadFile) -> bytes:
    """
    Validate an uploaded image file.

    Args:
        file: The uploaded file from the multipart request.

    Returns:
        Raw bytes of the image on success.

    Raises:
        HTTPException 415 – Unsupported content type.
        HTTPException 413 – File too large.
        HTTPException 422 – Invalid dimensions or unreadable image.
    """

    # 1. Content-type whitelist
    if file.content_type not in settings.allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type '{file.content_type}'. "
                "Only image/jpeg and image/png are accepted."
            ),
        )

    # 2. Read file into memory
    contents = await file.read()

    # 3. Size check
    if len(contents) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File size {len(contents) / 1024 / 1024:.2f}MB exceeds "
                f"the {settings.max_file_size_mb}MB limit."
            ),
        )

    # 4. Dimension check (open image without fully decoding pixels)
    try:
        img = Image.open(io.BytesIO(contents))
        img.verify()  # Verify it's a valid image
        img = Image.open(io.BytesIO(contents))  # Re-open after verify
        width, height = img.size

        if width > settings.max_image_dimension or height > settings.max_image_dimension:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Image dimensions {width}×{height}px exceed the maximum "
                    f"{settings.max_image_dimension}×{settings.max_image_dimension}px."
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"Image validation failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not read image file. Please upload a valid JPEG or PNG.",
        )

    logger.info(f"Image validated: {width}×{height}px, {len(contents)/1024:.1f}KB")
    return contents
