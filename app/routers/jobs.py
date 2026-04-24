"""
Jobs Router — In-Memory Version
Independent of Supabase for job tracking.
"""

import base64
import logging
import uuid
import time

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
from app.utils.validators import validate_image

logger   = logging.getLogger(__name__)
settings = get_settings()
router   = APIRouter()
limiter  = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# In-Memory Store for Job Data
# Format: { job_id: { "status": JobStatus, "output_url": str, "error": str } }
# ---------------------------------------------------------------------------
_jobs_db: dict[str, dict] = {}


async def _process_job(
    job_id:       str,
    style_id:     str,
    image_bytes:  bytes,
) -> None:
    """
    Background task using purely in-memory tracking.
    """
    try:
        # Step 1 — Mark as processing
        _jobs_db[job_id]["status"] = JobStatus.PROCESSING
        logger.info(f"⏳ Job {job_id} → PROCESSING")

        # Step 2 — Run Gemini AI pipeline
        # Note: This might take 10-15 seconds
        result_bytes = overlay_mustache(image_bytes, style_id=style_id)

        # Step 3 — Convert to base64 data URI
        b64_str    = base64.b64encode(result_bytes).decode("utf-8")
        output_url = f"data:image/jpeg;base64,{b64_str}"

        # Step 4 — Update In-Memory DB
        _jobs_db[job_id].update({
            "status": JobStatus.DONE,
            "output_url": output_url
        })
        logger.info(f"✅ Job {job_id} → DONE")

    except AIProcessingError as exc:
        logger.warning(f"[AI] Job {job_id} failed: {exc}")
        _jobs_db[job_id].update({
            "status": JobStatus.FAILED,
            "error": str(exc)
        })
    except Exception as exc:
        logger.error(f"[Job] Unexpected error for {job_id}: {exc}", exc_info=True)
        _jobs_db[job_id].update({
            "status": JobStatus.FAILED,
            "error": "Internal AI error. Please try again."
        })


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.rate_limit)
async def create_job(
    request:          Request,
    background_tasks: BackgroundTasks,
    image:    UploadFile = File(...),
    style_id: str        = Form(...),
    user_id:  str        = Form(None),
):
    # Validate image
    image_bytes = await validate_image(image)
    
    job_id = str(uuid.uuid4())
    
    # Initialize in-memory record
    _jobs_db[job_id] = {
        "status": JobStatus.PENDING,
        "output_url": None,
        "error": None,
        "created_at": time.time()
    }

    # Kick off background task
    background_tasks.add_task(_process_job, job_id, style_id, image_bytes)

    logger.info(f"🚀 Job {job_id} created in-memory")
    return JobResponse(job_id=job_id, status=JobStatus.PENDING)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    job = _jobs_db.get(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        output_url=job["output_url"],
        error=job["error"]
    )
