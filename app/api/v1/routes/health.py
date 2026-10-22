"""Endpoints de salud y disponibilidad."""

import structlog
from fastapi import APIRouter, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.schemas.health import HealthResponse, ReadinessResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Indica que el proceso esta vivo. No consulta dependencias externas.",
)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.version,
        environment=settings.environment,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description="Verifica que la base de datos responda.",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Base de datos no disponible"}},
)
async def readiness(response: Response) -> ReadinessResponse:
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        logger.warning("readiness.database_unavailable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="unavailable", database="down")
    return ReadinessResponse(status="ok", database="up")
