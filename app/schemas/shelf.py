"""Schemas del contrato de estantes (shelves) y celdas (cells) (FEAT-017)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

CellDescription = Annotated[str | None, Field(default=None, max_length=200)]
ShelfDescription = Annotated[str | None, Field(default=None, max_length=200)]


def _reject_duplicate_positions(cells: list[CellCreate] | list[CellUpdate]) -> None:
    """Impide dos celdas con la misma posicion en el mismo estante.

    La comparacion es case-insensitive, igual que el `CITEXT` de la base y la
    constraint `uq_cells_position`; sin esta validacion el cliente recibiria un
    error de integridad en vez de un `422` con el campo culpable.
    """
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(cells):
        key = (item.section.strip().casefold(), item.cell.strip().casefold())
        if key in seen:
            raise ValueError(
                f"cells.{index}: la posición '{item.section}/{item.cell}' está repetida"
            )
        seen.add(key)


class CellCreate(BaseModel):
    """Alta de celda dentro de un estante."""

    model_config = ConfigDict(extra="forbid")

    section: Annotated[str, Field(min_length=1, max_length=50)]
    cell: Annotated[str, Field(min_length=1, max_length=50)]
    description: CellDescription = None
    is_active: bool = True


class CellUpdate(BaseModel):
    """Reemplazo completo de una celda."""

    model_config = ConfigDict(extra="forbid")

    section: Annotated[str, Field(min_length=1, max_length=50)]
    cell: Annotated[str, Field(min_length=1, max_length=50)]
    description: CellDescription = None
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


class ShelfCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Alta de estante con sus celdas."""

    name: Annotated[str, Field(min_length=2, max_length=100)]
    description: ShelfDescription = None
    is_active: bool = True
    cells: list[CellCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_cells(self) -> ShelfCreate:
        _reject_duplicate_positions(self.cells)
        return self


class ShelfUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Reemplazo completo de un estante con sincronizacion de celdas."""

    name: Annotated[str, Field(min_length=2, max_length=100)]
    description: ShelfDescription = None
    is_active: bool
    cells: list[CellUpdate]

    @model_validator(mode="after")
    def _validate_cells(self) -> ShelfUpdate:
        _reject_duplicate_positions(self.cells)
        return self


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
    model_config = ConfigDict(from_attributes=True)

    items: list[ShelfRead]
    total: int
    page: int
    size: int
    pages: int
