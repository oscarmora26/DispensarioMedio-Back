"""Tests del contrato OpenAPI y del envelope global (FEAT-002, CA-41 y CA-45)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.main import app
from tests.auth_support import LOGIN_PATH, ME_PATH, create_auth_user
from tests.db_support import unique_suffix


def test_ca41_openapi_declares_auth_contract() -> None:
    """CA-41: rutas, seguridad y respuestas documentadas."""
    spec = app.openapi()
    paths = spec["paths"]

    auth_paths = sorted(path for path in paths if "auth" in path)
    assert auth_paths == [
        "/api/v1/auth/change-password",
        "/api/v1/auth/login",
        "/api/v1/auth/me",
    ]

    assert bool(paths["/api/v1/auth/me"]["get"].get("security")) is True
    assert bool(paths["/api/v1/auth/change-password"]["post"].get("security")) is True
    assert "security" not in paths["/api/v1/auth/login"]["post"]

    login = paths["/api/v1/auth/login"]["post"]
    assert sorted(login["responses"]) == ["200", "401", "403", "422", "429"]
    assert login["tags"] == ["auth"]
    assert login["summary"]
    assert login["description"]

    for path, method in (
        ("/api/v1/auth/me", "get"),
        ("/api/v1/auth/change-password", "post"),
    ):
        responses = paths[path][method]["responses"]
        for status_code in ("401", "403", "422"):
            assert status_code in responses, (path, status_code)
        assert paths[path][method]["tags"] == ["auth"]

    assert "BearerAuth" in spec["components"]["securitySchemes"]


async def test_ca45_error_envelope_is_uniform(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-45: 401 y 422 comparten `{detail, code, errors}` sin `details`."""
    unauthorized = await auth_client.get(ME_PATH)
    assert unauthorized.status_code == 401
    unauthorized_body = unauthorized.json()
    assert set(unauthorized_body) == {"detail", "code", "errors"}
    assert "details" not in unauthorized_body

    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)
    validation = await auth_client.post(LOGIN_PATH, json={"username": username})
    assert validation.status_code == 422
    validation_body = validation.json()
    assert set(validation_body) == {"detail", "code", "errors"}
    assert "details" not in validation_body
    assert isinstance(validation_body["errors"], list)
    assert validation_body["errors"]
    assert all(set(item) == {"field", "message"} for item in validation_body["errors"])


async def test_health_endpoint_is_unchanged(auth_client: AsyncClient) -> None:
    """El nuevo envelope no altera `/health`."""
    response = await auth_client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "version", "environment"}
    assert body["status"] == "ok"
