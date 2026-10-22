"""Endpoints de gestion de ubicaciones."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.location import (
    SORT_PATTERN,
    LocationCreate,
    LocationListResponse,
    LocationRead,
    LocationUpdate,
)
from app.services import location_service

router = APIRouter(prefix="/locations", tags=["locations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
AdminUser = Annotated[User, Depends(require_role("ADMIN"))]

_COMMON_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
    status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo o sin permisos"},
}


@router.get(
    "",
    response_model=LocationListResponse,
    summary="Listar ubicaciones",
    description=(
        "Devuelve una pagina de ubicaciones con busqueda parcial y "
        "case-insensitive en description, shelf, section y cell, filtro opcional "
        "por estado y orden por el catalogo permitido. Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Parametros invalidos"},
    },
)
async def list_locations(
    session: SessionDep,
    _actor: ReaderUser,
    page: Annotated[int, Query(ge=1, description="Pagina, desde 1")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Elementos por pagina")] = 20,
    search: Annotated[
        str | None,
        Query(max_length=100, description="Busqueda parcial en los cuatro campos"),
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filtra por estado")] = None,
    sort: Annotated[
        str,
        Query(
            pattern=SORT_PATTERN,
            description=(
                "description, shelf, section, cell, created_at, is_active o id; "
                "prefijo '-' descendente"
            ),
        ),
    ] = "description",
) -> LocationListResponse:
    """Lista ubicaciones con paginacion y filtros."""
    return await location_service.list_locations(
        session,
        page=page,
        size=size,
        search=search,
        is_active=is_active,
        sort=sort,
    )


@router.post(
    "",
    response_model=LocationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear ubicación",
    description=(
        "Crea una ubicación con posicion (shelf, section, cell) unica "
        "(case-insensitive) e is_active opcional (por defecto true). "
        "La description puede repetirse. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_409_CONFLICT: {"description": "La ubicación ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def create_location(
    session: SessionDep,
    actor: AdminUser,
    payload: LocationCreate,
) -> LocationRead:
    """Crea una ubicación."""
    return await location_service.create_location(session, actor=actor, payload=payload)


@router.get(
    "/{location_id}",
    response_model=LocationRead,
    summary="Obtener una ubicación",
    description=(
        "Devuelve una ubicación por identificador, incluida si esta inactiva. "
        "Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "La ubicación no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def get_location(
    location_id: int,
    session: SessionDep,
    _actor: ReaderUser,
) -> LocationRead:
    """Devuelve una ubicación por identificador."""
    return await location_service.get_location(session, location_id=location_id)


@router.put(
    "/{location_id}",
    response_model=LocationRead,
    summary="Reemplazar una ubicación",
    description=(
        "Reemplazo completo de description, shelf, section, cell e is_active. "
        "Permite reactivar con is_active true y desactivar con false. "
        "Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "La ubicación no existe"},
        status.HTTP_409_CONFLICT: {"description": "La ubicación ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def update_location(
    location_id: int,
    payload: LocationUpdate,
    session: SessionDep,
    actor: AdminUser,
) -> LocationRead:
    """Reemplaza los campos editables de una ubicación."""
    return await location_service.update_location(
        session,
        actor=actor,
        location_id=location_id,
        payload=payload,
    )


@router.delete(
    "/{location_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Desactivar ubicación",
    description=(
        "Desactiva logicamente una ubicación sin borrar la fila ni sus "
        "referencias. Es idempotente: repetir la operacion no modifica ni registra "
        "de nuevo. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "La ubicación no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def delete_location(
    location_id: int,
    session: SessionDep,
    actor: AdminUser,
) -> Response:
    """Desactiva una ubicación."""
    await location_service.deactivate_location(session, actor=actor, location_id=location_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
