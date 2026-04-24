"""
AI Processing Pipeline — Gemini Image Generation
================================================
Workflow:
  1. Map style_id → descriptive AI prompt
  2. Encode user image as base64 for Gemini
  3. Call Gemini with the image + prompt to generate a new image
  4. Return the generated image as JPEG bytes
"""

import io
import base64
import logging

from PIL import Image
import google.generativeai as genai

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Style ID → AI Prompt Mapping
# ---------------------------------------------------------------------------
STYLE_PROMPTS: dict[str, str] = {
    "chevron":   "Add a thick, full chevron mustache above this person's upper lip. The mustache should look photorealistic, match the person's natural hair color, and blend naturally with the skin.",
    "handlebar": "Add a classic handlebar mustache to this person's face. The ends should curl upward stylishly. Make it photorealistic, matching their hair color.",
    "fu_manchu": "Add a Fu Manchu mustache to this person's face — thin strips growing from the upper lip that hang down past the jawline. Make it photorealistic.",
    "pencil":    "Add a very thin, precisely groomed pencil-thin mustache just above this person's upper lip. Make it photorealistic and neat.",
    "walrus":    "Add a thick, bushy walrus mustache to this person's face that droops down over the mouth. Make it photorealistic and match their hair color.",
    "english":   "Add a narrow English-style mustache to this person's face with long, straight whiskers extending horizontally outward. Make it photorealistic.",
}

DEFAULT_PROMPT = "Add a natural-looking mustache to this person's upper lip. Make it photorealistic and match their hair color."


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------
class AIProcessingError(Exception):
    """Raised when the AI pipeline cannot process the image."""
    pass


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------
def generate_mustache(image_bytes: bytes, style_id: str) -> bytes:
    """
    Sends the user's photo and a style prompt to Gemini to generate
    a mustached version of the photo.

    Args:
        image_bytes: Raw bytes of the user's selfie (JPEG or PNG).
        style_id:    One of the defined style IDs (e.g. 'handlebar').

    Returns:
        JPEG bytes of the AI-generated output image.

    Raises:
        AIProcessingError: On API failure or no image returned.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        raise AIProcessingError("GEMINI_API_KEY is not configured on the server.")

    # Step 1 — Configure Gemini
    genai.configure(api_key=settings.gemini_api_key)

    # Step 2 — Select prompt
    prompt = STYLE_PROMPTS.get(style_id, DEFAULT_PROMPT)
    full_prompt = (
        f"{prompt} "
        "Keep the person's face, background, lighting, and all other features "
        "exactly the same. Only add the mustache. Output a photorealistic image."
    )
    logger.info(f"[Gemini] style={style_id!r} | prompt={full_prompt[:80]}...")

    # Step 3 — Prepare image for Gemini (inline data)
    img_pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Resize to max 1024px on the longest side (Gemini works best with this)
    max_dim = 1024
    if max(img_pil.width, img_pil.height) > max_dim:
        ratio = max_dim / max(img_pil.width, img_pil.height)
        img_pil = img_pil.resize(
            (int(img_pil.width * ratio), int(img_pil.height * ratio)),
            Image.LANCZOS,
        )

    img_buffer = io.BytesIO()
    img_pil.save(img_buffer, format="JPEG", quality=90)
    img_buffer.seek(0)
    input_b64 = base64.b64encode(img_buffer.read()).decode("utf-8")

    # Step 4 — Call Gemini (imagen-3.0-generate-002 supports image output)
    try:
        model = genai.GenerativeModel("gemini-3.1-flash-image-preview")
        response = model.generate_content(
            [
                {"mime_type": "image/jpeg", "data": input_b64},
                full_prompt,
            ],
            generation_config=genai.types.GenerationConfig(
                temperature=0.4,
                candidate_count=1,
            ),
        )
    except Exception as e:
        logger.error(f"[Gemini] API call failed: {e}")
        raise AIProcessingError(f"Gemini API error: {e}")

    # Step 5 — Extract generated image from response
    result_bytes: bytes | None = None

    try:
        # Log the full response parts for debugging
        for i, part in enumerate(response.candidates[0].content.parts):
            if hasattr(part, "inline_data") and part.inline_data:
                logger.info(f"[Gemini] Part {i} contains inline_data (size: {len(part.inline_data.data)})")
                # In some SDK versions, data is already bytes. In others, it's base64 string.
                if isinstance(part.inline_data.data, bytes):
                    result_bytes = part.inline_data.data
                else:
                    result_bytes = base64.b64decode(part.inline_data.data)
                break
            elif hasattr(part, "text") and part.text:
                logger.info(f"[Gemini] Part {i} contains text: {part.text[:100]}...")

        if result_bytes is None:
            text_resp = response.text if hasattr(response, "text") else "No image or text returned."
            logger.warning(f"[Gemini] FAILED: Model returned text instead of an image. Response: {text_resp}")
            raise AIProcessingError(f"Model returned text instead of an image: {text_resp[:100]}")

        # Step 6 — Normalise to JPEG bytes
        out_img = Image.open(io.BytesIO(result_bytes)).convert("RGB")
        out_buf = io.BytesIO()
        out_img.save(out_buf, format="JPEG", quality=85, optimize=True)
        out_buf.seek(0)

        logger.info(f"[Gemini] ✅ Image generated successfully")
        return out_buf.read()
    except Exception as e:
        if isinstance(e, AIProcessingError): raise
        logger.error(f"[Gemini] Error processing result: {e}")
        raise AIProcessingError(f"Error processing Gemini result: {str(e)}")


# ---------------------------------------------------------------------------
# Public entry point (used by jobs router)
# ---------------------------------------------------------------------------
def overlay_mustache(image_bytes: bytes, style_id: str = "handlebar") -> bytes:
    """
    Public entry point called by the jobs background task.
    Delegates to generate_mustache().
    """
    return generate_mustache(image_bytes, style_id)
