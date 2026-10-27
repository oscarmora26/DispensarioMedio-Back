"""Data access for doctors (FEAT-004 profile lookup plus FEAT-012 CRUD)."""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor import Doctor
from app.models.visit import Visit
from app.repositories.drug_type_repository import escape_like

# Sortable column for each field of the FEAT-012 D-3 catalog.
SORT_COLUMNS = {
    "name": Doctor.name,
    "cedula": Doctor.cedula,
    "lab_shift": Doctor.lab_shift,
    "specialty": Doctor.specialty,
    "created_at": Doctor.created_at,
    "is_active": Doctor.is_active,
    "id": Doctor.id,
}


async def get_doctor_by_id(session: AsyncSession, doctor_id: int) -> Doctor | None:
    """Busca un medico por identificador."""
    result = await session.execute(select(Doctor).where(Doctor.id == doctor_id))
    return result.scalar_one_or_none()


async def find_doctor_by_cedula(
    session: AsyncSession,
    cedula: str,
    *,
    exclude_id: int | None = None,
) -> Doctor | None:
    """Look up a doctor by its eleven-digit cedula.

    `exclude_id` lets a full replacement ignore the doctor being updated.
    """
    conditions = [Doctor.cedula == cedula]
    if exclude_id is not None:
        conditions.append(Doctor.id != exclude_id)
    result = await session.execute(select(Doctor.id).where(*conditions).limit(1))
    row_id = result.scalar_one_or_none()
    if row_id is None:
        return None
    return await get_doctor_by_id(session, row_id)


async def count_visits_by_doctor(session: AsyncSession, doctor_id: int) -> int:
    """Count visits of a doctor, any status (FEAT-012 rule 33 / D-7)."""
    result = await session.execute(select(func.count(Visit.id)).where(Visit.doctor_id == doctor_id))
    return int(result.scalar_one())


def _list_conditions(
    *,
    search: str | None,
    is_active: bool | None,
    doctor_id: int | None,
) -> list[object]:
    conditions: list[object] = []
    if search:
        pattern = f"%{escape_like(search)}%"
        conditions.append(
            or_(
                func.unaccent(Doctor.name).ilike(func.unaccent(pattern), escape="\\"),
                func.unaccent(Doctor.cedula).ilike(func.unaccent(pattern), escape="\\"),
            )
        )
    if is_active is not None:
        conditions.append(Doctor.is_active.is_(is_active))
    if doctor_id is not None:
        conditions.append(Doctor.id == doctor_id)
    return conditions


def _ordered(
    statement: Select[tuple[Doctor]], *, sort_field: str, sort_desc: bool
) -> Select[tuple[Doctor]]:
    column = SORT_COLUMNS[sort_field]
    if sort_field == "created_at":
        ordering = column.desc().nulls_last() if sort_desc else column.asc().nulls_last()
    else:
        ordering = column.desc() if sort_desc else column.asc()

    order_by = [ordering]
    if sort_field != "id":
        order_by.append(Doctor.id.asc())
    return statement.order_by(*order_by)


async def list_doctors(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    sort_field: str,
    sort_desc: bool,
    doctor_id: int | None = None,
) -> tuple[list[Doctor], int]:
    """Return one page of doctors plus the total matching rows.

    `doctor_id` applies the doctor profile ownership filter (rule 6); `None`
    omits it (ADMIN view).
    """
    conditions = _list_conditions(search=search, is_active=is_active, doctor_id=doctor_id)

    total_result = await session.execute(select(func.count(Doctor.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(Doctor).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total
