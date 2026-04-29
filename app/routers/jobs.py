"""
Jobs Router — Auth-Protected Version

POST /api/jobs         → create a new mustache job  (JWT required)
GET  /api/jobs/{id}    → poll job status             (JWT required)
GET  /api/jobs         → list current user's jobs    (JWT required)
"""

import base64
import logging
import time
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings
from app.dependencies.auth import get_current_user
from app.models.auth import UserProfile
from app.models.job import JobResponse, JobStatus, JobStatusResponse
from app.services.ai_pipeline import AIProcessingError, overlay_mustache
from app.services.credits_service import CreditsService
from app.services.image_service import ImageService
from app.utils.validators import validate_image

logger   = logging.getLogger(__name__)
settings = get_settings()
router   = APIRouter()
limiter  = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# In-Memory Store for Job Data
# Format: { job_id: { "status": JobStatus, "output_url": str, "error": str,
#                     "user_id": str, "ai_image_id": str } }
# ---------------------------------------------------------------------------
_jobs_db: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def _process_job(
    job_id:         str,
    style_id:       str,
    image_bytes:    bytes,
    user_id:        str,
    original_id:    str,
    ai_image_id:    str,
) -> None:
    """Run the Gemini AI pipeline and persist the result to Supabase Storage."""
    svc     = ImageService()
    credits = CreditsService()

    try:
        _jobs_db[job_id]["status"] = JobStatus.PROCESSING
        await svc.mark_ai_processing(ai_image_id)
        logger.info(f"⏳ Job {job_id} → PROCESSING")

        # --- Gemini AI --------------------------------------------------------
        result_bytes = overlay_mustache(image_bytes, style_id=style_id)

        # --- Persist result to Supabase Storage & DB -------------------------
        output_url = await svc.save_ai_result(
            user_id=user_id,
            ai_image_id=ai_image_id,
            result_bytes=result_bytes,
        )

        # --- Also embed as base64 so the mobile app can render immediately ---
        b64_str = base64.b64encode(result_bytes).decode("utf-8")
        data_uri = f"data:image/jpeg;base64,{b64_str}"

        _jobs_db[job_id].update({"status": JobStatus.DONE, "output_url": data_uri})
        logger.info(f"✅ Job {job_id} → DONE (storage: {output_url})")

    except AIProcessingError as exc:
        logger.warning(f"[AI] Job {job_id} failed: {exc}")
        _jobs_db[job_id].update({"status": JobStatus.FAILED, "error": str(exc)})
        await svc.mark_ai_failed(ai_image_id, str(exc))
        # Credit was charged at job-creation time — give it back on failure.
        credits.refund(user_id, amount=1)

    except Exception as exc:
        logger.error(f"[Job] Unexpected error for {job_id}: {exc}", exc_info=True)
        msg = "Internal AI error. Please try again."
        _jobs_db[job_id].update({"status": JobStatus.FAILED, "error": msg})
        await svc.mark_ai_failed(ai_image_id, msg)
        credits.refund(user_id, amount=1)


# ---------------------------------------------------------------------------
# POST /api/jobs  — create a new job  (🔒 auth required)
# ---------------------------------------------------------------------------

@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a mustache job",
    description="Upload a selfie and choose a style. Returns a job_id to poll.",
)
@limiter.limit(settings.rate_limit)
async def create_job(
    request:          Request,
    background_tasks: BackgroundTasks,
    image:    UploadFile = File(..., description="Selfie image (JPEG/PNG, max 5 MB)"),
    style_id: str        = Form(..., description="Mustache style key, e.g. 'handlebar'"),
    current_user: UserProfile = Depends(get_current_user),
):
    """
    1. Validates the uploaded image.
    2. Persists the original image to Supabase Storage (uploads/{user_id}/…).
    3. Creates an ai_generated_images record with status='pending'.
    4. Enqueues the Gemini AI background task.
    5. Returns the job_id immediately (HTTP 202).
    """
    image_bytes = await validate_image(image)
    job_id = str(uuid.uuid4())

    svc     = ImageService()
    credits = CreditsService()

    # --- Credit gate -----------------------------------------------------
    # 1 credit per generation. Charge up-front; refund on AI failure
    # (see `_process_job`).
    if not credits.try_spend(current_user.id, amount=1):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="You're out of credits. Pick a plan to keep generating.",
        )

    # --- Persist original image & create AI record -----------------------
    try:
        original_id, ai_image_id = await svc.create_job_records(
            user_id=current_user.id,
            image_bytes=image_bytes,
            style_id=style_id,
            mime_type=image.content_type or "image/jpeg",
        )
    except Exception as exc:
        logger.error(f"[Jobs] Failed to persist image records: {exc}", exc_info=True)
        # Storage failure isn't the user's fault — return the credit.
        credits.refund(current_user.id, amount=1)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not save image. Please try again.",
        )

    # --- In-memory job record --------------------------------------------
    _jobs_db[job_id] = {
        "status":       JobStatus.PENDING,
        "output_url":   None,
        "error":        None,
        "user_id":      current_user.id,
        "original_id":  original_id,
        "ai_image_id":  ai_image_id,
        "created_at":   time.time(),
    }

    background_tasks.add_task(
        _process_job,
        job_id, style_id, image_bytes,
        current_user.id, original_id, ai_image_id,
    )

    logger.info(f"🚀 Job {job_id} created for user {current_user.id}")
    return JobResponse(job_id=job_id, status=JobStatus.PENDING)


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}  — poll status  (🔒 auth required)
# ---------------------------------------------------------------------------

@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll job status",
    description="Poll every 2 seconds. output_url is populated when status='done'.",
)
async def get_job_status(
    job_id: str,
    current_user: UserProfile = Depends(get_current_user),
):
    job = _jobs_db.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    # Ownership check — users can only poll their own jobs
    if job.get("user_id") and job["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied.")

    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        output_url=job["output_url"],
        error=job["error"],
    )


# ---------------------------------------------------------------------------
# GET /api/jobs  — list user's jobs  (🔒 auth required)
# ---------------------------------------------------------------------------

@router.get(
    "/jobs",
    summary="List my jobs",
    description="Returns in-memory jobs for the currently authenticated user.",
)
async def list_jobs(
    current_user: UserProfile = Depends(get_current_user),
):
    user_jobs = [
        {
            "job_id":     jid,
            "status":     data["status"],
            "created_at": data["created_at"],
        }
        for jid, data in _jobs_db.items()
        if data.get("user_id") == current_user.id
    ]
    # Most recent first
    user_jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return {"jobs": user_jobs, "total": len(user_jobs)}
