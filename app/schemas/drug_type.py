"""Schemas del contrato de tipos de farmacos (FEAT-007 D-4, D-5, D-6)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

# Catalogo cerrado de ordenacion del listado (FEAT-007 D-3).
SORT_FIELDS = ("description", "created_at", "is_active", "id")
SORT_PATTERN = r"^-?(?:description|created_at|is_active|id)$"


class _DrugTypeDescription(BaseModel):
    """Descripcion normalizada compartida por alta y reemplazo completo."""

    description: str = Field(
        max_length=100,
        description="Descripcion del tipo de farmaco, entre 1 y 100 caracteres",
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


class DrugTypeCreate(_DrugTypeDescription):
    """Alta de tipo de farmaco; `is_active` es opcional (D-5, por defecto `true`)."""

    is_active: bool = Field(default=True, description="Estado inicial del tipo")


class DrugTypeUpdate(_DrugTypeDescription):
    """Reemplazo completo de un tipo de farmaco (D-1); ambos campos obligatorios."""

    is_active: bool = Field(description="Estado resultante del tipo")


class DrugTypeRead(BaseModel):
    """Forma unica de un tipo de farmaco (D-4): exactamente `id`, `description`, `is_active`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    is_active: bool


class DrugTypeListResponse(BaseModel):
    """Pagina de tipos de farmacos."""

    items: list[DrugTypeRead]
    total: int
    page: int
    size: int
    pages: int
