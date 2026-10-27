"""Reglas de negocio para estantes (shelves).

La escritura de celdas vive en `cell_service`: un estante se gestiona con sus
propios campos y sus celdas se crean, editan o desactivan por `/cells`.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.shelf import Shelf
from app.models.user import User
from app.repositories import shelf_repository
from app.schemas.shelf import ShelfCreate, ShelfListResponse, ShelfRead, ShelfUpdate

logger = structlog.get_logger(__name__)

SHELF_EXISTS_MESSAGE = "El estante ya existe"
SHELF_NOT_FOUND_MESSAGE = "El estante no existe"

SORT_FIELDS = ("name", "created_at", "is_active", "id")


def calculate_pages(total: int, size: int) -> int:
    """Cantidad de paginas para un total y tamano de pagina dados."""
    return (total + size - 1) // size if total else 0


def parse_sort(sort: str) -> tuple[str, bool]:
    """Traduce `campo` o `-campo` en `(campo, descendente)`."""
    sort_desc = sort.startswith("-")
    field = sort.removeprefix("-")
    return field, sort_desc


def _normalize_description(value: str | None) -> str | None:
    """Convierte descripcion vacia o solo espacios en `None`."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _to_read(shelf: Shelf) -> ShelfRead:
    return ShelfRead.model_validate(shelf)


async def _get_or_404(session: AsyncSession, shelf_id: int) -> Shelf:
    shelf = await shelf_repository.get_shelf_by_id(session, shelf_id)
    if shelf is None:
        raise NotFoundError(SHELF_NOT_FOUND_MESSAGE, code="shelf_not_found")
    return shelf


async def list_shelves(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None = None,
    is_active: bool | None = None,
    sort: str = "name",
) -> ShelfListResponse:
    """Lista estantes con sus celdas anidadas, con paginacion y filtros."""
    sort_field, sort_desc = parse_sort(sort)
    shelves, total = await shelf_repository.list_shelves(
        session,
        page=page,
        size=size,
        search=search.strip() if search else None,
        is_active=is_active,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    return ShelfListResponse(
        items=[_to_read(shelf) for shelf in shelves],
        total=total,
        page=page,
        size=size,
        pages=calculate_pages(total, size),
    )


async def get_shelf(session: AsyncSession, shelf_id: int) -> ShelfRead:
    """Devuelve un estante con sus celdas por identificador."""
    return _to_read(await _get_or_404(session, shelf_id))


async def create_shelf(session: AsyncSession, payload: ShelfCreate, *, actor: User) -> ShelfRead:
    """Crea un estante sin celdas."""
    if await shelf_repository.find_shelf_by_name(session, payload.name) is not None:
        raise ConflictError(SHELF_EXISTS_MESSAGE, code="shelf_exists")

    shelf = Shelf(
        name=payload.name,
        description=_normalize_description(payload.description),
        is_active=payload.is_active,
    )
    session.add(shelf)
    await session.flush()
    logger.info("shelf.created", shelf_id=shelf.id, actor_id=actor.id)
    return _to_read(await _get_or_404(session, shelf.id))


async def update_shelf(
    session: AsyncSession, shelf_id: int, payload: ShelfUpdate, *, actor: User
) -> ShelfRead:
    """Reemplaza los campos del estante; no modifica sus celdas."""
    shelf = await _get_or_404(session, shelf_id)

    other = await shelf_repository.find_shelf_by_name(session, payload.name, exclude_id=shelf_id)
    if other is not None:
        raise ConflictError(SHELF_EXISTS_MESSAGE, code="shelf_exists")

    shelf.name = payload.name
    shelf.description = _normalize_description(payload.description)
    shelf.is_active = payload.is_active
    await session.flush()
    logger.info("shelf.updated", shelf_id=shelf.id, actor_id=actor.id)
    return _to_read(await _get_or_404(session, shelf.id))


async def delete_shelf(session: AsyncSession, shelf_id: int, *, actor: User) -> None:
    """Baja logica del estante: lo desactiva junto con todas sus celdas."""
    shelf = await _get_or_404(session, shelf_id)

    if not shelf.is_active:
        return

    shelf.is_active = False
    for item in shelf.cells:
        item.is_active = False
    await session.flush()
    logger.info("shelf.deactivated", shelf_id=shelf.id, actor_id=actor.id)
