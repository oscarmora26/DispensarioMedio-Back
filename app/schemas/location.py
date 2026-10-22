"""Schemas del contrato de ubicaciones (FEAT-009 D-4, D-5, D-6)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

# Catalogo cerrado de ordenacion del listado (FEAT-009 D-3).
SORT_FIELDS = ("description", "shelf", "section", "cell", "created_at", "is_active", "id")
SORT_PATTERN = r"^-?(?:description|shelf|section|cell|created_at|is_active|id)$"

_TEXT_FIELDS = ("description", "shelf", "section", "cell")

_EMPTY_MESSAGES = {
    "description": "La descripción no puede estar vacía",
    "shelf": "El estante no puede estar vacío",
    "section": "El tramo no puede estar vacío",
    "cell": "La celda no puede estar vacía",
}


class _LocationText(BaseModel):
    """Cuatro campos de texto normalizados compartidos por alta y reemplazo."""

    description: str = Field(
        max_length=100,
        description="Descripción de la ubicación, entre 1 y 100 caracteres",
    )
    shelf: str = Field(
        max_length=100,
        description="Estante de la ubicación, entre 1 y 100 caracteres",
    )
    section: str = Field(
        max_length=100,
        description="Tramo de la ubicación, entre 1 y 100 caracteres",
    )
    cell: str = Field(
        max_length=100,
        description="Celda de la ubicación, entre 1 y 100 caracteres",
    )

    @field_validator("description", "shelf", "section", "cell", mode="before")
    @classmethod
    def _strip_text_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", "shelf", "section", "cell")
    @classmethod
    def _validate_text_fields(cls, value: str, info: ValidationInfo) -> str:
        if not value:
            field_name = info.field_name or "description"
            raise PydanticCustomError(
                f"{field_name}_empty",
                _EMPTY_MESSAGES.get(field_name, "El campo no puede estar vacío"),
            )
        return value


class LocationCreate(_LocationText):
    """Alta de ubicación; `is_active` es opcional (D-5, por defecto `true`)."""

    is_active: bool = Field(default=True, description="Estado inicial de la ubicación")


class LocationUpdate(_LocationText):
    """Reemplazo completo de una ubicación (D-1); los cinco campos obligatorios."""

    is_active: bool = Field(description="Estado resultante de la ubicación")


class LocationRead(BaseModel):
    """Forma unica de una ubicación (D-4)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    shelf: str
    section: str
    cell: str
    is_active: bool


class LocationListResponse(BaseModel):
    """Pagina de ubicaciones."""

    items: list[LocationRead]
    total: int
    page: int
    size: int
    pages: int
