"""Data access for visits and their medication details (FEAT-013)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload

from app.models.doctor import Doctor
from app.models.visit import Visit
from app.models.visit_medication import VisitMedication
from app.repositories.drug_type_repository import escape_like

# Sortable column for each field of the FEAT-013 D-6 catalog.
SORT_COLUMNS: dict[str, InstrumentedAttribute[Any]] = {
    "visited_at": Visit.visited_at,
    "created_at": Visit.created_at,
    "id": Visit.id,
    "status": Visit.status,
}


def _load_options(*, with_medications: bool) -> list[Any]:
    """Eager loading shared by the detail and the full replacement.

    `doctor`, `patient` and `visit_medications` are `lazy="raise"` on the
    models, so every query that serializes a visit loads them explicitly.
    """
    options: list[Any] = [selectinload(Visit.doctor), selectinload(Visit.patient)]
    if with_medications:
        options.append(
            selectinload(Visit.visit_medications).selectinload(VisitMedication.medication)
        )
    return options


async def get_visit_by_id(
    session: AsyncSession,
    visit_id: int,
    *,
    with_medications: bool = False,
) -> Visit | None:
    """Return a visit with its doctor and patient (and details when asked)."""
    statement = (
        select(Visit)
        .options(*_load_options(with_medications=with_medications))
        .where(Visit.id == visit_id)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none()


def _list_conditions(
    *,
    doctor_id: int | None,
    patient_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    medication_id: int | None,
    status: str | None,
    q: str | None,
) -> list[Any]:
    """Build the combinable `AND` filters of the listing (rules 20-24)."""
    conditions: list[Any] = []
    if doctor_id is not None:
        conditions.append(Visit.doctor_id == doctor_id)
    if patient_id is not None:
        conditions.append(Visit.patient_id == patient_id)
    if date_from is not None:
        conditions.append(Visit.visited_at >= date_from)
    if date_to is not None:
        conditions.append(Visit.visited_at <= date_to)
    if medication_id is not None:
        conditions.append(
            Visit.visit_medications.any(VisitMedication.medication_id == medication_id)
        )
    if status is not None:
        conditions.append(Visit.status == status)
    if q:
        pattern = f"%{escape_like(q)}%"
        conditions.append(
            or_(
                Visit.symptoms.ilike(pattern, escape="\\"),
                Visit.recommendations.ilike(pattern, escape="\\"),
            )
        )
    return conditions


def _ordered(
    statement: Select[tuple[Visit]],
    *,
    sort_field: str,
    sort_desc: bool,
) -> Select[tuple[Visit]]:
    """Apply the requested order with `visits.id DESC` as stable tie-breaker."""
    column = SORT_COLUMNS[sort_field]
    ordering = column.desc() if sort_desc else column.asc()

    order_by = [ordering]
    if sort_field != "id":
        order_by.append(Visit.id.desc())
    return statement.order_by(*order_by)


async def summarize_visits(
    session: AsyncSession,
    *,
    doctor_id: int | None,
    patient_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    status: str | None,
) -> tuple[int, list[tuple[int, str, int]]]:
    """Return the total and the per-doctor totals of the report (FEAT-014).

    Reuses `_list_conditions` so the summary applies exactly the same `AND`
    filters as the paginated detail of `list_visits` (FEAT-014 rules 7, 13).
    """
    conditions = _list_conditions(
        doctor_id=doctor_id,
        patient_id=patient_id,
        date_from=date_from,
        date_to=date_to,
        medication_id=None,
        status=status,
        q=None,
    )

    total_result = await session.execute(select(func.count(Visit.id)).where(*conditions))
    total = int(total_result.scalar_one())

    grouped_total = func.count(Visit.id)
    statement = (
        select(Visit.doctor_id, Doctor.name, grouped_total)
        .join(Doctor, Doctor.id == Visit.doctor_id)
        .where(*conditions)
        .group_by(Visit.doctor_id, Doctor.name)
        .order_by(grouped_total.desc(), Doctor.name.asc(), Visit.doctor_id.asc())
    )
    result = await session.execute(statement)
    by_doctor = [(int(row[0]), str(row[1]), int(row[2])) for row in result.all()]
    return total, by_doctor


async def list_visits(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    doctor_id: int | None,
    patient_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    medication_id: int | None,
    status: str | None,
    q: str | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[Visit], int]:
    """Return one page of visits plus the total matching rows.

    `doctor_id` also carries the `MEDICO` ownership filter (rule 20): the same
    parameter combines in `AND` with every other filter.
    """
    conditions = _list_conditions(
        doctor_id=doctor_id,
        patient_id=patient_id,
        date_from=date_from,
        date_to=date_to,
        medication_id=medication_id,
        status=status,
        q=q,
    )

    total_result = await session.execute(select(func.count(Visit.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(Visit).options(*_load_options(with_medications=False)).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total
