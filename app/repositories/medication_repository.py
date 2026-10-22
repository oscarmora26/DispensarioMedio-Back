"""Acceso a datos de medicamentos."""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication import Medication

# Columna de ordenacion permitida para cada campo del catalogo de FEAT-010 D-3.
SORT_COLUMNS = {
    "description": Medication.description,
    "dose": Medication.dose,
    "drug_type_id": Medication.drug_type_id,
    "brand_id": Medication.brand_id,
    "location_id": Medication.location_id,
    "created_at": Medication.created_at,
    "is_active": Medication.is_active,
    "id": Medication.id,
}


def escape_like(value: str) -> str:
    """Escapa `\\`, `%` y `_` para que `ILIKE` los trate como literales."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_medication_by_id(session: AsyncSession, medication_id: int) -> Medication | None:
    """Busca un medicamento por identificador."""
    result = await session.execute(select(Medication).where(Medication.id == medication_id))
    return result.scalar_one_or_none()


async def find_medication_by_identity(
    session: AsyncSession,
    description: str,
    brand_id: int,
    dose: str,
    *,
    exclude_id: int | None = None,
) -> Medication | None:
    """Busca un medicamento por su identidad `(description, brand_id, dose)`.

    `description` y `dose` son `CITEXT`: la comparacion es case-insensitive.
    `exclude_id` permite ignorar al propio medicamento en un reemplazo completo.
    """
    conditions: list[object] = [
        Medication.description == description,
        Medication.brand_id == brand_id,
        Medication.dose == dose,
    ]
    if exclude_id is not None:
        conditions.append(Medication.id != exclude_id)
    result = await session.execute(select(Medication.id).where(*conditions).limit(1))
    row_id = result.scalar_one_or_none()
    if row_id is None:
        return None
    return await get_medication_by_id(session, row_id)


def _list_conditions(
    *,
    search: str | None,
    is_active: bool | None,
    drug_type_id: int | None,
    brand_id: int | None,
    location_id: int | None,
) -> list[object]:
    conditions: list[object] = []
    if search:
        pattern = f"%{escape_like(search)}%"
        conditions.append(
            or_(
                Medication.description.ilike(pattern, escape="\\"),
                Medication.dose.ilike(pattern, escape="\\"),
            )
        )
    if is_active is not None:
        conditions.append(Medication.is_active.is_(is_active))
    if drug_type_id is not None:
        conditions.append(Medication.drug_type_id == drug_type_id)
    if brand_id is not None:
        conditions.append(Medication.brand_id == brand_id)
    if location_id is not None:
        conditions.append(Medication.location_id == location_id)
    return conditions


def _ordered(
    statement: Select[tuple[Medication]], *, sort_field: str, sort_desc: bool
) -> Select[tuple[Medication]]:
    column = SORT_COLUMNS[sort_field]
    if sort_field == "created_at":
        ordering = column.desc().nulls_last() if sort_desc else column.asc().nulls_last()
    else:
        ordering = column.desc() if sort_desc else column.asc()

    order_by = [ordering]
    if sort_field != "id":
        order_by.append(Medication.id.asc())
    return statement.order_by(*order_by)


async def list_medications(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    drug_type_id: int | None,
    brand_id: int | None,
    location_id: int | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[Medication], int]:
    """Devuelve una pagina de medicamentos y el total que cumplen los filtros."""
    conditions = _list_conditions(
        search=search,
        is_active=is_active,
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
    )

    total_result = await session.execute(select(func.count(Medication.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(Medication).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total
