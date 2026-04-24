import io
import logging
from typing import Optional, List
from functools import lru_cache

from google import genai
from google.genai import types
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings, SettingsConfigDict
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from PIL import Image

# ---------------------------------------------------------------------------
# 1. CONFIGURATION
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")
    app_name: str = "AI Mustache Generator API"
    app_version: str = "1.0.0"
    gemini_api_key: str
    rate_limit: str = "5/minute"
    cors_origins: list[str] = ["*"]

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Initialize Gemini GenAI Client
client = genai.Client(api_key=settings.gemini_api_key)

# ---------------------------------------------------------------------------
# 2. MOUSTACHE PROMPTS
# ---------------------------------------------------------------------------
MUSTACHE_PROMPTS = {
    "chevron": "A precise close-up photograph focusing on the lower face. The key feature is a full-density, dark brown Chevron mustache, with a substantial, heavy classic mass, neatly trimmed and groomed edges. The mustache covers the entire upper lip with sharp, clean corners extending to the mouth corners. It is integrated seamlessly with light, matching dark brown stubble on the chin and jawline. The skin and facial details are realistic and clear.",
    "pencil": "Modify the facial hair to a classic, ultra-thin Pencil Mustache. The mustache must be a sharp, singular, narrow line of dark hair that follows the natural curve of the upper lip with surgical precision. Ensure a clean-shaven gap between the mustache and the top of the lip. The edges must be perfectly crisp and well-defined, tapering off neatly at the corners of the mouth. The rest of the face should be clean-shaven with no stubble.",
    "light_natural": "This must include natural, variably spaced dark hairs creating a subtle, translucent patchiness that allows the underlying skin texture to show through. The mustache edges must be soft and unmanicured, not sharp, and seamlessly integrated with light, sparse 1-2 day stubble across the cheeks and chin. Preserve all other facial features of the target person.",
    "walrus": "A face modification is applied to the provided target face, adding a dominant, extremely full, and dense walrus-style mustache. This heavy mass of rugged, dense hair must completely cover the upper lip with hairs hanging visibly over the lip line. All other facial features, eyes, nose, skin texture, and minimal stubble of the target person must remain unchanged.",
    "horseshoe": "A photorealistic portrait modification of the target user face. Completely replace only the existing mustache with the specific, dark, dense, and well-groomed Horseshoe mustache style. This must include a full upper-lip mustache connected to two well-defined, parallel vertical beard segments extending from the mouth corners to the jawline, creating a clean 'U' shape. Render with realistic studio lighting.",
    "imperial": "A photorealistic portrait modification of the target user face. Completely replace only the existing mustache with the specific, full-bodied, dense, reddish-brown Imperial mustache style. This new mustache must extend horizontally and feature gracefully and distinctly curled upward ends. Render with high-definition detail, ensuring the new mustache is integrated naturally into the skin.",
    "anchor": "The key modification is the removal of the original moustache, replaced by a meticulously styled, crisp, defined, and symmetric pencil moustache that sits neatly above his lip. This new thin pencil moustache is paired perfectly with his existing lower chin beard/goatee combo, creating a perfect, clean 'Anchor' beard style. High detail, sharp focus on the new symmetric facial hair.",
    "k_style": "A high-resolution, black and white close-up portrait modification. The thick, dark facial hair on his upper lip and chin has been lightened and thinned dramatically. The new style is a K-style moustache and soul patch characterized by ultra-light realism. The edges of the hair must be soft and diffused, appearing to be blended flawlessly into the skin. Sparse and light-colored hair."
}

# ---------------------------------------------------------------------------
# 3. GENERATION LOGIC
# ---------------------------------------------------------------------------
async def generate_mustache_direct(input_bytes: bytes, style_id: str) -> bytes:
    prompt = MUSTACHE_PROMPTS.get(style_id.lower())
    if not prompt:
        raise HTTPException(status_code=400, detail=f"Invalid mustache style: {style_id}")

    try:
        model_name = 'gemini-3.1-flash-image-preview'
        
        # If using a Gemini model, we use generate_content
        # We provide the image and the prompt
        img_part = types.Part.from_bytes(data=input_bytes, mime_type='image/png')
        
        response = client.models.generate_content(
            model=model_name,
            contents=[
                "Apply the following modification to this person's face. Return only the final image.",
                img_part,
                prompt
            ]
        )
        
        # Check for image in response parts
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
            # Some versions might return a file or blob
            if hasattr(part, 'image'):
                buf = io.BytesIO()
                part.image.save(buf, format="JPEG")
                return buf.getvalue()

        raise Exception("No image returned in Gemini response.")

    except Exception as e:
        logger.error(f"Generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"AI Generation failed: {str(e)}")

# ---------------------------------------------------------------------------
# 4. API ROUTES
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])
app = FastAPI(title=settings.app_name, version=settings.app_version)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
async def health(): return {"status": "healthy"}

@app.post("/api/generate")
async def generate_mustache(
    image: UploadFile = File(...), 
    style_id: str = Form(...)
):
    """
    Synchronous Generation: Upload image + Style ID -> Get JPEG directly.
    """
    logger.info(f"Generating mustache style: {style_id}")
    
    img_bytes = await image.read()
    result_bytes = await generate_mustache_direct(img_bytes, style_id)
    
    return Response(content=result_bytes, media_type="image/jpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
