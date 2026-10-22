"""Schemas del contrato de marcas (FEAT-008 D-4, D-5, D-6)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

# Catalogo cerrado de ordenacion del listado (FEAT-008 D-3).
SORT_FIELDS = ("description", "created_at", "is_active", "id")
SORT_PATTERN = r"^-?(?:description|created_at|is_active|id)$"


class _BrandDescription(BaseModel):
    """Descripcion normalizada compartida por alta y reemplazo completo."""

    description: str = Field(
        max_length=100,
        description="Descripcion de la marca, entre 1 y 100 caracteres",
    )

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        if not value:
            raise PydanticCustomError(
                "description_empty",
                "La descripción no puede estar vacía",
            )
        return value


class BrandCreate(_BrandDescription):
    """Alta de marca; `is_active` es opcional (D-5, por defecto `true`)."""

    is_active: bool = Field(default=True, description="Estado inicial de la marca")


class BrandUpdate(_BrandDescription):
    """Reemplazo completo de una marca (D-1); ambos campos obligatorios."""

    is_active: bool = Field(description="Estado resultante de la marca")


class BrandRead(BaseModel):
    """Forma unica de una marca (D-4): exactamente `id`, `description`, `is_active`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    is_active: bool


class BrandListResponse(BaseModel):
    """Pagina de marcas."""

    items: list[BrandRead]
    total: int
    page: int
    size: int
    pages: int
