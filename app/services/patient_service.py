"""Patient management services."""

from __future__ import annotations

import structlog
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.models.patient import Patient
from app.models.user import User
from app.repositories import patient_repository
from app.schemas.patient import (
    CARNET_REQUIRED_MESSAGE,
    CEDULA_FORMAT_MESSAGE,
    PATIENT_TYPE_MESSAGE,
    SORT_FIELDS,
    PatientCreate,
    PatientListResponse,
    PatientRead,
    PatientUpdate,
)

logger = structlog.get_logger(__name__)

PATIENT_NOT_FOUND_MESSAGE = "El paciente no existe"
PATIENT_EXISTS_MESSAGE = "El paciente ya existe"
CARNET_EXISTS_MESSAGE = "El carnet ya está registrado"
INVALID_DATA_MESSAGE = "Datos inválidos"

UNIQUE_CEDULA = "uq_patients_cedula"
UNIQUE_CARNET = "uq_patients_carnet"

# Database CHECK constraint mapped to the field-level Spanish message (D-11).
CHECK_ERRORS: dict[str, tuple[str, str]] = {
    "ck_patients_cedula_format": ("cedula", CEDULA_FORMAT_MESSAGE),
    "ck_patients_carnet_required_for_student": ("carnet", CARNET_REQUIRED_MESSAGE),
    "ck_patients_patient_type_allowed": ("patient_type", PATIENT_TYPE_MESSAGE),
}

KNOWN_CONSTRAINTS = (UNIQUE_CEDULA, UNIQUE_CARNET, *CHECK_ERRORS)


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


def _to_read(patient: Patient) -> PatientRead:
    """Build the single patient shape from the ORM entity (D-4)."""
    return PatientRead(
        id=patient.id,
        name=patient.name,
        cedula=patient.cedula,
        carnet=patient.carnet,
        patient_type=patient.patient_type,
        is_active=patient.is_active,
    )


def _touch(patient: Patient) -> None:
    """Refresh `updated_at` with the database wall clock (FEAT-011 D-9).

    The model uses `onupdate=func.now()`, but in PostgreSQL `now()` is the
    transaction start time: inside a long transaction the value would not
    change. `clock_timestamp()` keeps the expected semantics without touching
    the schema or depending on the application clock.
    """
    patient.updated_at = func.clock_timestamp()


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
        return ConflictError(PATIENT_EXISTS_MESSAGE, code="patient_exists")
    if constraint == UNIQUE_CARNET:
        return ConflictError(CARNET_EXISTS_MESSAGE, code="patient_carnet_exists")

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


async def _get_patient_or_404(session: AsyncSession, patient_id: int) -> Patient:
    """Load a patient or raise `patient_not_found`."""
    patient = await patient_repository.get_patient_by_id(session, patient_id)
    if patient is None:
        raise NotFoundError(PATIENT_NOT_FOUND_MESSAGE, code="patient_not_found")
    return patient


async def _ensure_unique_cedula(
    session: AsyncSession,
    cedula: str,
    *,
    exclude_id: int | None = None,
) -> None:
    """Check that `cedula` is not registered yet."""
    existing = await patient_repository.find_patient_by_cedula(
        session,
        cedula,
        exclude_id=exclude_id,
    )
    if existing is not None:
        raise ConflictError(PATIENT_EXISTS_MESSAGE, code="patient_exists")


async def _ensure_unique_carnet(
    session: AsyncSession,
    carnet: str | None,
    *,
    exclude_id: int | None = None,
) -> None:
    """Check that a non-null `carnet` is not registered yet (case-insensitive)."""
    if carnet is None:
        return
    existing = await patient_repository.find_patient_by_carnet(
        session,
        carnet,
        exclude_id=exclude_id,
    )
    if existing is not None:
        raise ConflictError(CARNET_EXISTS_MESSAGE, code="patient_carnet_exists")


async def _ensure_unique(
    session: AsyncSession,
    *,
    cedula: str,
    carnet: str | None,
    exclude_id: int | None = None,
) -> None:
    """Apply the duplicate precedence of rule 24: cedula first, carnet second."""
    await _ensure_unique_cedula(session, cedula, exclude_id=exclude_id)
    await _ensure_unique_carnet(session, carnet, exclude_id=exclude_id)


def _patient_from_payload(patient: Patient, payload: PatientUpdate) -> None:
    """Apply the full replacement of D-1 to an existing entity."""
    patient.name = payload.name
    patient.cedula = payload.cedula
    patient.carnet = payload.carnet
    patient.patient_type = payload.patient_type
    patient.is_active = payload.is_active


async def list_patients(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None = None,
    is_active: bool | None = None,
    sort: str = "name",
) -> PatientListResponse:
    """List patients with pagination, search, state filter and ordering."""
    sort_field, sort_desc = parse_sort(sort)
    normalized_search = search.strip() if search else None
    patients, total = await patient_repository.list_patients(
        session,
        page=page,
        size=size,
        search=normalized_search or None,
        is_active=is_active,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    pages = calculate_pages(total, size)
    return PatientListResponse(
        items=[_to_read(patient) for patient in patients],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


async def get_patient(session: AsyncSession, *, patient_id: int) -> PatientRead:
    """Return a patient by identifier, including inactive ones."""
    patient = await _get_patient_or_404(session, patient_id)
    return _to_read(patient)


async def create_patient(
    session: AsyncSession,
    *,
    actor: User,
    payload: PatientCreate,
) -> PatientRead:
    """Create a patient and return its single shape."""
    await _ensure_unique(session, cedula=payload.cedula, carnet=payload.carnet)

    patient = Patient(
        name=payload.name,
        cedula=payload.cedula,
        carnet=payload.carnet,
        patient_type=payload.patient_type,
        is_active=payload.is_active,
    )
    session.add(patient)
    await _commit(session)

    logger.info("patient.created", patient_id=patient.id, actor_id=actor.id)
    return _to_read(patient)


async def update_patient(
    session: AsyncSession,
    *,
    actor: User,
    patient_id: int,
    payload: PatientUpdate,
) -> PatientRead:
    """Replace the five editable fields of a patient (D-1)."""
    patient = await _get_patient_or_404(session, patient_id)
    await _ensure_unique(
        session,
        cedula=payload.cedula,
        carnet=payload.carnet,
        exclude_id=patient.id,
    )

    was_active = patient.is_active
    _patient_from_payload(patient, payload)
    _touch(patient)
    await _commit(session)

    deactivated = was_active and not patient.is_active
    event = "patient.deactivated" if deactivated else "patient.updated"
    logger.info(event, patient_id=patient.id, actor_id=actor.id)
    return _to_read(patient)


async def deactivate_patient(
    session: AsyncSession,
    *,
    actor: User,
    patient_id: int,
) -> None:
    """Logically deactivate a patient; idempotent when already inactive (rule 33)."""
    patient = await _get_patient_or_404(session, patient_id)
    if not patient.is_active:
        return

    patient.is_active = False
    _touch(patient)
    await _commit(session)

    logger.info("patient.deactivated", patient_id=patient.id, actor_id=actor.id)
