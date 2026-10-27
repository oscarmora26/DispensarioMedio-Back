"""Acceso a datos de celdas (cells)."""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cell import Cell
from app.models.medication import Medication
from app.models.shelf import Shelf

SORT_COLUMNS = {
    "section": Cell.section,
    "cell": Cell.cell,
    "shelf_id": Cell.shelf_id,
    "created_at": Cell.created_at,
    "is_active": Cell.is_active,
    "id": Cell.id,
}


def escape_like(value: str) -> str:
    """Escapa `\\`, `%` y `_` para que `ILIKE` los trate como literales."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_cell_by_id(session: AsyncSession, cell_id: int) -> Cell | None:
    """Busca una celda por identificador."""
    result = await session.execute(select(Cell).where(Cell.id == cell_id))
    return result.scalar_one_or_none()


async def find_cell_by_position(
    session: AsyncSession,
    shelf_id: int,
    section: str,
    cell: str,
    *,
    exclude_id: int | None = None,
) -> Cell | None:
    """Busca una celda por su posicion `(shelf_id, section, cell)`.

    Las columnas de posicion son `CITEXT`: la comparacion es case-insensitive.
    `exclude_id` permite ignorar la propia celda en un reemplazo completo.
    """
    conditions = [
        Cell.shelf_id == shelf_id,
        Cell.section == section,
        Cell.cell == cell,
    ]
    if exclude_id is not None:
        conditions.append(Cell.id != exclude_id)
    result = await session.execute(select(Cell.id).where(*conditions).limit(1))
    row_id = result.scalar_one_or_none()
    if row_id is None:
        return None
    return await get_cell_by_id(session, row_id)


async def count_medications_by_cell(session: AsyncSession, cell_id: int) -> int:
    """Cuenta los medicamentos ubicados en una celda."""
    result = await session.execute(
        select(func.count(Medication.id)).where(Medication.cell_id == cell_id)
    )
    return int(result.scalar_one())


def _list_conditions(
    *, search: str | None, is_active: bool | None, shelf_id: int | None
) -> list[object]:
    conditions: list[object] = []
    if search:
        pattern = f"%{escape_like(search)}%"
        conditions.append(
            or_(
                func.unaccent(Cell.section).ilike(func.unaccent(pattern), escape="\\"),
                func.unaccent(Cell.cell).ilike(func.unaccent(pattern), escape="\\"),
                func.unaccent(Cell.description).ilike(func.unaccent(pattern), escape="\\"),
            )
        )
    if is_active is not None:
        conditions.append(Cell.is_active.is_(is_active))
    if shelf_id is not None:
        conditions.append(Cell.shelf_id == shelf_id)
    return conditions


def _ordered(
    statement: Select[tuple[Cell]], *, sort_field: str, sort_desc: bool
) -> Select[tuple[Cell]]:
    column = SORT_COLUMNS[sort_field]
    if sort_field == "created_at":
        ordering = column.desc().nulls_last() if sort_desc else column.asc().nulls_last()
    else:
        ordering = column.desc() if sort_desc else column.asc()

    order_by = [ordering]
    if sort_field != "id":
        order_by.append(Cell.id.asc())
    return statement.order_by(*order_by)


async def list_cells(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    shelf_id: int | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[Cell], int]:
    """Devuelve una pagina de celdas y el total de filas que cumplen los filtros."""
    conditions = _list_conditions(search=search, is_active=is_active, shelf_id=shelf_id)

    total_result = await session.execute(select(func.count(Cell.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(Cell).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total


async def get_shelf_by_id(session: AsyncSession, shelf_id: int) -> Shelf | None:
    """Busca el estante al que pertenece una celda."""
    result = await session.execute(select(Shelf).where(Shelf.id == shelf_id))
    return result.scalar_one_or_none()
