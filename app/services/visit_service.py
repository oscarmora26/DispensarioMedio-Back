"""Visit management services (FEAT-013 rules 6-37)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta, timezone

import structlog
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from app.models.doctor import Doctor
from app.models.medication import Medication
from app.models.patient import Patient
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_medication import VisitMedication
from app.repositories import (
    doctor_repository,
    medication_repository,
    patient_repository,
    visit_repository,
)
from app.schemas.visit import (
    DATE_RANGE_MESSAGE,
    DOCTOR_FILTER_FORBIDDEN_MESSAGE,
    DOCTOR_FROM_TOKEN_MESSAGE,
    DOCTOR_INACTIVE_MESSAGE,
    DOCTOR_REQUIRED_MESSAGE,
    DUPLICATE_MEDICATION_MESSAGE,
    INSTRUCTIONS_MAX_LENGTH,
    INSTRUCTIONS_MAX_MESSAGE,
    MEDICATION_INACTIVE_MESSAGE,
    PATIENT_INACTIVE_MESSAGE,
    PATIENT_REQUIRED_MESSAGE,
    QUANTITY_POSITIVE_MESSAGE,
    SORT_FIELDS,
    SYMPTOMS_RANGE_MESSAGE,
    VISITED_AT_FUTURE_MESSAGE,
    VISITED_AT_REQUIRED_MESSAGE,
    VisitAnnulRequest,
    VisitAnnulResponse,
    VisitCreate,
    VisitDoctorRef,
    VisitListItem,
    VisitListResponse,
    VisitMedicationInput,
    VisitMedicationRead,
    VisitPatientRef,
    VisitRead,
    VisitUpdate,
)

logger = structlog.get_logger(__name__)

VISIT_STATUS_REGISTERED = "REGISTRADA"
VISIT_STATUS_ANNULLED = "ANULADA"

VISIT_NOT_FOUND_MESSAGE = "La visita no existe"
VISIT_ANNULLED_MESSAGE = "La visita anulada no se puede editar"
VISIT_ALREADY_ANNULLED_MESSAGE = "La visita ya está anulada"
DOCTOR_NOT_FOUND_MESSAGE = "El médico no existe"
DOCTOR_NOT_ASSOCIATED_MESSAGE = "El usuario no tiene un médico asociado"
PATIENT_NOT_FOUND_MESSAGE = "El paciente no existe"
MEDICATION_NOT_FOUND_MESSAGE = "El medicamento no existe"
INVALID_DATA_MESSAGE = "Datos inválidos"
INVALID_STATUS_MESSAGE = "El estado de la visita no es válido"
INVALID_ANNULMENT_MESSAGE = "Los datos de anulación no son válidos"

# Database constraints of `visits`/`visit_medications` mapped to their error (D-9).
UNIQUE_VISIT_MEDICATION = "uq_visit_medications"
QUANTITY_CHECK = "ck_visit_medications_quantity_positive"
STATUS_CHECK = "ck_visits_status_allowed"
ANNULMENT_CHECK = "ck_visits_annulment_consistency"

CONSTRAINT_ERRORS: dict[str, tuple[str, str]] = {
    UNIQUE_VISIT_MEDICATION: ("medications", DUPLICATE_MEDICATION_MESSAGE),
    QUANTITY_CHECK: ("quantity", QUANTITY_POSITIVE_MESSAGE),
    STATUS_CHECK: ("status", INVALID_STATUS_MESSAGE),
    ANNULMENT_CHECK: ("status", INVALID_ANNULMENT_MESSAGE),
}
KNOWN_CONSTRAINTS = tuple(CONSTRAINT_ERRORS)

# `America/Santo_Domingo` is a fixed UTC-4 offset: the country has no DST and
# `tzdata` is not a dependency of the project (D-3, RNF-12).
SANTO_DOMINGO = timezone(timedelta(hours=-4), "America/Santo_Domingo")


def _invalid(field: str, message: str) -> AppError:
    """Build the 422 field-level error of rules 6-13 and 33."""
    return AppError(
        INVALID_DATA_MESSAGE,
        status_code=422,
        code="validation_error",
        errors=[{"field": field, "message": message}],
    )


def parse_sort(sort: str) -> tuple[str, bool]:
    """Split the `sort` parameter into field name and descending flag (D-6)."""
    sort_desc = sort.startswith("-")
    sort_field = sort[1:] if sort_desc else sort
    if sort_field not in SORT_FIELDS:
        raise _invalid("sort", "Valor de orden no permitido")
    return sort_field, sort_desc


def calculate_pages(total: int, size: int) -> int:
    """Compute the page count; `0` when there are no rows (rule 26)."""
    return (total + size - 1) // size if total else 0


def to_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC, assuming Santo Domingo for naive input (D-3)."""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=SANTO_DOMINGO)
    return value.astimezone(UTC)


def normalize_medications(
    medications: Iterable[VisitMedicationInput] | None,
) -> list[VisitMedicationInput]:
    """Validate the medication list of rule 12 without touching the database."""
    items = list(medications or [])
    seen: set[int] = set()
    for item in items:
        if item.quantity is not None and item.quantity <= 0:
            raise _invalid("quantity", QUANTITY_POSITIVE_MESSAGE)
        instructions = item.instructions
        if instructions is not None and len(instructions) > INSTRUCTIONS_MAX_LENGTH:
            raise _invalid("instructions", INSTRUCTIONS_MAX_MESSAGE)
        if item.medication_id in seen:
            raise _invalid("medications", DUPLICATE_MEDICATION_MESSAGE)
        seen.add(item.medication_id)
    return items


def translate_integrity_error(exc: IntegrityError) -> AppError | None:
    """Translate a known constraint violation into its domain error (D-9)."""
    constraint = getattr(exc.orig, "constraint_name", None)
    if not (isinstance(constraint, str) and constraint in CONSTRAINT_ERRORS):
        haystack = f"{exc.orig} {exc}"
        constraint = next((name for name in KNOWN_CONSTRAINTS if name in haystack), None)

    mapped = CONSTRAINT_ERRORS.get(constraint) if isinstance(constraint, str) else None
    if mapped is None:
        return None
    field, message = mapped
    return _invalid(field, message)


async def _commit(session: AsyncSession) -> None:
    """Commit the transaction translating known constraint violations (rule 18)."""
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        error = translate_integrity_error(exc)
        if error is not None:
            raise error from None
        raise


def _touch(visit: Visit) -> None:
    """Refresh `updated_at` with the database wall clock (D-11)."""
    visit.updated_at = func.clock_timestamp()


async def _reload_timestamps(session: AsyncSession, visit: Visit, *names: str) -> None:
    """Reload server-generated columns assigned as SQL expressions (D-11)."""
    await session.refresh(visit, attribute_names=list(names))


def _visited_at_or_422(value: datetime | None, *, now: datetime | None = None) -> datetime:
    """Validate presence and "not future" of `visited_at` (rule 9)."""
    if value is None:
        raise _invalid("visited_at", VISITED_AT_REQUIRED_MESSAGE)
    visited_at = to_utc(value)
    if visited_at > (now or datetime.now(UTC)):
        raise _invalid("visited_at", VISITED_AT_FUTURE_MESSAGE)
    return visited_at


def _symptoms_or_422(value: str | None) -> str:
    """Validate presence of `symptoms`; length is checked by the schema (rule 10)."""
    if value is None:
        raise _invalid("symptoms", SYMPTOMS_RANGE_MESSAGE)
    return value


async def _resolve_doctor(
    session: AsyncSession,
    *,
    actor: User,
    doctor_id: int | None,
) -> Doctor:
    """Resolve the visit doctor from the token (`MEDICO`) or the body (`ADMIN`)."""
    if actor.role.name == "MEDICO":
        if doctor_id is not None and doctor_id != actor.doctor_id:
            raise _invalid("doctor_id", DOCTOR_FROM_TOKEN_MESSAGE)
        if actor.doctor_id is None:
            raise ForbiddenError(DOCTOR_NOT_ASSOCIATED_MESSAGE, code="doctor_not_associated")
        doctor_id = actor.doctor_id
    elif doctor_id is None:
        raise _invalid("doctor_id", DOCTOR_REQUIRED_MESSAGE)

    doctor = await doctor_repository.get_doctor_by_id(session, doctor_id)
    if doctor is None:
        raise NotFoundError(DOCTOR_NOT_FOUND_MESSAGE, code="doctor_not_found")
    if not doctor.is_active:
        raise _invalid("doctor_id", DOCTOR_INACTIVE_MESSAGE)
    return doctor


async def _resolve_patient(session: AsyncSession, patient_id: int | None) -> Patient:
    """Load the patient of the visit and enforce existence and state (rule 8)."""
    if patient_id is None:
        raise _invalid("patient_id", PATIENT_REQUIRED_MESSAGE)

    patient = await patient_repository.get_patient_by_id(session, patient_id)
    if patient is None:
        raise NotFoundError(PATIENT_NOT_FOUND_MESSAGE, code="patient_not_found")
    if not patient.is_active:
        raise _invalid("patient_id", PATIENT_INACTIVE_MESSAGE)
    return patient


async def _resolve_medications(
    session: AsyncSession,
    medications: Iterable[VisitMedicationInput],
) -> dict[int, Medication]:
    """Load every referenced medication, enforcing existence and state (rule 13)."""
    resolved: dict[int, Medication] = {}
    for item in medications:
        medication = await medication_repository.get_medication_by_id(session, item.medication_id)
        if medication is None:
            raise NotFoundError(MEDICATION_NOT_FOUND_MESSAGE, code="medication_not_found")
        if not medication.is_active:
            raise _invalid("medications", MEDICATION_INACTIVE_MESSAGE)
        resolved[medication.id] = medication
    return resolved


def _medication_details(
    medications: Iterable[VisitMedicationInput],
) -> list[VisitMedication]:
    """Build the `visit_medications` rows of a visit (rules 12 and 17)."""
    return [
        VisitMedication(
            medication_id=item.medication_id,
            quantity=item.quantity,
            instructions=item.instructions,
        )
        for item in medications
    ]


def _doctor_ref(doctor: Doctor) -> VisitDoctorRef:
    """Build the nested doctor reference (D-7, D-8)."""
    return VisitDoctorRef(id=doctor.id, name=doctor.name)


def _patient_ref(patient: Patient) -> VisitPatientRef:
    """Build the nested patient reference (D-7, D-8)."""
    return VisitPatientRef(id=patient.id, name=patient.name, cedula=patient.cedula)


def _medication_reads(
    visit: Visit,
    medications: Mapping[int, Medication],
) -> list[VisitMedicationRead]:
    """Build the medication details of the detail shape (D-7)."""
    reads: list[VisitMedicationRead] = []
    for detail in visit.visit_medications:
        medication = medications[detail.medication_id]
        reads.append(
            VisitMedicationRead(
                medication_id=detail.medication_id,
                description=medication.description,
                dose=medication.dose,
                quantity=detail.quantity,
                instructions=detail.instructions,
            )
        )
    return reads


def _to_read(
    visit: Visit,
    *,
    doctor: Doctor,
    patient: Patient,
    medications: Mapping[int, Medication],
) -> VisitRead:
    """Build the detail shape of D-7 from the ORM entities."""
    return VisitRead(
        id=visit.id,
        doctor=_doctor_ref(doctor),
        patient=_patient_ref(patient),
        visited_at=visit.visited_at,
        symptoms=visit.symptoms,
        recommendations=visit.recommendations,
        status=visit.status,
        created_by=visit.created_by,
        created_at=visit.created_at,
        updated_at=visit.updated_at,
        medications=_medication_reads(visit, medications),
        annulled_at=visit.annulled_at,
        annulled_by=visit.annulled_by,
        annulment_reason=visit.annulment_reason,
    )


def _to_list_item(visit: Visit) -> VisitListItem:
    """Build the summary shape of D-8 from the ORM entity."""
    return VisitListItem(
        id=visit.id,
        doctor=_doctor_ref(visit.doctor),
        patient=_patient_ref(visit.patient),
        visited_at=visit.visited_at,
        symptoms=visit.symptoms,
        status=visit.status,
        created_by=visit.created_by,
    )


async def list_visits(
    session: AsyncSession,
    *,
    actor: User,
    page: int,
    size: int,
    doctor_id: int | None = None,
    patient_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    medication_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    sort: str = "-visited_at",
) -> VisitListResponse:
    """List visits with filters, ordering and the `MEDICO` ownership rule (rules 19-26)."""
    sort_field, sort_desc = parse_sort(sort)
    is_medico = actor.role.name == "MEDICO"

    if is_medico and doctor_id is not None:
        raise _invalid("doctor_id", DOCTOR_FILTER_FORBIDDEN_MESSAGE)

    range_start = to_utc(date_from) if date_from is not None else None
    range_end = to_utc(date_to) if date_to is not None else None
    if range_start is not None and range_end is not None and range_start > range_end:
        raise _invalid("date_from", DATE_RANGE_MESSAGE)

    if is_medico and actor.doctor_id is None:
        return VisitListResponse(items=[], total=0, page=page, size=size, pages=0)

    visits, total = await visit_repository.list_visits(
        session,
        page=page,
        size=size,
        doctor_id=actor.doctor_id if is_medico else doctor_id,
        patient_id=patient_id,
        date_from=range_start,
        date_to=range_end,
        medication_id=medication_id,
        status=status,
        q=q.strip() if q else None,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    return VisitListResponse(
        items=[_to_list_item(visit) for visit in visits],
        total=total,
        page=page,
        size=size,
        pages=calculate_pages(total, size),
    )


async def get_visit(session: AsyncSession, *, actor: User, visit_id: int) -> VisitRead:
    """Return a visit detail, including annulled ones (rules 27 and 28)."""
    visit = await visit_repository.get_visit_by_id(session, visit_id, with_medications=True)
    if visit is None:
        raise NotFoundError(VISIT_NOT_FOUND_MESSAGE, code="visit_not_found")

    if actor.role.name == "MEDICO" and visit.doctor_id != actor.doctor_id:
        raise NotFoundError(VISIT_NOT_FOUND_MESSAGE, code="visit_not_found")

    medications = {detail.medication_id: detail.medication for detail in visit.visit_medications}
    return _to_read(
        visit,
        doctor=visit.doctor,
        patient=visit.patient,
        medications=medications,
    )


async def create_visit(
    session: AsyncSession,
    *,
    actor: User,
    payload: VisitCreate,
) -> VisitRead:
    """Register a visit for the actor's doctor (`MEDICO`) or the body's (`ADMIN`)."""
    doctor = await _resolve_doctor(session, actor=actor, doctor_id=payload.doctor_id)
    patient = await _resolve_patient(session, payload.patient_id)
    visited_at = _visited_at_or_422(payload.visited_at)
    symptoms = _symptoms_or_422(payload.symptoms)
    medication_inputs = normalize_medications(payload.medications)
    medications = await _resolve_medications(session, medication_inputs)

    visit = Visit(
        doctor_id=doctor.id,
        patient_id=patient.id,
        visited_at=visited_at,
        symptoms=symptoms,
        recommendations=payload.recommendations,
        status=VISIT_STATUS_REGISTERED,
        created_by=actor.id,
    )
    visit.visit_medications = _medication_details(medication_inputs)
    session.add(visit)
    await _commit(session)
    await _reload_timestamps(session, visit, "created_at", "updated_at")

    logger.info("visit.created", visit_id=visit.id, actor_id=actor.id)
    return _to_read(visit, doctor=doctor, patient=patient, medications=medications)


async def update_visit(
    session: AsyncSession,
    *,
    actor: User,
    visit_id: int,
    payload: VisitUpdate,
) -> VisitRead:
    """Replace the editable fields and the medication list of a visit (rules 29-32)."""
    visit = await visit_repository.get_visit_by_id(session, visit_id, with_medications=True)
    if visit is None:
        raise NotFoundError(VISIT_NOT_FOUND_MESSAGE, code="visit_not_found")
    if visit.status == VISIT_STATUS_ANNULLED:
        raise ConflictError(VISIT_ANNULLED_MESSAGE, code="visit_annulled")

    doctor = await _resolve_doctor(session, actor=actor, doctor_id=payload.doctor_id)
    patient = await _resolve_patient(session, payload.patient_id)
    visited_at = _visited_at_or_422(payload.visited_at)
    symptoms = _symptoms_or_422(payload.symptoms)
    medication_inputs = normalize_medications(payload.medications)
    medications = await _resolve_medications(session, medication_inputs)

    visit.doctor_id = doctor.id
    visit.patient_id = patient.id
    visit.visited_at = visited_at
    visit.symptoms = symptoms
    visit.recommendations = payload.recommendations
    if visit.visit_medications:
        # The new list may reuse a medication already present in the visit, and
        # `uq_visit_medications` is checked per statement: flush the orphan
        # deletions before inserting the replacement rows (rule 31, D-10).
        visit.visit_medications = []
        await session.flush()
    visit.visit_medications = _medication_details(medication_inputs)
    _touch(visit)
    await _commit(session)
    await _reload_timestamps(session, visit, "updated_at")

    logger.info("visit.updated", visit_id=visit.id, actor_id=actor.id)
    return _to_read(visit, doctor=doctor, patient=patient, medications=medications)


async def annul_visit(
    session: AsyncSession,
    *,
    actor: User,
    visit_id: int,
    payload: VisitAnnulRequest,
) -> VisitAnnulResponse:
    """Annull a visit once, recording actor, timestamp and reason (rules 33-36)."""
    visit = await visit_repository.get_visit_by_id(session, visit_id)
    if visit is None:
        raise NotFoundError(VISIT_NOT_FOUND_MESSAGE, code="visit_not_found")
    if visit.status == VISIT_STATUS_ANNULLED:
        raise ConflictError(VISIT_ALREADY_ANNULLED_MESSAGE, code="visit_already_annulled")

    visit.status = VISIT_STATUS_ANNULLED
    visit.annulled_by = actor.id
    visit.annulled_at = func.clock_timestamp()
    visit.annulment_reason = payload.reason
    _touch(visit)
    await _commit(session)
    await _reload_timestamps(session, visit, "annulled_at", "updated_at")

    logger.info("visit.annulled", visit_id=visit.id, actor_id=actor.id)
    return VisitAnnulResponse(
        id=visit.id,
        status=visit.status,
        annulled_at=visit.annulled_at,
        annulled_by=visit.annulled_by,
        annulment_reason=visit.annulment_reason,
    )
