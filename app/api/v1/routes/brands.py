"""Endpoints de gestion de marcas."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.brand import (
    SORT_PATTERN,
    BrandCreate,
    BrandListResponse,
    BrandRead,
    BrandUpdate,
)
from app.services import brand_service

router = APIRouter(prefix="/brands", tags=["brands"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
AdminUser = Annotated[User, Depends(require_role("ADMIN"))]

_COMMON_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
    status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo o sin permisos"},
}


@router.get(
    "",
    response_model=BrandListResponse,
    summary="Listar marcas",
    description=(
        "Devuelve una pagina de marcas con busqueda parcial y "
        "case-insensitive en description, filtro opcional por estado y orden por "
        "el catalogo permitido. Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Parametros invalidos"},
    },
)
async def list_brands(
    session: SessionDep,
    _actor: ReaderUser,
    page: Annotated[int, Query(ge=1, description="Pagina, desde 1")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Elementos por pagina")] = 20,
    search: Annotated[
        str | None,
        Query(max_length=100, description="Busqueda parcial en description"),
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filtra por estado")] = None,
    sort: Annotated[
        str,
        Query(
            pattern=SORT_PATTERN,
            description="description, created_at, is_active o id; prefijo '-' descendente",
        ),
    ] = "description",
) -> BrandListResponse:
    """Lista marcas con paginacion y filtros."""
    return await brand_service.list_brands(
        session,
        page=page,
        size=size,
        search=search,
        is_active=is_active,
        sort=sort,
    )


@router.post(
    "",
    response_model=BrandRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear marca",
    description=(
        "Crea una marca con description unica (case-insensitive) e "
        "is_active opcional (por defecto true). Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_409_CONFLICT: {"description": "La marca ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def create_brand(
    session: SessionDep,
    actor: AdminUser,
    payload: BrandCreate,
) -> BrandRead:
    """Crea una marca."""
    return await brand_service.create_brand(session, actor=actor, payload=payload)


@router.get(
    "/{brand_id}",
    response_model=BrandRead,
    summary="Obtener una marca",
    description=(
        "Devuelve una marca por identificador, incluida si esta inactiva. "
        "Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "La marca no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def get_brand(
    brand_id: int,
    session: SessionDep,
    _actor: ReaderUser,
) -> BrandRead:
    """Devuelve una marca por identificador."""
    return await brand_service.get_brand(session, brand_id=brand_id)


@router.put(
    "/{brand_id}",
    response_model=BrandRead,
    summary="Reemplazar una marca",
    description=(
        "Reemplazo completo de description e is_active. Permite reactivar con "
        "is_active true y desactivar con false. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "La marca no existe"},
        status.HTTP_409_CONFLICT: {"description": "La marca ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def update_brand(
    brand_id: int,
    payload: BrandUpdate,
    session: SessionDep,
    actor: AdminUser,
) -> BrandRead:
    """Reemplaza los campos editables de una marca."""
    return await brand_service.update_brand(
        session,
        actor=actor,
        brand_id=brand_id,
        payload=payload,
    )


@router.delete(
    "/{brand_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Desactivar marca",
    description=(
        "Desactiva logicamente una marca sin borrar la fila ni sus "
        "referencias. Es idempotente: repetir la operacion no modifica ni registra "
        "de nuevo. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "La marca no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def delete_brand(
    brand_id: int,
    session: SessionDep,
    actor: AdminUser,
) -> Response:
    """Desactiva una marca."""
    await brand_service.deactivate_brand(session, actor=actor, brand_id=brand_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
