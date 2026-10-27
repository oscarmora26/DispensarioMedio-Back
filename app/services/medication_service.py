"""Servicios de gestion de medicamentos."""

from __future__ import annotations

import structlog
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.models.medication import Medication
from app.models.user import User
from app.repositories import (
    brand_repository,
    cell_repository,
    drug_type_repository,
    medication_repository,
)
from app.schemas.medication import (
    SORT_FIELDS,
    MedicationCreate,
    MedicationListResponse,
    MedicationRead,
    MedicationUpdate,
)

logger = structlog.get_logger(__name__)

MEDICATION_NOT_FOUND_MESSAGE = "El medicamento no existe"
MEDICATION_EXISTS_MESSAGE = "El medicamento ya existe"

DRUG_TYPE_NOT_FOUND_MESSAGE = "El tipo de fármaco no existe"
BRAND_NOT_FOUND_MESSAGE = "La marca no existe"
CELL_NOT_FOUND_MESSAGE = "La celda no existe"

DRUG_TYPE_INACTIVE_MESSAGE = "El tipo de fármaco está inactivo"
BRAND_INACTIVE_MESSAGE = "La marca está inactiva"
CELL_INACTIVE_MESSAGE = "La celda está inactiva"

UNIQUE_CONSTRAINT = "uq_medications_identity"


def parse_sort(sort: str) -> tuple[str, bool]:
    """Separa el campo de orden y el sentido descendente del parametro `sort`."""
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
    """Calcula el numero de paginas; `0` cuando no hay filas."""
    return (total + size - 1) // size if total else 0


def _to_read(medication: Medication) -> MedicationRead:
    """Construye la forma unica del medicamento a partir de la entidad ORM."""
    return MedicationRead(
        id=medication.id,
        description=medication.description,
        drug_type_id=medication.drug_type_id,
        brand_id=medication.brand_id,
        cell_id=medication.cell_id,
        dose=medication.dose,
        is_active=medication.is_active,
    )


def _touch(medication: Medication) -> None:
    """Refresca `updated_at` con la hora de pared de la base (FEAT-010 D-9).

    El modelo usa `onupdate=func.now()`, pero en PostgreSQL `now()` es la hora
    de inicio de la transaccion: dentro de una transaccion larga el valor no
    cambiaria. `clock_timestamp()` mantiene la semantica esperada sin tocar el
    esquema ni depender del reloj de la aplicacion.
    """
    medication.updated_at = func.clock_timestamp()


def _integrity_constraint(exc: IntegrityError) -> str | None:
    """Extrae el nombre de la constraint violada de un `IntegrityError`."""
    constraint = getattr(exc.orig, "constraint_name", None)
    if isinstance(constraint, str) and constraint:
        return constraint

    haystack = f"{exc.orig} {exc}"
    if UNIQUE_CONSTRAINT in haystack:
        return UNIQUE_CONSTRAINT
    return None


async def _commit(session: AsyncSession) -> None:
    """Confirma la transaccion traduciendo la violacion de unicidad.

    Antes de responder se deshace el savepoint para que la sesion quede usable.
    """
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if _integrity_constraint(exc) == UNIQUE_CONSTRAINT:
            raise ConflictError(MEDICATION_EXISTS_MESSAGE, code="medication_exists") from None
        raise


async def _get_medication_or_404(session: AsyncSession, medication_id: int) -> Medication:
    """Carga un medicamento o lanza `medication_not_found`."""
    medication = await medication_repository.get_medication_by_id(session, medication_id)
    if medication is None:
        raise NotFoundError(MEDICATION_NOT_FOUND_MESSAGE, code="medication_not_found")
    return medication


async def _ensure_unique(
    session: AsyncSession,
    description: str,
    brand_id: int,
    dose: str,
    *,
    exclude_id: int | None = None,
) -> None:
    """Comprueba que la identidad `(description, brand_id, dose)` no exista ya."""
    existing = await medication_repository.find_medication_by_identity(
        session,
        description,
        brand_id,
        dose,
        exclude_id=exclude_id,
    )
    if existing is not None:
        raise ConflictError(MEDICATION_EXISTS_MESSAGE, code="medication_exists")


async def _validate_fks(
    session: AsyncSession,
    *,
    drug_type_id: int,
    brand_id: int,
    cell_id: int,
) -> None:
    """Valida que los FK existan (404 del catalogo) y esten activos (422).

    Precedencia de la regla 23: primero inexistencia en orden
    `drug_type_id`, `brand_id`, `cell_id`; despues inactividad en el mismo
    orden.
    """
    drug_type = await drug_type_repository.get_drug_type_by_id(session, drug_type_id)
    if drug_type is None:
        raise NotFoundError(DRUG_TYPE_NOT_FOUND_MESSAGE, code="drug_type_not_found")

    brand = await brand_repository.get_brand_by_id(session, brand_id)
    if brand is None:
        raise NotFoundError(BRAND_NOT_FOUND_MESSAGE, code="brand_not_found")

    cell = await cell_repository.get_cell_by_id(session, cell_id)
    if cell is None:
        raise NotFoundError(CELL_NOT_FOUND_MESSAGE, code="cell_not_found")

    if not drug_type.is_active:
        raise AppError(
            DRUG_TYPE_INACTIVE_MESSAGE,
            status_code=422,
            code="validation_error",
            errors=[{"field": "drug_type_id", "message": DRUG_TYPE_INACTIVE_MESSAGE}],
        )
    if not brand.is_active:
        raise AppError(
            BRAND_INACTIVE_MESSAGE,
            status_code=422,
            code="validation_error",
            errors=[{"field": "brand_id", "message": BRAND_INACTIVE_MESSAGE}],
        )
    if not cell.is_active:
        raise AppError(
            CELL_INACTIVE_MESSAGE,
            status_code=422,
            code="validation_error",
            errors=[{"field": "cell_id", "message": CELL_INACTIVE_MESSAGE}],
        )


async def list_medications(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None = None,
    is_active: bool | None = None,
    drug_type_id: int | None = None,
    brand_id: int | None = None,
    cell_id: int | None = None,
    sort: str = "description",
) -> MedicationListResponse:
    """Lista medicamentos con paginacion, busqueda, filtros de FK y orden."""
    sort_field, sort_desc = parse_sort(sort)
    normalized_search = search.strip() if search else None
    medications, total = await medication_repository.list_medications(
        session,
        page=page,
        size=size,
        search=normalized_search or None,
        is_active=is_active,
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        cell_id=cell_id,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    pages = calculate_pages(total, size)
    return MedicationListResponse(
        items=[_to_read(medication) for medication in medications],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


async def get_medication(session: AsyncSession, *, medication_id: int) -> MedicationRead:
    """Devuelve un medicamento por identificador, incluido si esta inactivo."""
    medication = await _get_medication_or_404(session, medication_id)
    return _to_read(medication)


async def create_medication(
    session: AsyncSession,
    *,
    actor: User,
    payload: MedicationCreate,
) -> MedicationRead:
    """Crea un medicamento validando FK y unicidad compuesta."""
    await _validate_fks(
        session,
        drug_type_id=payload.drug_type_id,
        brand_id=payload.brand_id,
        cell_id=payload.cell_id,
    )
    await _ensure_unique(session, payload.description, payload.brand_id, payload.dose)

    medication = Medication(
        description=payload.description,
        drug_type_id=payload.drug_type_id,
        brand_id=payload.brand_id,
        cell_id=payload.cell_id,
        dose=payload.dose,
        is_active=payload.is_active,
    )
    session.add(medication)
    await _commit(session)

    logger.info("medication.created", medication_id=medication.id, actor_id=actor.id)
    return _to_read(medication)


async def update_medication(
    session: AsyncSession,
    *,
    actor: User,
    medication_id: int,
    payload: MedicationUpdate,
) -> MedicationRead:
    """Reemplaza los campos editables de un medicamento (FEAT-010 D-1)."""
    medication = await _get_medication_or_404(session, medication_id)
    await _validate_fks(
        session,
        drug_type_id=payload.drug_type_id,
        brand_id=payload.brand_id,
        cell_id=payload.cell_id,
    )
    await _ensure_unique(
        session,
        payload.description,
        payload.brand_id,
        payload.dose,
        exclude_id=medication.id,
    )

    was_active = medication.is_active
    medication.description = payload.description
    medication.drug_type_id = payload.drug_type_id
    medication.brand_id = payload.brand_id
    medication.cell_id = payload.cell_id
    medication.dose = payload.dose
    medication.is_active = payload.is_active
    _touch(medication)
    await _commit(session)

    deactivated = was_active and not medication.is_active
    event = "medication.deactivated" if deactivated else "medication.updated"
    logger.info(event, medication_id=medication.id, actor_id=actor.id)
    return _to_read(medication)


async def deactivate_medication(
    session: AsyncSession,
    *,
    actor: User,
    medication_id: int,
) -> None:
    """Desactiva logicamente un medicamento; es idempotente si ya estaba inactivo."""
    medication = await _get_medication_or_404(session, medication_id)

    if not medication.is_active:
        return

    medication.is_active = False
    _touch(medication)
    await _commit(session)
    logger.info("medication.deactivated", medication_id=medication.id, actor_id=actor.id)
