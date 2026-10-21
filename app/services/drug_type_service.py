"""Servicios de gestion de tipos de farmacos."""

from __future__ import annotations

import structlog
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.drug_type import DrugType
from app.models.user import User
from app.repositories import drug_type_repository
from app.schemas.drug_type import (
    SORT_FIELDS,
    DrugTypeCreate,
    DrugTypeListResponse,
    DrugTypeRead,
    DrugTypeUpdate,
)

logger = structlog.get_logger(__name__)

DRUG_TYPE_NOT_FOUND_MESSAGE = "El tipo de fármaco no existe"
DRUG_TYPE_EXISTS_MESSAGE = "El tipo de fármaco ya existe"

UNIQUE_CONSTRAINT = "uq_drug_types_description"


def parse_sort(sort: str) -> tuple[str, bool]:
    """Separa el campo de orden y el sentido descendente del parametro `sort`."""
    sort_desc = sort.startswith("-")
    sort_field = sort[1:] if sort_desc else sort
    if sort_field not in SORT_FIELDS:
        from app.core.exceptions import AppError

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


def _to_read(drug_type: DrugType) -> DrugTypeRead:
    """Construye la forma unica del tipo a partir de la entidad ORM."""
    return DrugTypeRead(
        id=drug_type.id,
        description=drug_type.description,
        is_active=drug_type.is_active,
    )


def _touch(drug_type: DrugType) -> None:
    """Refresca `updated_at` con la hora de pared de la base (FEAT-007 D-9).

    El modelo usa `onupdate=func.now()`, pero en PostgreSQL `now()` es la hora
    de inicio de la transaccion: dentro de una transaccion larga el valor no
    cambiaria. `clock_timestamp()` mantiene la semantica esperada sin tocar el
    esquema ni depender del reloj de la aplicacion.
    """
    drug_type.updated_at = func.clock_timestamp()


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
            raise ConflictError(DRUG_TYPE_EXISTS_MESSAGE, code="drug_type_exists") from None
        raise


async def _get_drug_type_or_404(session: AsyncSession, drug_type_id: int) -> DrugType:
    """Carga un tipo de farmaco o lanza `drug_type_not_found`."""
    drug_type = await drug_type_repository.get_drug_type_by_id(session, drug_type_id)
    if drug_type is None:
        raise NotFoundError(DRUG_TYPE_NOT_FOUND_MESSAGE, code="drug_type_not_found")
    return drug_type


async def _ensure_unique(
    session: AsyncSession,
    description: str,
    *,
    exclude_id: int | None = None,
) -> None:
    """Comprueba que `description` no exista ya (CITEXT, case-insensitive)."""
    existing = await drug_type_repository.find_drug_type_by_description(
        session,
        description,
        exclude_id=exclude_id,
    )
    if existing is not None:
        raise ConflictError(DRUG_TYPE_EXISTS_MESSAGE, code="drug_type_exists")


async def list_drug_types(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None = None,
    is_active: bool | None = None,
    sort: str = "description",
) -> DrugTypeListResponse:
    """Lista tipos de farmacos con paginacion, busqueda, filtro de estado y orden."""
    sort_field, sort_desc = parse_sort(sort)
    normalized_search = search.strip() if search else None
    drug_types, total = await drug_type_repository.list_drug_types(
        session,
        page=page,
        size=size,
        search=normalized_search or None,
        is_active=is_active,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    pages = calculate_pages(total, size)
    return DrugTypeListResponse(
        items=[_to_read(drug_type) for drug_type in drug_types],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


async def get_drug_type(session: AsyncSession, *, drug_type_id: int) -> DrugTypeRead:
    """Devuelve un tipo de farmaco por identificador, incluido si esta inactivo."""
    drug_type = await _get_drug_type_or_404(session, drug_type_id)
    return _to_read(drug_type)


async def create_drug_type(
    session: AsyncSession,
    *,
    actor: User,
    payload: DrugTypeCreate,
) -> DrugTypeRead:
    """Crea un tipo de farmaco y devuelve su forma unica."""
    await _ensure_unique(session, payload.description)

    drug_type = DrugType(
        description=payload.description,
        is_active=payload.is_active,
    )
    session.add(drug_type)
    await _commit(session)

    logger.info("drug_type.created", drug_type_id=drug_type.id, actor_id=actor.id)
    return _to_read(drug_type)


async def update_drug_type(
    session: AsyncSession,
    *,
    actor: User,
    drug_type_id: int,
    payload: DrugTypeUpdate,
) -> DrugTypeRead:
    """Reemplaza `description` e `is_active` de un tipo (FEAT-007 D-1)."""
    drug_type = await _get_drug_type_or_404(session, drug_type_id)
    await _ensure_unique(session, payload.description, exclude_id=drug_type.id)

    was_active = drug_type.is_active
    drug_type.description = payload.description
    drug_type.is_active = payload.is_active
    _touch(drug_type)
    await _commit(session)

    deactivated = was_active and not drug_type.is_active
    event = "drug_type.deactivated" if deactivated else "drug_type.updated"
    logger.info(event, drug_type_id=drug_type.id, actor_id=actor.id)
    return _to_read(drug_type)


async def deactivate_drug_type(
    session: AsyncSession,
    *,
    actor: User,
    drug_type_id: int,
) -> None:
    """Desactiva logicamente un tipo; es idempotente si ya estaba inactivo."""
    drug_type = await _get_drug_type_or_404(session, drug_type_id)

    if not drug_type.is_active:
        return

    drug_type.is_active = False
    _touch(drug_type)
    await _commit(session)
    logger.info("drug_type.deactivated", drug_type_id=drug_type.id, actor_id=actor.id)
