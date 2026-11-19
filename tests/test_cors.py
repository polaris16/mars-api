"""Verify browser preflights for local Mock storage and its JSON API."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


ORIGIN = "http://localhost:5173"


@pytest.mark.parametrize(("method", "path", "request_headers"), [
    ("GET", "/api/media-assets/1", "authorization"),
    ("POST", "/api/media-assets/upload-authorizations", "authorization, content-type"),
    ("POST", "/api/recording-sessions/1/fragments", "authorization, content-type, idempotency-key"),
    ("PUT", "/api/mock-storage/upload/token", "content-type, if-none-match"),
    ("PATCH", "/api/recording-sessions/1", "authorization, content-type"),
    ("DELETE", "/api/media-assets/1", "authorization"),
])
def test_mock_mode_allows_configured_web_origin(monkeypatch, method, path, request_headers):
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_cors_origins", ORIGIN)

    with TestClient(create_app()) as client:
        response = client.options(path, headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": request_headers,
        })

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize(("mode", "origin", "expected_status"), [
    ("mock", "http://localhost:9999", 400),
    ("s3", ORIGIN, 405),
])
def test_development_cors_does_not_allow_other_origins_or_s3(monkeypatch, mode, origin, expected_status):
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_mode", mode)
    monkeypatch.setattr(settings, "object_storage_mock_cors_origins", ORIGIN)

    with TestClient(create_app()) as client:
        response = client.options("/api/media-assets/upload-authorizations", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        })

    assert response.status_code == expected_status
    assert "access-control-allow-origin" not in response.headers
