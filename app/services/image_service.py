"""
Image Service — persists original uploads and AI results to Supabase Storage,
and keeps the original_images / ai_generated_images tables in sync.
"""

import logging
import uuid
from datetime import datetime, timezone

from supabase import create_client, Client

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _service_client() -> Client:
    """Supabase client with the service-role key — bypasses RLS."""
    return create_client(settings.supabase_url, settings.supabase_service_key)


class ImageService:
    """
    Data + Storage access layer for original_images and ai_generated_images.

    All methods are async-compatible but call the synchronous Supabase SDK
    internally (the SDK doesn't expose an async interface yet).
    """

    def __init__(self) -> None:
        try:
            self.db: Client | None = _service_client()
        except Exception as exc:
            logger.error(f"[ImageService] Failed to initialise Supabase client: {exc}")
            self.db = None

    # ------------------------------------------------------------------
    # create_job_records
    # ------------------------------------------------------------------
    async def create_job_records(
        self,
        user_id:     str,
        image_bytes: bytes,
        style_id:    str,
        mime_type:   str = "image/jpeg",
    ) -> tuple[str, str]:
        """
        1. Upload the original selfie to Storage: uploads/{user_id}/{uuid}.jpg
        2. Insert a row in public.original_images.
        3. Insert a row in public.ai_generated_images (status='pending').

        Returns:
            (original_image_id, ai_image_id) as UUID strings.
        """
        if not self.db:
            raise RuntimeError("Supabase client not initialised.")

        ext = "png" if "png" in mime_type else "jpg"
        file_uuid     = str(uuid.uuid4())
        storage_path  = f"{user_id}/{file_uuid}.{ext}"  # uploads/<user>/<uuid>.jpg

        # --- 1. Upload original to Storage --------------------------------
        try:
            self.db.storage.from_(settings.uploads_bucket).upload(
                path=storage_path,           # fixed: use user_id folder
                file=image_bytes,
                file_options={"content-type": mime_type, "upsert": "true"},
            )
            logger.info(f"[ImageService] Uploaded original → {settings.uploads_bucket}/{storage_path}")
        except Exception as exc:
            logger.error(f"[ImageService] Upload failed: {exc}")
            raise

        # Signed URL for the original (1-hour expiry)
        try:
            signed = self.db.storage.from_(settings.uploads_bucket).create_signed_url(
                storage_path, settings.signed_url_expiry_seconds
            )
            original_url = signed.get("signedURL") or signed.get("signed_url") or ""
        except Exception:
            original_url = ""

        # --- 2. Insert original_images row --------------------------------
        original_id = str(uuid.uuid4())
        self.db.table("original_images").insert({
            "id":           original_id,
            "user_id":      user_id,
            "storage_path": f"{file_uuid}.{ext}",
            "url":          original_url,
            "file_size":    len(image_bytes),
            "mime_type":    mime_type,
        }).execute()
        logger.info(f"[ImageService] Inserted original_images row: {original_id}")

        # --- 3. Insert ai_generated_images row (status=pending) -----------
        ai_image_id = str(uuid.uuid4())
        self.db.table("ai_generated_images").insert({
            "id":               ai_image_id,
            "user_id":          user_id,
            "original_image_id": original_id,
            "style_id":         style_id,
            "status":           "pending",
        }).execute()
        logger.info(f"[ImageService] Inserted ai_generated_images row: {ai_image_id}")

        return original_id, ai_image_id

    # ------------------------------------------------------------------
    # mark_ai_processing
    # ------------------------------------------------------------------
    async def mark_ai_processing(self, ai_image_id: str) -> None:
        """Set ai_generated_images.status = 'processing'."""
        if not self.db:
            return
        try:
            self.db.table("ai_generated_images").update({
                "status": "processing",
            }).eq("id", ai_image_id).execute()
        except Exception as exc:
            logger.warning(f"[ImageService] mark_ai_processing failed: {exc}")

    # ------------------------------------------------------------------
    # save_ai_result
    # ------------------------------------------------------------------
    async def save_ai_result(
        self,
        user_id:      str,
        ai_image_id:  str,
        result_bytes: bytes,
    ) -> str:
        """
        1. Upload AI result to Storage: results/{ai_image_id}.jpg
        2. Generate a signed URL.
        3. Update ai_generated_images row: status='done', url=<signed_url>.

        Returns:
            The signed URL string.
        """
        if not self.db:
            logger.warning("[ImageService] No DB client — skipping storage persist.")
            return ""

        result_filename = f"{user_id}/{ai_image_id}.jpg"

        # --- 1. Upload result image ---------------------------------------
        try:
            self.db.storage.from_(settings.results_bucket).upload(
                path=result_filename,
                file=result_bytes,
                file_options={"content-type": "image/jpeg", "upsert": "true"},
            )
            logger.info(f"[ImageService] Uploaded result → {settings.results_bucket}/{result_filename}")
        except Exception as exc:
            logger.error(f"[ImageService] Result upload failed: {exc}")
            raise

        # --- 2. Signed URL ------------------------------------------------
        try:
            signed = self.db.storage.from_(settings.results_bucket).create_signed_url(
                result_filename, settings.signed_url_expiry_seconds
            )
            output_url = signed.get("signedURL") or signed.get("signed_url") or ""
        except Exception:
            output_url = ""

        # --- 3. Update DB row ---------------------------------------------
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.db.table("ai_generated_images").update({
                "status":        "done",
                "storage_path":  result_filename,
                "url":           output_url,
                "completed_at":  now,
            }).eq("id", ai_image_id).execute()
            logger.info(f"[ImageService] ai_generated_images {ai_image_id} → done")
        except Exception as exc:
            logger.warning(f"[ImageService] DB update (done) failed: {exc}")

        return output_url

    # ------------------------------------------------------------------
    # mark_ai_failed
    # ------------------------------------------------------------------
    async def mark_ai_failed(self, ai_image_id: str, error_message: str) -> None:
        """Set ai_generated_images.status = 'failed' with error_message."""
        if not self.db:
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.db.table("ai_generated_images").update({
                "status":        "failed",
                "error_message": error_message[:500],   # truncate for DB
                "completed_at":  now,
            }).eq("id", ai_image_id).execute()
            logger.info(f"[ImageService] ai_generated_images {ai_image_id} → failed")
        except Exception as exc:
            logger.warning(f"[ImageService] DB update (failed) failed: {exc}")

    # ------------------------------------------------------------------
    # get_user_images   (bonus: list history)
    # ------------------------------------------------------------------
    async def get_user_ai_images(self, user_id: str) -> list[dict]:
        """
        Fetch all ai_generated_images for a user, joined with original_images.
        Returns a list of dicts ordered by created_at desc.
        """
        if not self.db:
            return []
        try:
            resp = (
                self.db.table("ai_generated_images")
                .select("*, original_images(url)")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(50)
                .execute()
            )
            return resp.data or []
        except Exception as exc:
            logger.warning(f"[ImageService] get_user_ai_images failed: {exc}")
            return []
