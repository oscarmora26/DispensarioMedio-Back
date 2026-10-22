"""Servicios de gestion de ubicaciones."""

from __future__ import annotations

import structlog
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.location import Location
from app.models.user import User
from app.repositories import location_repository
from app.schemas.location import (
    SORT_FIELDS,
    LocationCreate,
    LocationListResponse,
    LocationRead,
    LocationUpdate,
)

logger = structlog.get_logger(__name__)

LOCATION_NOT_FOUND_MESSAGE = "La ubicación no existe"
LOCATION_EXISTS_MESSAGE = "La ubicación ya existe"

UNIQUE_CONSTRAINT = "uq_locations_position"


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


def _to_read(location: Location) -> LocationRead:
    """Construye la forma unica de la ubicación a partir de la entidad ORM."""
    return LocationRead(
        id=location.id,
        description=location.description,
        shelf=location.shelf,
        section=location.section,
        cell=location.cell,
        is_active=location.is_active,
    )


def _touch(location: Location) -> None:
    """Refresca `updated_at` con la hora de pared de la base (FEAT-009 D-9).

    El modelo usa `onupdate=func.now()`, pero en PostgreSQL `now()` es la hora
    de inicio de la transaccion: dentro de una transaccion larga el valor no
    cambiaria. `clock_timestamp()` mantiene la semantica esperada sin tocar el
    esquema ni depender del reloj de la aplicacion.
    """
    location.updated_at = func.clock_timestamp()


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
            raise ConflictError(LOCATION_EXISTS_MESSAGE, code="location_exists") from None
        raise


async def _get_location_or_404(session: AsyncSession, location_id: int) -> Location:
    """Carga una ubicación o lanza `location_not_found`."""
    location = await location_repository.get_location_by_id(session, location_id)
    if location is None:
        raise NotFoundError(LOCATION_NOT_FOUND_MESSAGE, code="location_not_found")
    return location


async def _ensure_unique_position(
    session: AsyncSession,
    shelf: str,
    section: str,
    cell: str,
    *,
    exclude_id: int | None = None,
) -> None:
    """Comprueba que la posición `(shelf, section, cell)` no exista ya (CITEXT)."""
    existing = await location_repository.find_location_by_position(
        session,
        shelf,
        section,
        cell,
        exclude_id=exclude_id,
    )
    if existing is not None:
        raise ConflictError(LOCATION_EXISTS_MESSAGE, code="location_exists")


async def list_locations(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None = None,
    is_active: bool | None = None,
    sort: str = "description",
) -> LocationListResponse:
    """Lista ubicaciones con paginacion, busqueda, filtro de estado y orden."""
    sort_field, sort_desc = parse_sort(sort)
    normalized_search = search.strip() if search else None
    locations, total = await location_repository.list_locations(
        session,
        page=page,
        size=size,
        search=normalized_search or None,
        is_active=is_active,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    pages = calculate_pages(total, size)
    return LocationListResponse(
        items=[_to_read(location) for location in locations],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


async def get_location(session: AsyncSession, *, location_id: int) -> LocationRead:
    """Devuelve una ubicación por identificador, incluida si esta inactiva."""
    location = await _get_location_or_404(session, location_id)
    return _to_read(location)


async def create_location(
    session: AsyncSession,
    *,
    actor: User,
    payload: LocationCreate,
) -> LocationRead:
    """Crea una ubicación y devuelve su forma unica."""
    await _ensure_unique_position(session, payload.shelf, payload.section, payload.cell)

    location = Location(
        description=payload.description,
        shelf=payload.shelf,
        section=payload.section,
        cell=payload.cell,
        is_active=payload.is_active,
    )
    session.add(location)
    await _commit(session)

    logger.info("location.created", location_id=location.id, actor_id=actor.id)
    return _to_read(location)


async def update_location(
    session: AsyncSession,
    *,
    actor: User,
    location_id: int,
    payload: LocationUpdate,
) -> LocationRead:
    """Reemplaza los cinco campos editables de una ubicación (FEAT-009 D-1)."""
    location = await _get_location_or_404(session, location_id)
    await _ensure_unique_position(
        session,
        payload.shelf,
        payload.section,
        payload.cell,
        exclude_id=location.id,
    )

    was_active = location.is_active
    location.description = payload.description
    location.shelf = payload.shelf
    location.section = payload.section
    location.cell = payload.cell
    location.is_active = payload.is_active
    _touch(location)
    await _commit(session)

    deactivated = was_active and not location.is_active
    event = "location.deactivated" if deactivated else "location.updated"
    logger.info(event, location_id=location.id, actor_id=actor.id)
    return _to_read(location)


async def deactivate_location(
    session: AsyncSession,
    *,
    actor: User,
    location_id: int,
) -> None:
    """Desactiva logicamente una ubicación; es idempotente si ya estaba inactiva."""
    location = await _get_location_or_404(session, location_id)

    if not location.is_active:
        return

    location.is_active = False
    _touch(location)
    await _commit(session)
    logger.info("location.deactivated", location_id=location.id, actor_id=actor.id)
