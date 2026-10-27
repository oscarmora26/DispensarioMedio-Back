"""Reglas de negocio para estantes (shelves) y celdas (cells)."""

from __future__ import annotations

from app.core.exceptions import ConflictError, NotFoundError
from app.models.cell import Cell
from app.models.shelf import Shelf
from app.repositories import cell_repository, shelf_repository
from app.schemas.shelf import ShelfCreate, ShelfUpdate

SHELF_EXISTS_MESSAGE = "El estante ya existe"
SHELF_NOT_FOUND_MESSAGE = "El estante no encontrado"
CELL_HAS_MEDICATIONS_MESSAGE = "La celda tiene medicamentos asociados"


def _normalize_description(value: str | None) -> str | None:
    """Convierte descripcion vacia o solo espacios en `None`."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _position_key(section: str, cell: str) -> tuple[str, str]:
    """Clave de posicion case-insensitive (`CITEXT`)."""
    return (section.strip().casefold(), cell.strip().casefold())


def calculate_pages(total: int, size: int) -> int:
    return (total + size - 1) // size if total else 0


async def list_shelves(session, *, page, size, search, is_active, sort):
    sort_field, sort_desc = _parse_sort(sort)
    shelves, total = await shelf_repository.list_shelves(
        session,
        page=page,
        size=size,
        search=search,
        is_active=is_active,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    return shelves, total


def _parse_sort(sort: str) -> tuple[str, bool]:
    sort_desc = sort.startswith("-")
    field = sort.lstrip("-")
    allowed = {"name", "created_at", "is_active", "id"}
    if field not in allowed:
        field = "name"
    return field, sort_desc


async def get_shelf(session, shelf_id: int) -> Shelf:
    shelf = await shelf_repository.get_shelf_by_id(session, shelf_id)
    if shelf is None:
        raise NotFoundError(SHELF_NOT_FOUND_MESSAGE, code="shelf_not_found")
    return shelf


async def create_shelf(session, payload: ShelfCreate) -> Shelf:
    existing = await shelf_repository.find_shelf_by_name(session, payload.name)
    if existing is not None:
        raise ConflictError(SHELF_EXISTS_MESSAGE, code="shelf_exists")
    shelf = Shelf(
        name=payload.name,
        description=_normalize_description(payload.description),
        is_active=payload.is_active,
    )
    for item in payload.cells:
        shelf.cells.append(
            Cell(
                section=item.section.strip(),
                cell=item.cell.strip(),
                description=_normalize_description(item.description),
                is_active=item.is_active,
            )
        )
    session.add(shelf)
    await session.flush()
    return await shelf_repository.get_shelf_by_id(session, shelf.id)


async def update_shelf(session, shelf_id: int, payload: ShelfUpdate) -> Shelf:
    """Actualiza el estante y sincroniza sus celdas por posicion.

    Las celdas se emparejan por `(section, cell)` sin distinguir mayusculas: las
    existentes se actualizan en el lugar (conservando su `id`, que es la FK de
    `medications.cell_id`), las nuevas se crean y las ausentes se eliminan.
    Una celda con medicamentos no se elimina: responde `409 cell_has_medications`.
    """
    shelf = await shelf_repository.get_shelf_by_id(session, shelf_id)
    if shelf is None:
        raise NotFoundError(SHELF_NOT_FOUND_MESSAGE, code="shelf_not_found")

    other = await shelf_repository.find_shelf_by_name(session, payload.name, exclude_id=shelf_id)
    if other is not None:
        raise ConflictError(SHELF_EXISTS_MESSAGE, code="shelf_exists")

    shelf.name = payload.name
    shelf.description = _normalize_description(payload.description)
    shelf.is_active = payload.is_active

    existing = {_position_key(c.section, c.cell): c for c in shelf.cells}
    kept: set[tuple[str, str]] = set()

    for item in payload.cells:
        key = _position_key(item.section, item.cell)
        kept.add(key)
        current = existing.get(key)
        if current is None:
            session.add(
                Cell(
                    shelf_id=shelf.id,
                    section=item.section.strip(),
                    cell=item.cell.strip(),
                    description=_normalize_description(item.description),
                    is_active=item.is_active,
                )
            )
            continue
        current.description = _normalize_description(item.description)
        current.is_active = item.is_active

    for key, current in existing.items():
        if key in kept:
            continue
        if await cell_repository.count_medications_by_cell(session, current.id) > 0:
            raise ConflictError(
                CELL_HAS_MEDICATIONS_MESSAGE,
                code="cell_has_medications",
                errors=[{"field": "cells", "message": CELL_HAS_MEDICATIONS_MESSAGE}],
            )
        await session.delete(current)

    await session.flush()
    # La coleccion carga con `selectinload`; tras borrar/crear celdas hay que
    # expirarla para que la respuesta refleje el estado real de la base y no
    # instancias obsoletas del identity map.
    session.expire(shelf, ["cells"])
    return await shelf_repository.get_shelf_by_id(session, shelf.id)


async def delete_shelf(session, shelf_id: int) -> Shelf:
    """Desactiva el estante y todas sus celdas (baja logica)."""
    shelf = await shelf_repository.get_shelf_by_id(session, shelf_id)
    if shelf is None:
        raise NotFoundError(SHELF_NOT_FOUND_MESSAGE, code="shelf_not_found")
    shelf.is_active = False
    for item in shelf.cells:
        item.is_active = False
    await session.flush()
    return shelf
