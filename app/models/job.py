"""
Pydantic models for Job-related request/response schemas.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, UUID4


class JobStatus(str, Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    DONE       = "done"
    FAILED     = "failed"


class JobResponse(BaseModel):
    """Returned immediately after POST /api/jobs."""
    job_id:     str
    status:     JobStatus
    created_at: Optional[datetime] = None


class JobStatusResponse(BaseModel):
    """Returned by GET /api/jobs/{job_id} — polled every 2s by mobile."""
    job_id:     str
    status:     JobStatus
    output_url: Optional[str] = None   # Populated when status == 'done'
    error:      Optional[str] = None   # Populated when status == 'failed'
