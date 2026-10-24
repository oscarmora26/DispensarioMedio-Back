"""Patient management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.patient import (
    PATIENT_TYPE_VALUES,
    SORT_PATTERN,
    PatientCreate,
    PatientListResponse,
    PatientRead,
    PatientUpdate,
)
from app.services import patient_service

router = APIRouter(prefix="/patients", tags=["patients"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
AdminUser = Annotated[User, Depends(require_role("ADMIN"))]

_COMMON_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
    status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo o sin permisos"},
}

_PATIENT_TYPES_DESCRIPTION = ", ".join(PATIENT_TYPE_VALUES)


@router.get(
    "",
    response_model=PatientListResponse,
    summary="Listar pacientes",
    description=(
        "Devuelve una pagina de pacientes con busqueda parcial y case-insensitive "
        "en nombre, cedula o carnet, filtro opcional por estado y orden por el "
        "catalogo permitido. Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Parametros invalidos"},
    },
)
async def list_patients(
    session: SessionDep,
    _actor: ReaderUser,
    page: Annotated[int, Query(ge=1, description="Pagina, desde 1")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Elementos por pagina")] = 20,
    search: Annotated[
        str | None,
        Query(
            max_length=150,
            description="Busqueda parcial en nombre, cedula o carnet",
        ),
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filtra por estado")] = None,
    sort: Annotated[
        str,
        Query(
            pattern=SORT_PATTERN,
            description="name, cedula, created_at, is_active o id; prefijo '-' descendente",
        ),
    ] = "name",
) -> PatientListResponse:
    """List patients with pagination and filters."""
    return await patient_service.list_patients(
        session,
        page=page,
        size=size,
        search=search,
        is_active=is_active,
        sort=sort,
    )


@router.post(
    "",
    response_model=PatientRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear paciente",
    description=(
        "Crea un paciente con cedula de 11 digitos unica, carnet unico cuando "
        "existe (obligatorio para estudiantes) y patient_type en el catalogo "
        f"{_PATIENT_TYPES_DESCRIPTION}. is_active es opcional (por defecto true). "
        "Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_409_CONFLICT: {"description": "La cedula o el carnet ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def create_patient(
    session: SessionDep,
    actor: AdminUser,
    payload: PatientCreate,
) -> PatientRead:
    """Create a patient."""
    return await patient_service.create_patient(session, actor=actor, payload=payload)


@router.get(
    "/{patient_id}",
    response_model=PatientRead,
    summary="Obtener un paciente",
    description=(
        "Devuelve un paciente por identificador, incluido si esta inactivo. "
        "Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El paciente no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def get_patient(
    patient_id: int,
    session: SessionDep,
    _actor: ReaderUser,
) -> PatientRead:
    """Return a patient by identifier."""
    return await patient_service.get_patient(session, patient_id=patient_id)


@router.put(
    "/{patient_id}",
    response_model=PatientRead,
    summary="Reemplazar un paciente",
    description=(
        "Reemplazo completo de name, cedula, patient_type e is_active. Un carnet "
        "ausente borra el carnet existente. Permite reactivar con is_active true "
        "y desactivar con false. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El paciente no existe"},
        status.HTTP_409_CONFLICT: {"description": "La cedula o el carnet ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def update_patient(
    patient_id: int,
    payload: PatientUpdate,
    session: SessionDep,
    actor: AdminUser,
) -> PatientRead:
    """Replace the editable fields of a patient."""
    return await patient_service.update_patient(
        session,
        actor=actor,
        patient_id=patient_id,
        payload=payload,
    )


@router.delete(
    "/{patient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Desactivar paciente",
    description=(
        "Desactiva logicamente un paciente sin borrar la fila ni sus referencias. "
        "Es idempotente: repetir la operacion no modifica ni registra de nuevo. "
        "Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El paciente no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def delete_patient(
    patient_id: int,
    session: SessionDep,
    actor: AdminUser,
) -> Response:
    """Logically deactivate a patient."""
    await patient_service.deactivate_patient(session, actor=actor, patient_id=patient_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
