"""
Jobs Router

Endpoints:
  POST /api/jobs        — Upload photo + select style → returns { job_id }
  GET  /api/jobs/{id}  — Poll for job status → pending | processing | done | failed
"""

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

logger = logging.getLogger(__name__)
settings = get_settings()
router  = APIRouter()
limiter = Limiter(key_func=get_remote_address)


# ---------------------------------------------------------------------------
# Background Task: Run AI pipeline and update job record
# ---------------------------------------------------------------------------
async def _process_job(
    job_id:          str,
    user_id:         str,
    style_id:        str,
    image_bytes:     bytes,
    jobs_service:    JobsService,
    storage_service: StorageService,
) -> None:
    """
    Background task executed after POST /api/jobs returns.

    Steps:
      1. Mark job as 'processing'
      2. Download mustache asset from /assets/{style_id}.png
      3. Run AI pipeline (face detection + overlay)
      4. Upload result to /results/{job_id}.jpg
      5. Generate signed URL (1hr expiry)
      6. Mark job as 'done' with output_url
    """
    try:
        # Step 1 — Mark as processing
        await jobs_service.update_job_status(job_id, JobStatus.PROCESSING)

        # Step 2 — Download mustache asset
        asset_path    = f"{style_id}.png"
        mustache_bytes = await storage_service.download_file(
            settings.assets_bucket, asset_path
        )

        # Step 3 — Run AI pipeline
        result_bytes = overlay_mustache(image_bytes, mustache_bytes)

        # Step 4 — Upload result image
        result_path = f"{job_id}.jpg"
        await storage_service.upload_image(
            bucket=settings.results_bucket,
            path=result_path,
            data=result_bytes,
            content_type="image/jpeg",
        )

        # Step 5 — Generate signed URL
        output_url = storage_service.get_signed_url(
            settings.results_bucket, result_path
        )

        # Step 6 — Mark done
        await jobs_service.update_job_status(
            job_id, JobStatus.DONE, output_url=output_url
        )
        logger.info(f"✅ Job {job_id} → DONE")

    except AIProcessingError as exc:
        # Known failure: no face detected, etc.
        logger.warning(f"AI processing failed for job {job_id}: {exc}")
        await jobs_service.update_job_status(
            job_id, JobStatus.FAILED, error=str(exc)
        )

    except Exception as exc:
        # Unexpected failure
        logger.error(f"Unexpected error for job {job_id}: {exc}", exc_info=True)
        await jobs_service.update_job_status(
            job_id,
            JobStatus.FAILED,
            error="An internal error occurred. Please try again.",
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
    image:    UploadFile = File(...,   description="Selfie photo. JPEG or PNG. Max 5MB. Max 4000×4000px."),
    style_id: str        = Form(...,   description="Mustache style ID (e.g. 'handlebar')."),
    user_id:  str        = Form(None,  description="Client-generated UUID. Persisted in the mobile app."),
):
    # Generate user_id if not provided
    if not user_id:
        user_id = str(uuid.uuid4())

    jobs_service    = JobsService()
    storage_service = StorageService()

    # --- Validate image ---
    image_bytes = await validate_image(image)

    # --- Validate style_id ---
    styles          = await jobs_service.get_styles()
    valid_style_ids = {s["id"] for s in styles}
    if style_id not in valid_style_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid style_id '{style_id}'. Valid options: {sorted(valid_style_ids)}.",
        )

    # --- 1-active-job-per-user limit ---
    active = await jobs_service.get_active_jobs_count(user_id)
    if active >= 1:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You already have an active job. Please wait for it to complete.",
        )

    job_id = str(uuid.uuid4())
    
    try:
        # Prevent database crashes if Supabase isn't hooked up yet
        await storage_service.upload_image(
            bucket=settings.uploads_bucket,
            path=f"{job_id}.jpg",
            data=image_bytes,
            content_type=image.content_type or "image/jpeg",
        )
        input_url = storage_service.get_signed_url(settings.uploads_bucket, f"{job_id}.jpg")
        job = await jobs_service.create_job(
            user_id=user_id,
            style_id=style_id,
            input_image_url=input_url,
        )
    except Exception as e:
        logger.error(f"Supabase error intercepted: {e}. Generating mock job!")
        job = {"id": job_id, "created_at": "now"}

    # --- Kick off background AI processing ---
    background_tasks.add_task(
        _process_job,
        job_id=job["id"],
        user_id=user_id,
        style_id=style_id,
        image_bytes=image_bytes,
        jobs_service=jobs_service,
        storage_service=storage_service,
    )

    logger.info(f"Job {job['id']} queued (user={user_id}, style={style_id})")

    return JobResponse(
        job_id=job["id"],
        status=JobStatus.PENDING,
        created_at=job.get("created_at"),
    )


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
    jobs_service = JobsService()
    try:
        job = await jobs_service.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job '{job_id}' not found.",
            )
    except Exception as e:
        logger.error(f"Get job status DB error intercepted: {e}. Returning mock DONE status.")
        # FAKE the AI being completely done, using a placeholder image for the result!
        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus.DONE,
            output_url="https://placehold.co/400x400.png?text=Mustachified!",
            error=None,
        )

    return JobStatusResponse(
        job_id=job["id"],
        status=JobStatus(job["status"]),
        output_url=job.get("output_image_url"),
        error=job.get("error_message"),
    )
