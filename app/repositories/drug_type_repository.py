"""Acceso a datos de tipos de farmacos."""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drug_type import DrugType

# Columna de ordenacion permitida para cada campo del catalogo de FEAT-007 D-3.
SORT_COLUMNS = {
    "description": DrugType.description,
    "created_at": DrugType.created_at,
    "is_active": DrugType.is_active,
    "id": DrugType.id,
}


def escape_like(value: str) -> str:
    """Escapa `\\`, `%` y `_` para que `ILIKE` los trate como literales."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_drug_type_by_id(session: AsyncSession, drug_type_id: int) -> DrugType | None:
    """Busca un tipo de farmaco por identificador."""
    result = await session.execute(select(DrugType).where(DrugType.id == drug_type_id))
    return result.scalar_one_or_none()


async def find_drug_type_by_description(
    session: AsyncSession,
    description: str,
    *,
    exclude_id: int | None = None,
) -> DrugType | None:
    """Busca un tipo por `description` (CITEXT: case-insensitive).

    `exclude_id` permite ignorar al propio tipo en un reemplazo completo.
    """
    conditions = [DrugType.description == description]
    if exclude_id is not None:
        conditions.append(DrugType.id != exclude_id)
    result = await session.execute(select(DrugType.id).where(*conditions).limit(1))
    row_id = result.scalar_one_or_none()
    if row_id is None:
        return None
    return await get_drug_type_by_id(session, row_id)


def _list_conditions(*, search: str | None, is_active: bool | None) -> list[object]:
    conditions: list[object] = []
    if search:
        pattern = f"%{escape_like(search)}%"
        conditions.append(DrugType.description.ilike(pattern, escape="\\"))
    if is_active is not None:
        conditions.append(DrugType.is_active.is_(is_active))
    return conditions


def _ordered(
    statement: Select[tuple[DrugType]], *, sort_field: str, sort_desc: bool
) -> Select[tuple[DrugType]]:
    column = SORT_COLUMNS[sort_field]
    if sort_field == "created_at":
        ordering = column.desc().nulls_last() if sort_desc else column.asc().nulls_last()
    else:
        ordering = column.desc() if sort_desc else column.asc()

    order_by = [ordering]
    if sort_field != "id":
        order_by.append(DrugType.id.asc())
    return statement.order_by(*order_by)


async def list_drug_types(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[DrugType], int]:
    """Devuelve una pagina de tipos y el total de filas que cumplen los filtros."""
    conditions = _list_conditions(search=search, is_active=is_active)

    total_result = await session.execute(select(func.count(DrugType.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(DrugType).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total
