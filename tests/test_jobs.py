"""
Backend tests for the Jobs endpoints.
Run: pytest tests/ -v
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from app.main import app

client = TestClient(app)


class TestGetJobStatus:
    """Tests for GET /api/jobs/{job_id}"""

    def test_job_not_found_returns_404(self):
        with patch("app.routers.jobs.JobsService") as MockService:
            MockService.return_value.get_job = AsyncMock(return_value=None)
            response = client.get("/api/jobs/nonexistent-id-1234")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_job_pending_returns_correct_shape(self):
        mock_job = {
            "id":                "abc-123",
            "status":            "pending",
            "output_image_url":  None,
            "error_message":     None,
        }
        with patch("app.routers.jobs.JobsService") as MockService:
            MockService.return_value.get_job = AsyncMock(return_value=mock_job)
            response = client.get("/api/jobs/abc-123")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"]    == "abc-123"
        assert data["status"]    == "pending"
        assert data["output_url"] is None
        assert data["error"]      is None

    def test_job_done_returns_output_url(self):
        mock_job = {
            "id":               "abc-456",
            "status":           "done",
            "output_image_url": "https://supabase.co/storage/v1/sign/results/abc-456.jpg?token=xyz",
            "error_message":    None,
        }
        with patch("app.routers.jobs.JobsService") as MockService:
            MockService.return_value.get_job = AsyncMock(return_value=mock_job)
            response = client.get("/api/jobs/abc-456")

        assert response.status_code == 200
        data = response.json()
        assert data["status"]     == "done"
        assert data["output_url"] is not None

    def test_job_failed_returns_error(self):
        mock_job = {
            "id":               "abc-789",
            "status":           "failed",
            "output_image_url": None,
            "error_message":    "No face detected.",
        }
        with patch("app.routers.jobs.JobsService") as MockService:
            MockService.return_value.get_job = AsyncMock(return_value=mock_job)
            response = client.get("/api/jobs/abc-789")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"]  == "No face detected."


class TestImageValidation:
    """Tests for image upload validation."""

    def test_invalid_content_type_rejected(self):
        """Only image/jpeg and image/png are allowed."""
        response = client.post(
            "/api/jobs",
            data={"style_id": "chevron", "user_id": "user-1"},
            files={"image": ("test.gif", b"GIF89a", "image/gif")},
        )
        assert response.status_code == 415

    def test_missing_image_returns_422(self):
        response = client.post(
            "/api/jobs",
            data={"style_id": "chevron", "user_id": "user-1"},
        )
        assert response.status_code == 422
