"""Reglas de negocio de celdas (cells)."""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.cell import Cell
from app.models.user import User
from app.repositories import cell_repository
from app.schemas.shelf import CellCreate, CellListResponse, CellRead, CellUpdate

logger = structlog.get_logger(__name__)

CELL_NOT_FOUND_MESSAGE = "La celda no existe"
SHELF_NOT_FOUND_MESSAGE = "El estante no existe"
CELL_POSITION_EXISTS_MESSAGE = "Ya existe una celda en esa posición del estante"
CELL_HAS_MEDICATIONS_MESSAGE = "La celda tiene medicamentos asociados"

SORT_FIELDS = ("section", "cell", "shelf_id", "created_at", "is_active", "id")


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


def _to_read(cell: Cell) -> CellRead:
    return CellRead.model_validate(cell)


async def _require_shelf(session: AsyncSession, shelf_id: int) -> None:
    if await cell_repository.get_shelf_by_id(session, shelf_id) is None:
        raise NotFoundError(SHELF_NOT_FOUND_MESSAGE, code="shelf_not_found")


async def _require_free_position(
    session: AsyncSession,
    shelf_id: int,
    section: str,
    cell: str,
    *,
    exclude_id: int | None = None,
) -> None:
    """Rechaza una posicion ya ocupada dentro del estante (CITEXT)."""
    existing = await cell_repository.find_cell_by_position(
        session, shelf_id, section, cell, exclude_id=exclude_id
    )
    if existing is not None:
        raise ConflictError(
            CELL_POSITION_EXISTS_MESSAGE,
            code="cell_position_exists",
            errors=[{"field": "section", "message": CELL_POSITION_EXISTS_MESSAGE}],
        )


async def _get_or_404(session: AsyncSession, cell_id: int) -> Cell:
    cell = await cell_repository.get_cell_by_id(session, cell_id)
    if cell is None:
        raise NotFoundError(CELL_NOT_FOUND_MESSAGE, code="cell_not_found")
    return cell


async def list_cells(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None = None,
    is_active: bool | None = None,
    shelf_id: int | None = None,
    sort: str = "shelf_id",
) -> CellListResponse:
    """Lista celdas con paginacion, busqueda, filtros y orden."""
    sort_field, sort_desc = parse_sort(sort)
    cells, total = await cell_repository.list_cells(
        session,
        page=page,
        size=size,
        search=search.strip() if search else None,
        is_active=is_active,
        shelf_id=shelf_id,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    return CellListResponse(
        items=[_to_read(cell) for cell in cells],
        total=total,
        page=page,
        size=size,
        pages=calculate_pages(total, size),
    )


async def get_cell(session: AsyncSession, cell_id: int) -> CellRead:
    """Devuelve una celda por identificador."""
    return _to_read(await _get_or_404(session, cell_id))


async def create_cell(session: AsyncSession, payload: CellCreate, *, actor: User) -> CellRead:
    """Crea una celda validando estante y unicidad de posicion."""
    await _require_shelf(session, payload.shelf_id)
    await _require_free_position(session, payload.shelf_id, payload.section, payload.cell)

    cell = Cell(
        shelf_id=payload.shelf_id,
        section=payload.section,
        cell=payload.cell,
        description=_normalize_description(payload.description),
        is_active=payload.is_active,
    )
    session.add(cell)
    await session.flush()
    logger.info(
        "cell.created",
        cell_id=cell.id,
        shelf_id=cell.shelf_id,
        actor_id=actor.id,
    )
    return _to_read(cell)


async def update_cell(
    session: AsyncSession, cell_id: int, payload: CellUpdate, *, actor: User
) -> CellRead:
    """Reemplaza una celda; `shelf_id` permite moverla de estante."""
    cell = await _get_or_404(session, cell_id)
    await _require_shelf(session, payload.shelf_id)
    await _require_free_position(
        session,
        payload.shelf_id,
        payload.section,
        payload.cell,
        exclude_id=cell.id,
    )

    cell.shelf_id = payload.shelf_id
    cell.section = payload.section
    cell.cell = payload.cell
    cell.description = _normalize_description(payload.description)
    cell.is_active = payload.is_active
    await session.flush()
    logger.info(
        "cell.updated",
        cell_id=cell.id,
        shelf_id=cell.shelf_id,
        actor_id=actor.id,
    )
    # `updated_at` usa `onupdate=func.now()`: tras el flush el atributo queda
    # expirado y leerlo en contexto async dispara `MissingGreenlet`. Se relee la
    # fila (mismo patron que `shelf_service.update_shelf`).
    return _to_read(await _get_or_404(session, cell.id))


async def delete_cell(session: AsyncSession, cell_id: int, *, actor: User) -> None:
    """Baja logica de la celda; bloqueada cuando tiene medicamentos."""
    cell = await _get_or_404(session, cell_id)

    medications = await cell_repository.count_medications_by_cell(session, cell.id)
    if medications > 0:
        raise ConflictError(
            CELL_HAS_MEDICATIONS_MESSAGE,
            code="cell_has_medications",
            errors=[{"field": "cell_id", "message": CELL_HAS_MEDICATIONS_MESSAGE}],
        )

    if not cell.is_active:
        return

    cell.is_active = False
    await session.flush()
    logger.info("cell.deactivated", cell_id=cell.id, actor_id=actor.id)
