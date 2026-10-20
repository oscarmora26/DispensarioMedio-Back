"""Schemas de los endpoints de salud."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Respuesta del liveness probe."""

    status: Literal["ok"] = Field(description="Estado del proceso")
    version: str = Field(description="Version de la aplicacion")
    environment: str = Field(description="Entorno de ejecucion")


class ReadinessResponse(BaseModel):
    """Respuesta del readiness probe."""

    status: Literal["ok", "unavailable"] = Field(description="Estado del servicio")
    database: Literal["up", "down"] = Field(description="Estado de la base de datos")
