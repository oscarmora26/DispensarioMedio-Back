"""Doctor management services."""

from __future__ import annotations

import structlog
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.models.doctor import Doctor
from app.models.user import User
from app.repositories import doctor_repository
from app.schemas.doctor import (
    CEDULA_FORMAT_MESSAGE,
    SORT_FIELDS,
    DoctorCreate,
    DoctorListResponse,
    DoctorRead,
    DoctorUpdate,
)

logger = structlog.get_logger(__name__)

DOCTOR_NOT_FOUND_MESSAGE = "El médico no existe"
DOCTOR_EXISTS_MESSAGE = "El médico ya existe"
DOCTOR_HAS_VISITS_MESSAGE = "El médico tiene visitas registradas"
INVALID_DATA_MESSAGE = "Datos inválidos"

UNIQUE_CEDULA = "uq_doctors_cedula"

# Database CHECK constraint mapped to the field-level Spanish message (D-9).
CHECK_ERRORS: dict[str, tuple[str, str]] = {
    "ck_doctors_cedula_format": ("cedula", CEDULA_FORMAT_MESSAGE),
}

KNOWN_CONSTRAINTS = (UNIQUE_CEDULA, *CHECK_ERRORS)


def parse_sort(sort: str) -> tuple[str, bool]:
    """Split the `sort` parameter into field name and descending flag."""
    sort_desc = sort.startswith("-")
    sort_field = sort[1:] if sort_desc else sort
    if sort_field not in SORT_FIELDS:
        raise AppError(
            "Parámetro de orden inválido",
            status_code=422,
            code="validation_error",
            errors=[{"field": "sort", "message": "Valor de orden no permitido"}],
        )
    return sort_field, sort_desc


def calculate_pages(total: int, size: int) -> int:
    """Compute the page count; `0` when there are no rows."""
    return (total + size - 1) // size if total else 0


def _to_read(doctor: Doctor) -> DoctorRead:
    """Build the single doctor shape from the ORM entity (D-2)."""
    return DoctorRead(
        id=doctor.id,
        name=doctor.name,
        cedula=doctor.cedula,
        lab_shift=doctor.lab_shift,
        specialty=doctor.specialty,
        is_active=doctor.is_active,
    )


def _touch(doctor: Doctor) -> None:
    """Refresh `updated_at` with the database wall clock (FEAT-012 D-12).

    The model uses `onupdate=func.now()`, but in PostgreSQL `now()` is the
    transaction start time: inside a long transaction the value would not
    change. `clock_timestamp()` keeps the expected semantics.
    """
    doctor.updated_at = func.clock_timestamp()


def _integrity_constraint(exc: IntegrityError) -> str | None:
    """Extract the violated constraint name from an `IntegrityError`."""
    constraint = getattr(exc.orig, "constraint_name", None)
    if isinstance(constraint, str) and constraint in KNOWN_CONSTRAINTS:
        return constraint

    haystack = f"{exc.orig} {exc}"
    for name in KNOWN_CONSTRAINTS:
        if name in haystack:
            return name
    return None


def _integrity_error(exc: IntegrityError) -> AppError | None:
    """Translate a known constraint violation into its domain error."""
    constraint = _integrity_constraint(exc)
    if constraint == UNIQUE_CEDULA:
        return ConflictError(DOCTOR_EXISTS_MESSAGE, code="doctor_exists")

    check = CHECK_ERRORS.get(constraint) if constraint is not None else None
    if check is not None:
        field, message = check
        return AppError(
            INVALID_DATA_MESSAGE,
            status_code=422,
            code="validation_error",
            errors=[{"field": field, "message": message}],
        )
    return None


async def _commit(session: AsyncSession) -> None:
    """Commit the transaction translating known constraint violations."""
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        error = _integrity_error(exc)
        if error is not None:
            raise error from None
        raise


async def _get_doctor_or_404(session: AsyncSession, doctor_id: int) -> Doctor:
    """Load a doctor or raise `doctor_not_found`."""
    doctor = await doctor_repository.get_doctor_by_id(session, doctor_id)
    if doctor is None:
        raise NotFoundError(DOCTOR_NOT_FOUND_MESSAGE, code="doctor_not_found")
    return doctor


async def _ensure_unique_cedula(
    session: AsyncSession,
    cedula: str,
    *,
    exclude_id: int | None = None,
) -> None:
    """Check that `cedula` is not registered yet."""
    existing = await doctor_repository.find_doctor_by_cedula(
        session,
        cedula,
        exclude_id=exclude_id,
    )
    if existing is not None:
        raise ConflictError(DOCTOR_EXISTS_MESSAGE, code="doctor_exists")


def _doctor_from_payload(doctor: Doctor, payload: DoctorUpdate) -> None:
    """Apply the full replacement of D-1 to an existing entity."""
    doctor.name = payload.name
    doctor.cedula = payload.cedula
    doctor.lab_shift = payload.lab_shift
    doctor.specialty = payload.specialty
    doctor.is_active = payload.is_active


async def list_doctors(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None = None,
    is_active: bool | None = None,
    sort: str = "name",
    viewer_is_medico: bool = False,
    viewer_doctor_id: int | None = None,
) -> DoctorListResponse:
    """List doctors with pagination, search, state filter and ordering.

    A `MEDICO` viewer only sees their own profile (rule 6): the ownership
    filter combines in `AND` with the requested filters, and a `MEDICO`
    without an associated doctor gets an empty page.
    """
    sort_field, sort_desc = parse_sort(sort)
    normalized_search = search.strip() if search else None

    if viewer_is_medico and viewer_doctor_id is None:
        return DoctorListResponse(items=[], total=0, page=page, size=size, pages=0)

    doctors, total = await doctor_repository.list_doctors(
        session,
        page=page,
        size=size,
        search=normalized_search or None,
        is_active=is_active,
        sort_field=sort_field,
        sort_desc=sort_desc,
        doctor_id=viewer_doctor_id if viewer_is_medico else None,
    )
    pages = calculate_pages(total, size)
    return DoctorListResponse(
        items=[_to_read(doctor) for doctor in doctors],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


async def get_doctor(
    session: AsyncSession,
    *,
    doctor_id: int,
    viewer_is_medico: bool = False,
    viewer_doctor_id: int | None = None,
) -> DoctorRead:
    """Return a doctor by identifier, including inactive ones.

    A `MEDICO` viewer only reaches their own `doctor_id`; any other
    identifier answers `404` to avoid leaking the existence of colleagues
    (rule 6, D-6).
    """
    if viewer_is_medico and doctor_id != viewer_doctor_id:
        raise NotFoundError(DOCTOR_NOT_FOUND_MESSAGE, code="doctor_not_found")
    doctor = await _get_doctor_or_404(session, doctor_id)
    return _to_read(doctor)


async def create_doctor(
    session: AsyncSession,
    *,
    actor: User,
    payload: DoctorCreate,
) -> DoctorRead:
    """Create a doctor and return its single shape."""
    await _ensure_unique_cedula(session, cedula=payload.cedula)

    doctor = Doctor(
        name=payload.name,
        cedula=payload.cedula,
        lab_shift=payload.lab_shift,
        specialty=payload.specialty,
        is_active=payload.is_active,
    )
    session.add(doctor)
    await _commit(session)

    logger.info("doctor.created", doctor_id=doctor.id, actor_id=actor.id)
    return _to_read(doctor)


async def update_doctor(
    session: AsyncSession,
    *,
    actor: User,
    doctor_id: int,
    payload: DoctorUpdate,
) -> DoctorRead:
    """Replace the five editable fields of a doctor (D-1)."""
    doctor = await _get_doctor_or_404(session, doctor_id)
    await _ensure_unique_cedula(session, cedula=payload.cedula, exclude_id=doctor.id)

    was_active = doctor.is_active
    _doctor_from_payload(doctor, payload)
    _touch(doctor)
    await _commit(session)

    deactivated = was_active and not doctor.is_active
    event = "doctor.deactivated" if deactivated else "doctor.updated"
    logger.info(event, doctor_id=doctor.id, actor_id=actor.id)
    return _to_read(doctor)


async def deactivate_doctor(
    session: AsyncSession,
    *,
    actor: User,
    doctor_id: int,
) -> None:
    """Logically deactivate a doctor; blocked when visits exist (rule 33).

    The visit check precedes idempotency on purpose (rule 34): retrying the
    deactivation of a doctor that already has visits still answers `409`
    instead of hiding the conflict behind a `204`.
    """
    doctor = await _get_doctor_or_404(session, doctor_id)

    visits = await doctor_repository.count_visits_by_doctor(session, doctor.id)
    if visits > 0:
        raise ConflictError(DOCTOR_HAS_VISITS_MESSAGE, code="doctor_has_visits")

    if not doctor.is_active:
        return

    doctor.is_active = False
    _touch(doctor)
    await _commit(session)

    logger.info("doctor.deactivated", doctor_id=doctor.id, actor_id=actor.id)
