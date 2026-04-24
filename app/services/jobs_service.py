"""
Jobs database service.

Handles all CRUD operations on the `jobs` and `styles` tables in Supabase.
"""

import logging
import uuid

from supabase import Client

from app.models.job import JobStatus
from app.services.storage import get_supabase_client

logger = logging.getLogger(__name__)


class JobsService:
    """Data access layer for jobs and styles tables."""

    def __init__(self) -> None:
        try:
            self.client: Client | None = get_supabase_client()
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")
            self.client = None

    # ------------------------------------------------------------------
    # Jobs — Create
    # ------------------------------------------------------------------
    async def create_job(
        self,
        user_id: str,
        style_id: str,
        input_image_url: str,
    ) -> dict:
        """
        Insert a new job row with status='pending'.

        Returns the created row as a dict.
        """
        if not self.client:
            raise Exception("Supabase client not initialized")
            
        job_id = str(uuid.uuid4())
        payload = {
            "id":              job_id,
            "user_id":         user_id,
            "style_id":        style_id,
            "status":          JobStatus.PENDING.value,
            "input_image_url": input_image_url,
        }
        response = self.client.table("jobs").insert(payload).execute()
        logger.info(f"Job created: {job_id} (user={user_id}, style={style_id})")
        return response.data[0]

    # ------------------------------------------------------------------
    # Jobs — Read
    # ------------------------------------------------------------------
    async def get_job(self, job_id: str) -> dict | None:
        """
        Fetch a single job by its UUID.

        Returns the job dict or None if not found.
        """
        response = (
            self.client.table("jobs")
            .select("*")
            .eq("id", job_id)
            .maybe_single()
            .execute()
        )
        return response.data

    # ------------------------------------------------------------------
    # Jobs — Update
    # ------------------------------------------------------------------
    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        output_url: str | None = None,
        error: str | None = None,
    ) -> None:
        """
        Update a job's status, and optionally set output_url or error_message.
        """
        updates: dict = {"status": status.value}
        if output_url is not None:
            updates["output_image_url"] = output_url
        if error is not None:
            updates["error_message"] = error

        self.client.table("jobs").update(updates).eq("id", job_id).execute()
        logger.info(f"Job {job_id} → {status.value}")

    # ------------------------------------------------------------------
    # Jobs — Active count (rate limit: 1 active job per user)
    # ------------------------------------------------------------------
    async def get_active_jobs_count(self, user_id: str) -> int:
        """
        Count pending or processing jobs for a given user.
        Used to enforce the 1-active-job-per-user limit.
        """
        response = (
            self.client.table("jobs")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .in_("status", [JobStatus.PENDING.value, JobStatus.PROCESSING.value])
            .execute()
        )
        return response.count or 0

    # ------------------------------------------------------------------
    # Styles — Read all
    # ------------------------------------------------------------------
    async def get_styles(self) -> list[dict]:
        """
        Fetch all mustache styles from the styles table.
        (Caller caches this in-memory for 5 minutes.)
        """
        response = self.client.table("styles").select("*").execute()
        return response.data or []
