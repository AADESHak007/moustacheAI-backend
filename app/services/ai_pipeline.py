"""
AI Processing Pipeline — AI Mustache Generator

Pipeline steps (strictly per system design):
  1. Load input image from bytes
  2. Run MediaPipe FaceMesh → 468 landmark coordinates
  3. Extract lip anchor points (indices 13, 61, 291) → anchor_x, anchor_y, lip_width, angle
  4. Load mustache PNG asset (transparent background, 600×200px base)
  5. Transform mustache: resize (scale = lip_width / 600) + rotate to match lip angle
  6. Alpha composite mustache onto input image using Pillow
  7. Compress to JPEG quality=85 and return bytes

References (system design §03):
  - Lip anchor indices: 0, 13, 14, 17
  - Compute: anchor_x (midpoint), anchor_y (above lip), lip_width, angle
  - Mustache base size: 600×200px
  - Output: JPEG quality=85
"""

import io
import math
import logging
from typing import Tuple

import cv2
import mediapipe as mp
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MediaPipe FaceMesh setup
# ---------------------------------------------------------------------------
_mp_face_mesh = mp.solutions.face_mesh

# ---------------------------------------------------------------------------
# Landmark indices (MediaPipe FaceMesh 468-point model)
# Per system design: "Indices 0, 13, 14, 17"
# ---------------------------------------------------------------------------
# Mouth corners — used to compute lip_width and rotation angle
_LIP_LEFT_IDX  = 61    # Left mouth corner
_LIP_RIGHT_IDX = 291   # Right mouth corner

# Upper lip references for vertical anchor placement
_UPPER_LIP_IDX = 13    # Upper lip center (per system design)
_NOSE_TIP_IDX  = 4     # Nose tip — used to verify face orientation

# Mustache asset base width (all assets are 600×200px)
_MUSTACHE_BASE_WIDTH = 600


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------
class AIProcessingError(Exception):
    """Raised when the AI pipeline cannot process the image (e.g., no face detected)."""
    pass


# ---------------------------------------------------------------------------
# Step 2 + 3: Face detection and lip anchor extraction
# ---------------------------------------------------------------------------
def _extract_lip_anchors(image_rgb: np.ndarray) -> dict:
    """
    Run MediaPipe FaceMesh and compute mustache placement anchors.

    Args:
        image_rgb: H×W×3 numpy array in RGB colour space.

    Returns:
        dict with keys:
            anchor_x  (int)   – horizontal center of the mustache
            anchor_y  (int)   – vertical position (above upper lip)
            lip_width (float) – pixel distance between mouth corners
            angle     (float) – rotation angle in degrees (matches lip tilt)

    Raises:
        AIProcessingError: if no face is detected.
    """
    h, w = image_rgb.shape[:2]

    with _mp_face_mesh.FaceMesh(
        static_image_mode=True,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as face_mesh:
        results = face_mesh.process(image_rgb)

    if not results.multi_face_landmarks:
        raise AIProcessingError(
            "No face detected. Please use a clear, well-lit, front-facing photo."
        )

    landmarks = results.multi_face_landmarks[0].landmark

    def px(idx: int) -> Tuple[int, int]:
        """Convert normalised landmark to pixel coordinates."""
        lm = landmarks[idx]
        return int(lm.x * w), int(lm.y * h)

    # Pixel positions of key landmarks
    left_corner  = px(_LIP_LEFT_IDX)
    right_corner = px(_LIP_RIGHT_IDX)
    upper_lip    = px(_UPPER_LIP_IDX)

    # Lip width: Euclidean distance between mouth corners
    lip_width = math.dist(left_corner, right_corner)

    # Anchor X: horizontal midpoint of mouth corners
    anchor_x = (left_corner[0] + right_corner[0]) // 2

    # Anchor Y: place mustache above upper lip
    # Lift by ~35% of lip width so the mustache sits naturally
    lift = int(lip_width * 0.35)
    anchor_y = upper_lip[1] - lift

    # Angle: slope of the line connecting mouth corners
    dx = right_corner[0] - left_corner[0]
    dy = right_corner[1] - left_corner[1]
    angle = math.degrees(math.atan2(dy, dx))

    logger.info(
        f"Anchors — center=({anchor_x},{anchor_y}), "
        f"lip_width={lip_width:.1f}px, angle={angle:.2f}°"
    )

    return {
        "anchor_x":  anchor_x,
        "anchor_y":  anchor_y,
        "lip_width": lip_width,
        "angle":     angle,
    }


# ---------------------------------------------------------------------------
# Step 5: Mustache transformation (resize + rotate)
# ---------------------------------------------------------------------------
def _transform_mustache(
    mustache: Image.Image,
    anchors: dict,
) -> Tuple[Image.Image, Tuple[int, int]]:
    """
    Resize and rotate the mustache asset to match the detected face geometry.

    Args:
        mustache: PIL Image of the mustache PNG (RGBA, 600×200px base).
        anchors:  Dict from _extract_lip_anchors().

    Returns:
        (transformed_mustache, paste_position)
        paste_position is the top-left (x, y) for Image.paste().
    """
    lip_width = anchors["lip_width"]
    angle     = anchors["angle"]
    anchor_x  = anchors["anchor_x"]
    anchor_y  = anchors["anchor_y"]

    # Scale: system design → scale = lip_width / 600
    scale = lip_width / _MUSTACHE_BASE_WIDTH
    new_w = max(int(mustache.width  * scale), 10)
    new_h = max(int(mustache.height * scale), 10)

    # Resize
    scaled = mustache.resize((new_w, new_h), Image.LANCZOS)

    # Rotate to match lip angle (expand keeps full image visible)
    rotated = scaled.rotate(-angle, expand=True, resample=Image.BICUBIC)

    # Center paste position: anchor point should be at mustache center
    paste_x = anchor_x - rotated.width  // 2
    paste_y = anchor_y - rotated.height // 2

    return rotated, (paste_x, paste_y)


# ---------------------------------------------------------------------------
# Step 6 + 7: Alpha composite and JPEG export
# ---------------------------------------------------------------------------
def overlay_mustache(
    input_image_bytes: bytes,
    mustache_bytes: bytes,
) -> bytes:
    """
    Full AI pipeline entry point.

    Args:
        input_image_bytes: Raw bytes of the user's selfie (JPEG or PNG).
        mustache_bytes:    Raw bytes of the mustache PNG asset (transparent background).

    Returns:
        JPEG bytes of the composited output image (quality=85).

    Raises:
        AIProcessingError: If no face is detected or pipeline fails.
        Exception:         On unexpected errors (caller should catch broadly).
    """
    # --- Step 1: Load input image ---
    input_pil = Image.open(io.BytesIO(input_image_bytes)).convert("RGBA")
    input_np  = np.array(input_pil.convert("RGB"))   # MediaPipe needs RGB numpy

    # --- Step 2 + 3: Detect face → extract anchors ---
    anchors = _extract_lip_anchors(input_np)

    # --- Step 4: Load mustache asset ---
    mustache_pil = Image.open(io.BytesIO(mustache_bytes)).convert("RGBA")

    # --- Step 5: Transform mustache ---
    transformed, paste_pos = _transform_mustache(mustache_pil, anchors)

    # --- Step 6: Alpha composite ---
    # Create a transparent overlay the same size as the input
    overlay = Image.new("RGBA", input_pil.size, (0, 0, 0, 0))

    # Clamp paste position to image bounds to avoid out-of-range errors
    px = max(paste_pos[0], -transformed.width)
    py = max(paste_pos[1], -transformed.height)
    overlay.paste(transformed, (px, py), mask=transformed)

    # Merge input + mustache overlay
    composited = Image.alpha_composite(input_pil, overlay)

    # --- Step 7: Compress to JPEG quality=85 ---
    final_rgb = composited.convert("RGB")
    output_buffer = io.BytesIO()
    final_rgb.save(output_buffer, format="JPEG", quality=85, optimize=True)
    output_buffer.seek(0)

    logger.info("✅ Mustache overlay complete")
    return output_buffer.read()
