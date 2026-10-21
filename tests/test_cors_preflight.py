"""Tests de preflight, logs y ausencia de dependencias (FEAT-006, CA-17..CA-21 y CA-26)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app

BuildApp = Callable[..., FastAPI]

ORIGIN = "http://localhost:4200"
EVIL_ORIGIN = "http://evil.example"
CORS_ENV_VARS = ("CORS_ALLOWED_ORIGINS", "CORS_ALLOWED_METHODS", "CORS_ALLOWED_HEADERS")
ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
PREFLIGHT_VARY_VALUES = (
    "Origin",
    "Access-Control-Request-Method",
    "Access-Control-Request-Headers",
    "Access-Control-Request-Private-Network",
)


@pytest.fixture
def build_app(monkeypatch: pytest.MonkeyPatch) -> BuildApp:
    """Fabrica apps aisladas: fija variables, limpia la cache y llama a `create_app()`."""

    def build(*, environment: str = "local", **env: str) -> FastAPI:
        for name in CORS_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("ENVIRONMENT", environment)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        return create_app()

    return build


@asynccontextmanager
async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP ASGI contra la app dada (sin red ni base de datos)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def preflight_headers(
    *,
    origin: str = ORIGIN,
    method: str = "POST",
    request_headers: str | None = None,
) -> dict[str, str]:
    """Construye las cabeceras de un preflight."""
    headers = {"Origin": origin, "Access-Control-Request-Method": method}
    if request_headers is not None:
        headers["Access-Control-Request-Headers"] = request_headers
    return headers


# CA-17 ------------------------------------------------------------------------


async def test_ca17_allowed_preflight_returns_exact_cors_headers(build_app: BuildApp) -> None:
    """CA-17: preflight permitido => 200 `OK` con todas las cabeceras de la spec."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        response = await client.options(
            "/api/v1/auth/login",
            headers=preflight_headers(request_headers="authorization,content-type"),
        )

    assert response.status_code == 200
    assert response.text == "OK"
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-methods"] == ALLOW_METHODS
    allow_headers = response.headers["access-control-allow-headers"]
    assert "Authorization" in allow_headers
    assert "Content-Type" in allow_headers
    assert response.headers["access-control-max-age"] == "600"
    vary = response.headers["vary"]
    for value in PREFLIGHT_VARY_VALUES:
        assert value in vary
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["x-request-id"]


# CA-18 ------------------------------------------------------------------------


async def test_ca18_preflight_from_disallowed_origin(build_app: BuildApp) -> None:
    """CA-18: origen no permitido => 400 `Disallowed CORS origin` sin `Allow-Origin`."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        response = await client.options(
            "/api/v1/auth/login", headers=preflight_headers(origin=EVIL_ORIGIN)
        )

    assert response.status_code == 400
    assert response.text == "Disallowed CORS origin"
    assert "access-control-allow-origin" not in response.headers
    # Starlette incluye igualmente estos headers; la spec lo documenta como no-fallo.
    assert response.headers["access-control-allow-methods"] == ALLOW_METHODS
    assert response.headers["access-control-max-age"] == "600"
    assert response.headers["x-request-id"]


# CA-19 ------------------------------------------------------------------------


async def test_ca19_preflight_with_disallowed_method(build_app: BuildApp) -> None:
    """CA-19: metodo no permitido => 400 `Disallowed CORS method` con `Allow-Origin`."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        response = await client.options(
            "/api/v1/auth/login", headers=preflight_headers(method="TRACE")
        )

    assert response.status_code == 400
    assert response.text == "Disallowed CORS method"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in response.headers


# CA-20 ------------------------------------------------------------------------


async def test_ca20_preflight_with_disallowed_header(build_app: BuildApp) -> None:
    """CA-20: cabecera no permitida => 400 `Disallowed CORS headers`; la permitida pasa."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        rejected = await client.options(
            "/api/v1/auth/login",
            headers=preflight_headers(request_headers="authorization, x-custom"),
        )
        accepted = await client.options(
            "/api/v1/auth/login",
            headers=preflight_headers(request_headers="authorization"),
        )

    assert rejected.status_code == 400
    assert rejected.text == "Disallowed CORS headers"
    assert rejected.headers["access-control-allow-origin"] == ORIGIN
    assert accepted.status_code == 200
    assert accepted.text == "OK"


# CA-21 ------------------------------------------------------------------------


async def test_ca21_preflight_is_logged_without_leaking_origin(
    build_app: BuildApp, caplog: pytest.LogCaptureFixture
) -> None:
    """CA-21: el preflight se registra como `OPTIONS` y el log no filtra el origen."""
    app = build_app(CORS_ALLOWED_ORIGINS=ORIGIN)

    # `configure_logging()` dentro de `create_app()` limpia los handlers del root:
    # se reengancha el handler de caplog despues de construir la app.
    root = logging.getLogger()
    reattached = caplog.handler not in root.handlers
    if reattached:
        root.addHandler(caplog.handler)
    caplog.set_level(logging.INFO)

    try:
        async with client_for(app) as client:
            response = await client.options(
                "/api/v1/auth/login",
                headers={
                    **preflight_headers(request_headers="authorization,content-type"),
                    "Cookie": "session=secreta",
                },
            )
            assert response.status_code == 200
    finally:
        if reattached:
            root.removeHandler(caplog.handler)

    events: list[dict[str, Any]] = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "request.completed"
    ]
    assert events, "no se capturo el evento request.completed del preflight"
    event = events[-1]
    assert event["method"] == "OPTIONS"
    assert event["path"] == "/api/v1/auth/login"
    assert event["status"] == 200

    logged = caplog.text.lower()
    assert ORIGIN not in logged
    assert "authorization" not in logged
    assert "secreta" not in logged


# CA-26 ------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/users"])
async def test_ca26_preflight_to_protected_route_needs_no_auth_or_database(
    build_app: BuildApp, path: str
) -> None:
    """CA-26: el preflight se resuelve antes del router, sin token ni sesion de BD."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        response = await client.options(path, headers=preflight_headers(method="GET"))

    assert response.status_code == 200
    assert response.text == "OK"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-methods"] == ALLOW_METHODS
