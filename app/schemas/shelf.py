"""Schemas del contrato de estantes (shelves) y celdas (cells) (FEAT-017).

La escritura esta separada por recurso: `/shelves` gestiona solo el estante y
`/cells` gestiona la celda con su `shelf_id`. La lectura de un estante incluye
sus celdas anidadas para que el cliente no tenga que componer la vista.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

CellDescription = Annotated[str | None, Field(default=None, max_length=200)]
ShelfDescription = Annotated[str | None, Field(default=None, max_length=200)]


def _strip(value: object) -> object:
    """Recorta espacios de un campo de texto."""
    return value.strip() if isinstance(value, str) else value


class _CellPosition(BaseModel):
    """Posicion de la celda dentro de un estante.

    `section` y `cell` son `CITEXT` en la base: la unicidad de la posicion es
    case-insensitive, por eso se recortan y comparan sin distinguir mayusculas.
    """

    model_config = ConfigDict(extra="forbid")

    section: Annotated[str, Field(min_length=1, max_length=50)]
    cell: Annotated[str, Field(min_length=1, max_length=50)]
    description: CellDescription = None

    @field_validator("section", "cell", mode="before")
    @classmethod
    def _strip_position(cls, value: object) -> object:
        return _strip(value)


class CellCreate(_CellPosition):
    """Alta de celda en un estante existente."""

    shelf_id: int = Field(ge=1, description="Identificador del estante")
    is_active: bool = True


class CellUpdate(_CellPosition):
    """Reemplazo completo de una celda; `shelf_id` permite moverla de estante."""

    shelf_id: int = Field(ge=1, description="Identificador del estante")
    is_active: bool


class CellRead(BaseModel):
    """Representacion de una celda."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    shelf_id: int
    section: str
    cell: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CellListResponse(BaseModel):
    """Pagina de celdas."""

    items: list[CellRead]
    total: int
    page: int
    size: int
    pages: int


class ShelfCreate(BaseModel):
    """Alta de estante; las celdas se crean por `/cells`."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=2, max_length=100)]
    description: ShelfDescription = None
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        return _strip(value)


class ShelfUpdate(BaseModel):
    """Reemplazo completo del estante; no toca sus celdas."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=2, max_length=100)]
    description: ShelfDescription = None
    is_active: bool

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        return _strip(value)


class ShelfRead(BaseModel):
    """Representacion de un estante con sus celdas anidadas."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    cells: list[CellRead] = Field(default_factory=list)


class ShelfListResponse(BaseModel):
    """Pagina de estantes."""

    model_config = ConfigDict(from_attributes=True)

    items: list[ShelfRead]
    total: int
    page: int
    size: int
    pages: int
