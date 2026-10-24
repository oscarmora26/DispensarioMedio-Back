"""Data access for patients."""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.repositories.drug_type_repository import escape_like

# Sortable column for each field of the FEAT-011 D-3 catalog.
SORT_COLUMNS = {
    "name": Patient.name,
    "cedula": Patient.cedula,
    "created_at": Patient.created_at,
    "is_active": Patient.is_active,
    "id": Patient.id,
}


async def get_patient_by_id(session: AsyncSession, patient_id: int) -> Patient | None:
    """Fetch a patient by identifier."""
    result = await session.execute(select(Patient).where(Patient.id == patient_id))
    return result.scalar_one_or_none()


async def find_patient_by_cedula(
    session: AsyncSession,
    cedula: str,
    *,
    exclude_id: int | None = None,
) -> Patient | None:
    """Look up a patient by its eleven-digit cedula.

    `exclude_id` lets a full replacement ignore the patient being updated.
    """
    conditions = [Patient.cedula == cedula]
    if exclude_id is not None:
        conditions.append(Patient.id != exclude_id)
    result = await session.execute(select(Patient.id).where(*conditions).limit(1))
    row_id = result.scalar_one_or_none()
    if row_id is None:
        return None
    return await get_patient_by_id(session, row_id)


async def find_patient_by_carnet(
    session: AsyncSession,
    carnet: str,
    *,
    exclude_id: int | None = None,
) -> Patient | None:
    """Look up a patient by carnet (CITEXT: case-insensitive)."""
    conditions = [Patient.carnet == carnet]
    if exclude_id is not None:
        conditions.append(Patient.id != exclude_id)
    result = await session.execute(select(Patient.id).where(*conditions).limit(1))
    row_id = result.scalar_one_or_none()
    if row_id is None:
        return None
    return await get_patient_by_id(session, row_id)


def _list_conditions(*, search: str | None, is_active: bool | None) -> list[object]:
    conditions: list[object] = []
    if search:
        pattern = f"%{escape_like(search)}%"
        conditions.append(
            or_(
                Patient.name.ilike(pattern, escape="\\"),
                Patient.cedula.ilike(pattern, escape="\\"),
                Patient.carnet.ilike(pattern, escape="\\"),
            )
        )
    if is_active is not None:
        conditions.append(Patient.is_active.is_(is_active))
    return conditions


def _ordered(
    statement: Select[tuple[Patient]], *, sort_field: str, sort_desc: bool
) -> Select[tuple[Patient]]:
    column = SORT_COLUMNS[sort_field]
    if sort_field == "created_at":
        ordering = column.desc().nulls_last() if sort_desc else column.asc().nulls_last()
    else:
        ordering = column.desc() if sort_desc else column.asc()

    order_by = [ordering]
    if sort_field != "id":
        order_by.append(Patient.id.asc())
    return statement.order_by(*order_by)


async def list_patients(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[Patient], int]:
    """Return one page of patients plus the total matching rows."""
    conditions = _list_conditions(search=search, is_active=is_active)

    total_result = await session.execute(select(func.count(Patient.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(Patient).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total
