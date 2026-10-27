"""Acceso a datos de ubicaciones."""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location

# Columna de ordenacion permitida para cada campo del catalogo de FEAT-009 D-3.
SORT_COLUMNS = {
    "description": Location.description,
    "shelf": Location.shelf,
    "section": Location.section,
    "cell": Location.cell,
    "created_at": Location.created_at,
    "is_active": Location.is_active,
    "id": Location.id,
}


def escape_like(value: str) -> str:
    """Escapa `\\`, `%` y `_` para que `ILIKE` los trate como literales."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_location_by_id(session: AsyncSession, location_id: int) -> Location | None:
    """Busca una ubicación por identificador."""
    result = await session.execute(select(Location).where(Location.id == location_id))
    return result.scalar_one_or_none()


async def find_location_by_position(
    session: AsyncSession,
    shelf: str,
    section: str,
    cell: str,
    *,
    exclude_id: int | None = None,
) -> Location | None:
    """Busca una ubicación por su posición `(shelf, section, cell)`.

    Las tres columnas son `CITEXT`: la comparación es case-insensitive.
    `exclude_id` permite ignorar a la propia ubicación en un reemplazo completo.
    """
    conditions = [
        Location.shelf == shelf,
        Location.section == section,
        Location.cell == cell,
    ]
    if exclude_id is not None:
        conditions.append(Location.id != exclude_id)
    result = await session.execute(select(Location.id).where(*conditions).limit(1))
    row_id = result.scalar_one_or_none()
    if row_id is None:
        return None
    return await get_location_by_id(session, row_id)


def _list_conditions(*, search: str | None, is_active: bool | None) -> list[object]:
    conditions: list[object] = []
    if search:
        pattern = f"%{escape_like(search)}%"
        conditions.append(
            or_(
                func.unaccent(Location.description).ilike(func.unaccent(pattern), escape="\\"),
                func.unaccent(Location.shelf).ilike(func.unaccent(pattern), escape="\\"),
                func.unaccent(Location.section).ilike(func.unaccent(pattern), escape="\\"),
                func.unaccent(Location.cell).ilike(func.unaccent(pattern), escape="\\"),
            )
        )
    if is_active is not None:
        conditions.append(Location.is_active.is_(is_active))
    return conditions


def _ordered(
    statement: Select[tuple[Location]], *, sort_field: str, sort_desc: bool
) -> Select[tuple[Location]]:
    column = SORT_COLUMNS[sort_field]
    if sort_field == "created_at":
        ordering = column.desc().nulls_last() if sort_desc else column.asc().nulls_last()
    else:
        ordering = column.desc() if sort_desc else column.asc()

    order_by = [ordering]
    if sort_field != "id":
        order_by.append(Location.id.asc())
    return statement.order_by(*order_by)


async def list_locations(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[Location], int]:
    """Devuelve una pagina de ubicaciones y el total de filas que cumplen los filtros."""
    conditions = _list_conditions(search=search, is_active=is_active)

    total_result = await session.execute(select(func.count(Location.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(Location).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total
