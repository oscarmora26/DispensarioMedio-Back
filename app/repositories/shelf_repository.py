"""Acceso a datos de estantes (shelves) y celdas (cells)."""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.shelf import Shelf

SORT_COLUMNS = {
    "name": Shelf.name,
    "created_at": Shelf.created_at,
    "is_active": Shelf.is_active,
    "id": Shelf.id,
}


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_shelf_by_id(session: AsyncSession, shelf_id: int) -> Shelf | None:
    result = await session.execute(
        select(Shelf).options(selectinload(Shelf.cells)).where(Shelf.id == shelf_id)
    )
    return result.scalar_one_or_none()


async def find_shelf_by_name(
    session: AsyncSession, name: str, *, exclude_id: int | None = None
) -> Shelf | None:
    conditions = [Shelf.name == name]
    if exclude_id is not None:
        conditions.append(Shelf.id != exclude_id)
    result = await session.execute(select(Shelf.id).where(*conditions).limit(1))
    row_id = result.scalar_one_or_none()
    if row_id is None:
        return None
    return await get_shelf_by_id(session, row_id)


def _list_conditions(*, search: str | None, is_active: bool | None) -> list[object]:
    conditions: list[object] = []
    if search:
        pattern = f"%{escape_like(search)}%"
        conditions.append(
            or_(
                func.unaccent(Shelf.name).ilike(func.unaccent(pattern), escape="\\"),
                func.unaccent(Shelf.description).ilike(func.unaccent(pattern), escape="\\"),
            )
        )
    if is_active is not None:
        conditions.append(Shelf.is_active.is_(is_active))
    return conditions


def _ordered(statement: Select, *, sort_field: str, sort_desc: bool) -> Select:
    column = SORT_COLUMNS[sort_field]
    if sort_field == "created_at":
        ordering = column.desc().nulls_last() if sort_desc else column.asc().nulls_last()
    else:
        ordering = column.desc() if sort_desc else column.asc()
    order_by = [ordering]
    if sort_field != "id":
        order_by.append(Shelf.id.asc())
    return statement.order_by(*order_by)


async def list_shelves(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[Shelf], int]:
    conditions = _list_conditions(search=search, is_active=is_active)

    total_result = await session.execute(select(func.count(Shelf.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(Shelf).options(selectinload(Shelf.cells)).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total
