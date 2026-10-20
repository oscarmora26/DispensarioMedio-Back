"""Tests de los endpoints de salud."""

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["environment"]


async def test_health_response_has_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.headers.get("x-request-id")


async def test_health_echoes_incoming_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "test-request-id"})

    assert response.headers["x-request-id"] == "test-request-id"


async def test_ready_reports_database_status(client: AsyncClient) -> None:
    response = await client.get("/api/v1/ready")

    assert response.status_code in {200, 503}
    assert response.json()["database"] in {"up", "down"}
