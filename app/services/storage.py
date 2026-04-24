"""
Supabase Storage service.

Handles:
  - Uploading images to private buckets
  - Generating signed URLs (1-hour expiry)
  - Downloading assets
"""

import logging

from supabase import Client, create_client

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_supabase_client() -> Client:
    """Create and return a Supabase client using the service role key."""
    return create_client(settings.supabase_url, settings.supabase_service_key)


class StorageService:
    """Thin wrapper around Supabase Storage operations."""

    def __init__(self) -> None:
        try:
            self.client: Client | None = get_supabase_client()
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")
            self.client = None

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    async def upload_image(
        self,
        bucket: str,
        path: str,
        data: bytes,
        content_type: str = "image/jpeg",
    ) -> str:
        """
        Upload raw image bytes to a private Supabase Storage bucket.

        Args:
            bucket:       Bucket name (uploads / results / assets)
            path:         Object path inside the bucket, e.g. "{job_id}.jpg"
            data:         Raw image bytes
            content_type: MIME type

        Returns:
            The storage path on success.

        Raises:
            Exception on Supabase SDK error.
        """
        if not self.client:
            raise Exception("Supabase client not initialized")
            
        try:
            self.client.storage.from_(bucket).upload(
                path=path,
                file=data,
                file_options={"content-type": content_type, "upsert": "true"},
            )
            logger.info(f"Uploaded → {bucket}/{path} ({len(data)/1024:.1f}KB)")
            return path
        except Exception as exc:
            logger.error(f"Upload failed [{bucket}/{path}]: {exc}")
            raise

    # ------------------------------------------------------------------
    # Signed URL
    # ------------------------------------------------------------------
    def get_signed_url(
        self,
        bucket: str,
        path: str,
        expires_in: int | None = None,
    ) -> str:
        """
        Generate a signed URL for a private storage object.

        Args:
            bucket:     Bucket name
            path:       Object path
            expires_in: Seconds until expiry (defaults to settings value = 3600s)

        Returns:
            Signed URL string.
        """
        expiry = expires_in or settings.signed_url_expiry_seconds
        try:
            response = self.client.storage.from_(bucket).create_signed_url(path, expiry)
            return response["signedURL"]
        except Exception as exc:
            logger.error(f"Signed URL failed [{bucket}/{path}]: {exc}")
            raise

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------
    async def download_file(self, bucket: str, path: str) -> bytes:
        """
        Download a file from Supabase Storage.

        Args:
            bucket: Bucket name
            path:   Object path

        Returns:
            Raw bytes of the file.
        """
        try:
            data = self.client.storage.from_(bucket).download(path)
            logger.info(f"Downloaded ← {bucket}/{path} ({len(data)/1024:.1f}KB)")
            return data
        except Exception as exc:
            logger.error(f"Download failed [{bucket}/{path}]: {exc}")
            raise
