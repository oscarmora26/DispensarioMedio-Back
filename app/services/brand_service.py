"""Servicios de gestion de marcas."""

from __future__ import annotations

import structlog
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.brand import Brand
from app.models.user import User
from app.repositories import brand_repository
from app.schemas.brand import (
    SORT_FIELDS,
    BrandCreate,
    BrandListResponse,
    BrandRead,
    BrandUpdate,
)

logger = structlog.get_logger(__name__)

BRAND_NOT_FOUND_MESSAGE = "La marca no existe"
BRAND_EXISTS_MESSAGE = "La marca ya existe"

UNIQUE_CONSTRAINT = "uq_brands_description"


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


def _to_read(brand: Brand) -> BrandRead:
    """Construye la forma unica de la marca a partir de la entidad ORM."""
    return BrandRead(
        id=brand.id,
        description=brand.description,
        is_active=brand.is_active,
    )


def _touch(brand: Brand) -> None:
    """Refresca `updated_at` con la hora de pared de la base (FEAT-008 D-9).

    El modelo usa `onupdate=func.now()`, pero en PostgreSQL `now()` es la hora
    de inicio de la transaccion: dentro de una transaccion larga el valor no
    cambiaria. `clock_timestamp()` mantiene la semantica esperada sin tocar el
    esquema ni depender del reloj de la aplicacion.
    """
    brand.updated_at = func.clock_timestamp()


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
            raise ConflictError(BRAND_EXISTS_MESSAGE, code="brand_exists") from None
        raise


async def _get_brand_or_404(session: AsyncSession, brand_id: int) -> Brand:
    """Carga una marca o lanza `brand_not_found`."""
    brand = await brand_repository.get_brand_by_id(session, brand_id)
    if brand is None:
        raise NotFoundError(BRAND_NOT_FOUND_MESSAGE, code="brand_not_found")
    return brand


async def _ensure_unique(
    session: AsyncSession,
    description: str,
    *,
    exclude_id: int | None = None,
) -> None:
    """Comprueba que `description` no exista ya (CITEXT, case-insensitive)."""
    existing = await brand_repository.find_brand_by_description(
        session,
        description,
        exclude_id=exclude_id,
    )
    if existing is not None:
        raise ConflictError(BRAND_EXISTS_MESSAGE, code="brand_exists")


async def list_brands(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None = None,
    is_active: bool | None = None,
    sort: str = "description",
) -> BrandListResponse:
    """Lista marcas con paginacion, busqueda, filtro de estado y orden."""
    sort_field, sort_desc = parse_sort(sort)
    normalized_search = search.strip() if search else None
    brands, total = await brand_repository.list_brands(
        session,
        page=page,
        size=size,
        search=normalized_search or None,
        is_active=is_active,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    pages = calculate_pages(total, size)
    return BrandListResponse(
        items=[_to_read(brand) for brand in brands],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


async def get_brand(session: AsyncSession, *, brand_id: int) -> BrandRead:
    """Devuelve una marca por identificador, incluida si esta inactiva."""
    brand = await _get_brand_or_404(session, brand_id)
    return _to_read(brand)


async def create_brand(
    session: AsyncSession,
    *,
    actor: User,
    payload: BrandCreate,
) -> BrandRead:
    """Crea una marca y devuelve su forma unica."""
    await _ensure_unique(session, payload.description)

    brand = Brand(
        description=payload.description,
        is_active=payload.is_active,
    )
    session.add(brand)
    await _commit(session)

    logger.info("brand.created", brand_id=brand.id, actor_id=actor.id)
    return _to_read(brand)


async def update_brand(
    session: AsyncSession,
    *,
    actor: User,
    brand_id: int,
    payload: BrandUpdate,
) -> BrandRead:
    """Reemplaza `description` e `is_active` de una marca (FEAT-008 D-1)."""
    brand = await _get_brand_or_404(session, brand_id)
    await _ensure_unique(session, payload.description, exclude_id=brand.id)

    was_active = brand.is_active
    brand.description = payload.description
    brand.is_active = payload.is_active
    _touch(brand)
    await _commit(session)

    deactivated = was_active and not brand.is_active
    event = "brand.deactivated" if deactivated else "brand.updated"
    logger.info(event, brand_id=brand.id, actor_id=actor.id)
    return _to_read(brand)


async def deactivate_brand(
    session: AsyncSession,
    *,
    actor: User,
    brand_id: int,
) -> None:
    """Desactiva logicamente una marca; es idempotente si ya estaba inactiva."""
    brand = await _get_brand_or_404(session, brand_id)

    if not brand.is_active:
        return

    brand.is_active = False
    _touch(brand)
    await _commit(session)
    logger.info("brand.deactivated", brand_id=brand.id, actor_id=actor.id)
