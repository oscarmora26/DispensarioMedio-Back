"""Endpoints de gestion de tipos de farmacos."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.drug_type import (
    SORT_PATTERN,
    DrugTypeCreate,
    DrugTypeListResponse,
    DrugTypeRead,
    DrugTypeUpdate,
)
from app.services import drug_type_service

router = APIRouter(prefix="/drug-types", tags=["drug-types"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
AdminUser = Annotated[User, Depends(require_role("ADMIN"))]

_COMMON_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
    status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo o sin permisos"},
}


@router.get(
    "",
    response_model=DrugTypeListResponse,
    summary="Listar tipos de fármacos",
    description=(
        "Devuelve una pagina de tipos de farmacos con busqueda parcial y "
        "case-insensitive en description, filtro opcional por estado y orden por "
        "el catalogo permitido. Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Parametros invalidos"},
    },
)
async def list_drug_types(
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
) -> DrugTypeListResponse:
    """Lista tipos de farmacos con paginacion y filtros."""
    return await drug_type_service.list_drug_types(
        session,
        page=page,
        size=size,
        search=search,
        is_active=is_active,
        sort=sort,
    )


@router.post(
    "",
    response_model=DrugTypeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear tipo de fármaco",
    description=(
        "Crea un tipo de farmaco con description unica (case-insensitive) e "
        "is_active opcional (por defecto true). Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_409_CONFLICT: {"description": "El tipo de farmaco ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def create_drug_type(
    session: SessionDep,
    actor: AdminUser,
    payload: DrugTypeCreate,
) -> DrugTypeRead:
    """Crea un tipo de farmaco."""
    return await drug_type_service.create_drug_type(session, actor=actor, payload=payload)


@router.get(
    "/{drug_type_id}",
    response_model=DrugTypeRead,
    summary="Obtener un tipo de fármaco",
    description=(
        "Devuelve un tipo de farmaco por identificador, incluido si esta inactivo. "
        "Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El tipo de farmaco no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def get_drug_type(
    drug_type_id: int,
    session: SessionDep,
    _actor: ReaderUser,
) -> DrugTypeRead:
    """Devuelve un tipo de farmaco por identificador."""
    return await drug_type_service.get_drug_type(session, drug_type_id=drug_type_id)


@router.put(
    "/{drug_type_id}",
    response_model=DrugTypeRead,
    summary="Reemplazar un tipo de fármaco",
    description=(
        "Reemplazo completo de description e is_active. Permite reactivar con "
        "is_active true y desactivar con false. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El tipo de farmaco no existe"},
        status.HTTP_409_CONFLICT: {"description": "El tipo de farmaco ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def update_drug_type(
    drug_type_id: int,
    payload: DrugTypeUpdate,
    session: SessionDep,
    actor: AdminUser,
) -> DrugTypeRead:
    """Reemplaza los campos editables de un tipo de farmaco."""
    return await drug_type_service.update_drug_type(
        session,
        actor=actor,
        drug_type_id=drug_type_id,
        payload=payload,
    )


@router.delete(
    "/{drug_type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Desactivar tipo de fármaco",
    description=(
        "Desactiva logicamente un tipo de farmaco sin borrar la fila ni sus "
        "referencias. Es idempotente: repetir la operacion no modifica ni registra "
        "de nuevo. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El tipo de farmaco no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def delete_drug_type(
    drug_type_id: int,
    session: SessionDep,
    actor: AdminUser,
) -> Response:
    """Desactiva un tipo de farmaco."""
    await drug_type_service.deactivate_drug_type(session, actor=actor, drug_type_id=drug_type_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
