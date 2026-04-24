"""
Backend tests for the Styles endpoint.
Run: pytest tests/ -v
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app

client = TestClient(app)

MOCK_STYLES = [
    {"id": "chevron",   "label": "Chevron",     "preview_url": "assets/previews/chevron_preview.jpg",   "asset_url": "assets/chevron.png"},
    {"id": "handlebar", "label": "Handlebar",   "preview_url": "assets/previews/handlebar_preview.jpg", "asset_url": "assets/handlebar.png"},
    {"id": "fu_manchu", "label": "Fu Manchu",   "preview_url": "assets/previews/fu_manchu_preview.jpg", "asset_url": "assets/fu_manchu.png"},
    {"id": "pencil",    "label": "Pencil Thin", "preview_url": "assets/previews/pencil_preview.jpg",    "asset_url": "assets/pencil.png"},
    {"id": "walrus",    "label": "Walrus",       "preview_url": "assets/previews/walrus_preview.jpg",    "asset_url": "assets/walrus.png"},
    {"id": "english",   "label": "English",     "preview_url": "assets/previews/english_preview.jpg",   "asset_url": "assets/english.png"},
]


class TestGetStyles:
    """Tests for GET /api/styles"""

    def test_returns_all_6_styles(self):
        import app.routers.styles as styles_module
        styles_module._cache = None  # Clear cache between tests

        with patch("app.routers.styles.JobsService") as MockService:
            MockService.return_value.get_styles = AsyncMock(return_value=MOCK_STYLES)
            response = client.get("/api/styles")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 6
        assert len(data["styles"]) == 6

    def test_style_shape_is_correct(self):
        import app.routers.styles as styles_module
        styles_module._cache = None

        with patch("app.routers.styles.JobsService") as MockService:
            MockService.return_value.get_styles = AsyncMock(return_value=MOCK_STYLES)
            response = client.get("/api/styles")

        style = response.json()["styles"][0]
        assert "id"          in style
        assert "label"       in style
        assert "preview_url" in style
        assert "asset_url"   in style

    def test_all_6_style_ids_present(self):
        import app.routers.styles as styles_module
        styles_module._cache = None

        with patch("app.routers.styles.JobsService") as MockService:
            MockService.return_value.get_styles = AsyncMock(return_value=MOCK_STYLES)
            response = client.get("/api/styles")

        ids = {s["id"] for s in response.json()["styles"]}
        expected = {"chevron", "handlebar", "fu_manchu", "pencil", "walrus", "english"}
        assert ids == expected

    def test_supabase_failure_returns_503(self):
        import app.routers.styles as styles_module
        styles_module._cache = None

        with patch("app.routers.styles.JobsService") as MockService:
            MockService.return_value.get_styles = AsyncMock(
                side_effect=Exception("Supabase connection failed")
            )
            response = client.get("/api/styles")

        assert response.status_code == 503
