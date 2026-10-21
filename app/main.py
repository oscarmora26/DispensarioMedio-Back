"""Punto de entrada de la API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import (
    CORS_EXPOSE_HEADERS,
    CORS_PREFLIGHT_MAX_AGE_SECONDS,
    get_settings,
)
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import engine
from app.middleware.http import AccessLogMiddleware, RequestIDMiddleware

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Se reconfigura al arrancar: uvicorn aplica su propio logging despues de importar la app.
    configure_logging(
        level=settings.log_level,
        json_logs=settings.log_json or settings.is_production,
        include_source=settings.log_include_source,
    )
    logger.info(
        "app.starting",
        version=settings.version,
        environment=settings.environment,
    )
    yield
    await engine.dispose()
    logger.info("app.stopped")


def create_app() -> FastAPI:
    """Construye y configura la aplicacion FastAPI."""
    settings = get_settings()

    configure_logging(
        level=settings.log_level,
        json_logs=settings.log_json or settings.is_production,
        include_source=settings.log_include_source,
    )

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    # El ultimo middleware agregado es el mas externo: RequestID debe envolver al de acceso.
    # CORS se agrega primero para quedar dentro de ambos: asi el preflight tambien
    # recibe X-Request-ID y se registra en el access log.
    # Si no hay origenes configurados, CORS queda deshabilitado (fail-closed).
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_methods=settings.cors_allowed_methods,
            allow_headers=settings.cors_allowed_headers,
            allow_credentials=False,
            expose_headers=CORS_EXPOSE_HEADERS,
            max_age=CORS_PREFLIGHT_MAX_AGE_SECONDS,
        )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
