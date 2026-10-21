"""Tests de middlewares y respuestas reales con CORS.

FEAT-006, CA-11..CA-16 y CA-22..CA-25.

Cada caso construye una app nueva con `create_app()` despues de
`get_settings.cache_clear()`; nunca se muta el `app` de modulo.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.middleware import Middleware

from app.core.config import get_settings
from app.main import create_app

BuildApp = Callable[..., FastAPI]

ORIGIN = "http://localhost:4200"
EVIL_ORIGIN = "http://evil.example"
CORS_ENV_VARS = ("CORS_ALLOWED_ORIGINS", "CORS_ALLOWED_METHODS", "CORS_ALLOWED_HEADERS")
PROD_JWT_SECRET = "cors-review-secret-0123456789abcdefghijklmnop"  # 48 caracteres
EXPOSE_HEADERS = "X-Request-ID, Retry-After"
DEFAULT_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"


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


def cors_middleware(app: FastAPI) -> Middleware:
    """Devuelve el `Middleware` de CORS registrado en la app."""
    return next(
        middleware
        for middleware in app.user_middleware
        if middleware.cls.__name__ == "CORSMiddleware"
    )


# CA-11 ------------------------------------------------------------------------


def test_ca11_middleware_order_and_cors_kwargs(build_app: BuildApp) -> None:
    """CA-11: RequestID -> AccessLog -> CORS, con los kwargs exactos de la spec."""
    app = build_app(
        CORS_ALLOWED_ORIGINS=ORIGIN,
        CORS_ALLOWED_METHODS="GET,POST,PUT,PATCH,DELETE,OPTIONS",
        CORS_ALLOWED_HEADERS="Authorization,Content-Type,Accept,X-Request-ID",
    )

    assert [middleware.cls.__name__ for middleware in app.user_middleware] == [
        "RequestIDMiddleware",
        "AccessLogMiddleware",
        "CORSMiddleware",
    ]

    kwargs = cors_middleware(app).kwargs
    assert kwargs["allow_origins"] == [ORIGIN]
    assert kwargs["allow_methods"] == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    assert kwargs["allow_headers"] == [
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Request-ID",
    ]
    assert kwargs["allow_credentials"] is False
    assert tuple(kwargs["expose_headers"]) == ("X-Request-ID", "Retry-After")
    assert kwargs["max_age"] == 600


# CA-12 / CA-13 ----------------------------------------------------------------


async def test_ca12_simple_request_from_allowed_origin(build_app: BuildApp) -> None:
    """CA-12: un GET permitido devuelve la respuesta normal mas las cabeceras CORS."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        response = await client.get("/api/v1/health", headers={"Origin": ORIGIN})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-expose-headers"] == EXPOSE_HEADERS
    assert "Origin" in response.headers["vary"]
    assert response.headers["x-request-id"]


async def test_ca13_simple_request_from_disallowed_origin(build_app: BuildApp) -> None:
    """CA-13: el servidor responde igual pero sin `Access-Control-Allow-Origin`."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        response = await client.get("/api/v1/health", headers={"Origin": EVIL_ORIGIN})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "access-control-allow-origin" not in response.headers
    assert "Origin" in response.headers["vary"]
    assert response.headers["x-request-id"]


# CA-14 ------------------------------------------------------------------------


async def test_ca14_unauthorized_response_carries_cors_headers(build_app: BuildApp) -> None:
    """CA-14: el 401 del endpoint protegido conserva su cuerpo y gana cabeceras CORS."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        response = await client.get("/api/v1/auth/me", headers={"Origin": ORIGIN})

    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-expose-headers"] == EXPOSE_HEADERS
    assert response.headers["x-request-id"]


# CA-15 ------------------------------------------------------------------------


def test_ca15_empty_origins_does_not_register_cors(build_app: BuildApp) -> None:
    """CA-15: con `CORS_ALLOWED_ORIGINS=""` no se registra `CORSMiddleware`."""
    app = build_app(CORS_ALLOWED_ORIGINS="")

    assert [middleware.cls.__name__ for middleware in app.user_middleware] == [
        "RequestIDMiddleware",
        "AccessLogMiddleware",
    ]


async def test_ca15_empty_origins_disables_cors_even_for_matching_origin(
    build_app: BuildApp,
) -> None:
    """CA-15: sin CORS no hay cabeceras de origen y el preflight cae en el 405 del router."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS="")) as client:
        simple = await client.get("/api/v1/health", headers={"Origin": ORIGIN})
        preflight = await client.options(
            "/api/v1/health",
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"},
        )

    assert simple.status_code == 200
    assert "access-control-allow-origin" not in simple.headers
    assert preflight.status_code == 405
    assert "access-control-allow-origin" not in preflight.headers
    # Regla 20: con CORS deshabilitado tampoco hay `Vary: Origin`.
    for response in (simple, preflight):
        assert "Origin" not in response.headers.get("vary", "")


async def test_ca15_production_with_empty_origins_still_starts(build_app: BuildApp) -> None:
    """CA-15: en `prod` con secreto valido, la app arranca y CORS sigue deshabilitado."""
    app = build_app(
        environment="prod",
        JWT_SECRET=PROD_JWT_SECRET,
        CORS_ALLOWED_ORIGINS="",
    )
    assert [middleware.cls.__name__ for middleware in app.user_middleware] == [
        "RequestIDMiddleware",
        "AccessLogMiddleware",
    ]

    async with client_for(app) as client:
        response = await client.get("/api/v1/health", headers={"Origin": ORIGIN})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


# CA-16 ------------------------------------------------------------------------


async def test_ca16_request_without_origin_has_vary_but_no_cors_headers(
    build_app: BuildApp,
) -> None:
    """CA-16: sin `Origin` no hay cabeceras CORS de origen/exposicion, pero si `Vary: Origin`."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-expose-headers" not in response.headers
    assert "Origin" in response.headers["vary"]
    assert response.headers["x-request-id"]


# CA-22 ------------------------------------------------------------------------


async def test_ca22_expose_headers_are_exactly_the_two_documented(build_app: BuildApp) -> None:
    """CA-22: `Access-Control-Expose-Headers` es fijo en simple y en el 401."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS=ORIGIN)) as client:
        simple = await client.get("/api/v1/health", headers={"Origin": ORIGIN})
        unauthorized = await client.get("/api/v1/auth/me", headers={"Origin": ORIGIN})

    for response in (simple, unauthorized):
        assert response.headers["access-control-expose-headers"] == EXPOSE_HEADERS


# CA-23 ------------------------------------------------------------------------


async def test_ca23_credentials_are_never_allowed(build_app: BuildApp) -> None:
    """CA-23: `allow_credentials=False` y ninguna respuesta emite `Allow-Credentials`."""
    app = build_app(CORS_ALLOWED_ORIGINS=ORIGIN)
    assert cors_middleware(app).kwargs["allow_credentials"] is False

    async with client_for(app) as client:
        responses = [
            await client.get("/api/v1/health", headers={"Origin": ORIGIN}),
            await client.get("/api/v1/auth/me", headers={"Origin": ORIGIN}),
            await client.get(
                "/api/v1/health",
                headers={"Origin": ORIGIN, "Cookie": "session=abc"},
            ),
            await client.options(
                "/api/v1/auth/login",
                headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
            ),
            await client.options(
                "/api/v1/auth/login",
                headers={"Origin": EVIL_ORIGIN, "Access-Control-Request-Method": "POST"},
            ),
        ]

    for response in responses:
        assert "access-control-allow-credentials" not in response.headers


# CA-24 ------------------------------------------------------------------------


async def test_ca24_wildcard_in_development(build_app: BuildApp) -> None:
    """CA-24: con `*` en local cualquier origen recibe `Access-Control-Allow-Origin: *`."""
    async with client_for(build_app(CORS_ALLOWED_ORIGINS="*")) as client:
        simple = await client.get("/api/v1/health", headers={"Origin": EVIL_ORIGIN})
        preflight = await client.options(
            "/api/v1/auth/login",
            headers={"Origin": EVIL_ORIGIN, "Access-Control-Request-Method": "POST"},
        )

    assert simple.status_code == 200
    assert simple.headers["access-control-allow-origin"] == "*"
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    for response in (simple, preflight):
        assert "access-control-allow-credentials" not in response.headers


# CA-25 ------------------------------------------------------------------------


async def test_ca25_production_with_explicit_origins(build_app: BuildApp) -> None:
    """CA-25: en `prod` solo el origen explicito recibe cabeceras CORS."""
    app = build_app(
        environment="prod",
        JWT_SECRET=PROD_JWT_SECRET,
        CORS_ALLOWED_ORIGINS="https://app.example.com",
    )

    async with client_for(app) as client:
        preflight = await client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        allowed = await client.get("/api/v1/health", headers={"Origin": "https://app.example.com"})
        default_origin = await client.get("/api/v1/health", headers={"Origin": ORIGIN})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://app.example.com"
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.com"
    assert default_origin.status_code == 200
    assert "access-control-allow-origin" not in default_origin.headers
