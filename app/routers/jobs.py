"""
Jobs Router

Endpoints:
  POST /api/jobs        — Upload photo + select style → returns { job_id }
  GET  /api/jobs/{id}  — Poll for job status → pending | processing | done | failed
"""

import base64
import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
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
from app.models.job import JobResponse, JobStatus, JobStatusResponse
from app.services.ai_pipeline import AIProcessingError, overlay_mustache
from app.services.jobs_service import JobsService
from app.services.storage import StorageService
from app.utils.validators import validate_image

logger   = logging.getLogger(__name__)
settings = get_settings()
router   = APIRouter()
limiter  = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# In-memory results cache  (Job ID → base64 data URI)
# Single-instance safe — works on Render free tier with 1 worker.
# ---------------------------------------------------------------------------
_results_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Background Task
# ---------------------------------------------------------------------------
async def _process_job(
    job_id:       str,
    style_id:     str,
    image_bytes:  bytes,
    jobs_service: JobsService,
) -> None:
    """
    Background task:
      1. Mark job → processing
      2. Call Gemini with user photo + style prompt
      3. Convert result to base64 data URI
      4. Store in memory cache
      5. Mark job → done  (or failed on error)
    """
    try:
        # Step 1 — Mark as processing
        await jobs_service.update_job_status(job_id, JobStatus.PROCESSING)

        # Step 2 — Run Gemini AI pipeline
        result_bytes = overlay_mustache(image_bytes, style_id=style_id)

        # Step 3 — Convert to base64 data URI
        b64_str    = base64.b64encode(result_bytes).decode("utf-8")
        output_url = f"data:image/jpeg;base64,{b64_str}"

        # Step 4 — Cache result
        _results_cache[job_id] = output_url

        # Step 5 — Mark done
        await jobs_service.update_job_status(
            job_id, JobStatus.DONE, output_url=output_url
        )
        logger.info(f"✅ Job {job_id} → DONE")

    except AIProcessingError as exc:
        logger.warning(f"[AI] Job {job_id} failed: {exc}")
        await jobs_service.update_job_status(
            job_id, JobStatus.FAILED, error=str(exc)
        )
    except Exception as exc:
        logger.error(f"[Job] Unexpected error for {job_id}: {exc}", exc_info=True)
        await jobs_service.update_job_status(
            job_id, JobStatus.FAILED, error="An internal error occurred. Please try again."
        )


# ---------------------------------------------------------------------------
# POST /api/jobs
# ---------------------------------------------------------------------------
@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a new mustache job",
    description=(
        "Upload a photo and select a mustache style. "
        "Returns a job_id — poll GET /api/jobs/{job_id} every 2 seconds for status."
    ),
)
@limiter.limit(settings.rate_limit)
async def create_job(
    request:          Request,
    background_tasks: BackgroundTasks,
    image:    UploadFile = File(...,  description="Selfie photo. JPEG or PNG. Max 5MB."),
    style_id: str        = Form(...,  description="Mustache style ID (e.g. 'handlebar')."),
    user_id:  str        = Form(None, description="Client-generated UUID."),
):
    if not user_id:
        user_id = str(uuid.uuid4())

    # Validate image bytes
    image_bytes = await validate_image(image)

    jobs_service = JobsService()

    # Validate style_id against DB (falls back gracefully if DB is down)
    try:
        styles          = await jobs_service.get_styles()
        valid_style_ids = {s["id"] for s in styles}
        if style_id not in valid_style_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid style_id '{style_id}'. Valid options: {sorted(valid_style_ids)}.",
            )
    except HTTPException:
        raise
    except Exception as e:
        # DB unavailable — allow any style_id through (pipeline handles unknown ones)
        logger.warning(f"Styles DB check skipped (DB error): {e}")

    job_id = str(uuid.uuid4())

    # Persist job record to DB (best-effort — non-fatal)
    try:
        job = await jobs_service.create_job(
            user_id=user_id,
            style_id=style_id,
            input_image_url="[in-memory]",
        )
        job_id = job["id"]
    except Exception as e:
        logger.warning(f"DB insert skipped: {e}")

    # Kick off background AI processing
    background_tasks.add_task(
        _process_job,
        job_id=job_id,
        style_id=style_id,
        image_bytes=image_bytes,
        jobs_service=jobs_service,
    )

    logger.info(f"Job {job_id} queued (user={user_id}, style={style_id})")
    return JobResponse(job_id=job_id, status=JobStatus.PENDING, created_at=None)


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}
# ---------------------------------------------------------------------------
@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status",
    description="Poll every 2 seconds. Status: pending → processing → done | failed.",
)
async def get_job_status(job_id: str):
    # Check in-memory cache first (fastest path)
    cached_url = _results_cache.get(job_id)
    if cached_url:
        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus.DONE,
            output_url=cached_url,
            error=None,
        )

    # Fall back to DB
    jobs_service = JobsService()
    try:
        job = await jobs_service.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job '{job_id}' not found.",
            )
        return JobStatusResponse(
            job_id=job["id"],
            status=JobStatus(job["status"]),
            output_url=job.get("output_image_url"),
            error=job.get("error_message"),
        )
    except HTTPException:
        raise
    except Exception as e:
        # DB unavailable — job is still processing in background
        logger.warning(f"DB status check failed for {job_id}: {e}")
        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus.PROCESSING,
            output_url=None,
            error=None,
        )
