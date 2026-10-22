"""Endpoints de gestion de medicamentos."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.medication import (
    SORT_PATTERN,
    MedicationCreate,
    MedicationListResponse,
    MedicationRead,
    MedicationUpdate,
)
from app.services import medication_service

router = APIRouter(prefix="/medications", tags=["medications"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
AdminUser = Annotated[User, Depends(require_role("ADMIN"))]

_COMMON_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
    status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo o sin permisos"},
}


@router.get(
    "",
    response_model=MedicationListResponse,
    summary="Listar medicamentos",
    description=(
        "Devuelve una pagina de medicamentos con busqueda parcial y "
        "case-insensitive en description o dose, filtros opcionales por estado y "
        "por drug_type_id, brand_id y location_id, y orden por el catalogo "
        "permitido. Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Parametros invalidos"},
    },
)
async def list_medications(
    session: SessionDep,
    _actor: ReaderUser,
    page: Annotated[int, Query(ge=1, description="Pagina, desde 1")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Elementos por pagina")] = 20,
    search: Annotated[
        str | None,
        Query(max_length=100, description="Busqueda parcial en description o dose"),
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filtra por estado")] = None,
    drug_type_id: Annotated[
        int | None, Query(ge=1, description="Filtra por tipo de fármaco")
    ] = None,
    brand_id: Annotated[int | None, Query(ge=1, description="Filtra por marca")] = None,
    location_id: Annotated[int | None, Query(ge=1, description="Filtra por ubicación")] = None,
    sort: Annotated[
        str,
        Query(
            pattern=SORT_PATTERN,
            description=(
                "description, dose, drug_type_id, brand_id, location_id, "
                "created_at, is_active o id; prefijo '-' descendente"
            ),
        ),
    ] = "description",
) -> MedicationListResponse:
    """Lista medicamentos con paginacion y filtros."""
    return await medication_service.list_medications(
        session,
        page=page,
        size=size,
        search=search,
        is_active=is_active,
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
        sort=sort,
    )


@router.post(
    "",
    response_model=MedicationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear medicamento",
    description=(
        "Crea un medicamento con identidad unica (description, brand_id, dose) "
        "case-insensitive e is_active opcional (por defecto true). Los FK deben "
        "existir y estar activos. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El catálogo referenciado no existe"},
        status.HTTP_409_CONFLICT: {"description": "El medicamento ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def create_medication(
    session: SessionDep,
    actor: AdminUser,
    payload: MedicationCreate,
) -> MedicationRead:
    """Crea un medicamento."""
    return await medication_service.create_medication(session, actor=actor, payload=payload)


@router.get(
    "/{medication_id}",
    response_model=MedicationRead,
    summary="Obtener un medicamento",
    description=(
        "Devuelve un medicamento por identificador, incluido si esta inactivo. "
        "Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El medicamento no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def get_medication(
    medication_id: int,
    session: SessionDep,
    _actor: ReaderUser,
) -> MedicationRead:
    """Devuelve un medicamento por identificador."""
    return await medication_service.get_medication(session, medication_id=medication_id)


@router.put(
    "/{medication_id}",
    response_model=MedicationRead,
    summary="Reemplazar un medicamento",
    description=(
        "Reemplazo completo de description, drug_type_id, brand_id, location_id, "
        "dose e is_active. Permite reactivar con is_active true y desactivar con "
        "false. Los FK deben existir y estar activos. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El medicamento o el catálogo no existe"},
        status.HTTP_409_CONFLICT: {"description": "El medicamento ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def update_medication(
    medication_id: int,
    payload: MedicationUpdate,
    session: SessionDep,
    actor: AdminUser,
) -> MedicationRead:
    """Reemplaza los campos editables de un medicamento."""
    return await medication_service.update_medication(
        session,
        actor=actor,
        medication_id=medication_id,
        payload=payload,
    )


@router.delete(
    "/{medication_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Desactivar medicamento",
    description=(
        "Desactiva logicamente un medicamento sin borrar la fila ni sus "
        "referencias. Es idempotente: repetir la operacion no modifica ni registra "
        "de nuevo. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El medicamento no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def delete_medication(
    medication_id: int,
    session: SessionDep,
    actor: AdminUser,
) -> Response:
    """Desactiva un medicamento."""
    await medication_service.deactivate_medication(
        session, actor=actor, medication_id=medication_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
