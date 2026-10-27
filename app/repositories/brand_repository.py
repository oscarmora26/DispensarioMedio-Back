"""Acceso a datos de marcas."""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brand import Brand

# Columna de ordenacion permitida para cada campo del catalogo de FEAT-008 D-3.
SORT_COLUMNS = {
    "description": Brand.description,
    "created_at": Brand.created_at,
    "is_active": Brand.is_active,
    "id": Brand.id,
}


def escape_like(value: str) -> str:
    """Escapa `\\`, `%` y `_` para que `ILIKE` los trate como literales."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_brand_by_id(session: AsyncSession, brand_id: int) -> Brand | None:
    """Busca una marca por identificador."""
    result = await session.execute(select(Brand).where(Brand.id == brand_id))
    return result.scalar_one_or_none()


async def find_brand_by_description(
    session: AsyncSession,
    description: str,
    *,
    exclude_id: int | None = None,
) -> Brand | None:
    """Busca una marca por `description` (CITEXT: case-insensitive).

    `exclude_id` permite ignorar a la propia marca en un reemplazo completo.
    """
    conditions = [Brand.description == description]
    if exclude_id is not None:
        conditions.append(Brand.id != exclude_id)
    result = await session.execute(select(Brand.id).where(*conditions).limit(1))
    row_id = result.scalar_one_or_none()
    if row_id is None:
        return None
    return await get_brand_by_id(session, row_id)


def _list_conditions(*, search: str | None, is_active: bool | None) -> list[object]:
    conditions: list[object] = []
    if search:
        pattern = f"%{escape_like(search)}%"
        conditions.append(
            func.unaccent(Brand.description).ilike(func.unaccent(pattern), escape="\\")
        )
    if is_active is not None:
        conditions.append(Brand.is_active.is_(is_active))
    return conditions


def _ordered(
    statement: Select[tuple[Brand]], *, sort_field: str, sort_desc: bool
) -> Select[tuple[Brand]]:
    column = SORT_COLUMNS[sort_field]
    if sort_field == "created_at":
        ordering = column.desc().nulls_last() if sort_desc else column.asc().nulls_last()
    else:
        ordering = column.desc() if sort_desc else column.asc()

    order_by = [ordering]
    if sort_field != "id":
        order_by.append(Brand.id.asc())
    return statement.order_by(*order_by)


async def list_brands(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[Brand], int]:
    """Devuelve una pagina de marcas y el total de filas que cumplen los filtros."""
    conditions = _list_conditions(search=search, is_active=is_active)

    total_result = await session.execute(select(func.count(Brand.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(Brand).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total
