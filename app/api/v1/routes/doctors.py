"""Doctor management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.doctor import (
    SORT_PATTERN,
    DoctorCreate,
    DoctorListResponse,
    DoctorRead,
    DoctorUpdate,
)
from app.services import doctor_service

router = APIRouter(prefix="/doctors", tags=["doctors"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
AdminUser = Annotated[User, Depends(require_role("ADMIN"))]

_COMMON_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
    status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo o sin permisos"},
}


def _is_medico(actor: User) -> bool:
    """Whether the authenticated viewer is a `MEDICO` (profile-only access)."""
    return actor.role.name == "MEDICO"


@router.get(
    "",
    response_model=DoctorListResponse,
    summary="Listar médicos",
    description=(
        "Devuelve una pagina de medicos con busqueda parcial y case-insensitive "
        "en nombre o cedula, filtro opcional por estado y orden por el catalogo "
        "permitido. Un MEDICO solo ve su propio perfil (sin asociacion, pagina "
        "vacia). Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Parametros invalidos"},
    },
)
async def list_doctors(
    session: SessionDep,
    actor: ReaderUser,
    page: Annotated[int, Query(ge=1, description="Pagina, desde 1")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Elementos por pagina")] = 20,
    search: Annotated[
        str | None,
        Query(
            max_length=150,
            description="Busqueda parcial en nombre o cedula",
        ),
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filtra por estado")] = None,
    sort: Annotated[
        str,
        Query(
            pattern=SORT_PATTERN,
            description=(
                "name, cedula, lab_shift, specialty, created_at, is_active o id; "
                "prefijo '-' descendente"
            ),
        ),
    ] = "name",
) -> DoctorListResponse:
    """List doctors with pagination and filters."""
    return await doctor_service.list_doctors(
        session,
        page=page,
        size=size,
        search=search,
        is_active=is_active,
        sort=sort,
        viewer_is_medico=_is_medico(actor),
        viewer_doctor_id=actor.doctor_id,
    )


@router.post(
    "",
    response_model=DoctorRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear médico",
    description=(
        "Crea un medico con cedula de 11 digitos unica, tanda de trabajo y "
        "especialidad. is_active es opcional (por defecto true). Un medico "
        "puede existir sin usuario asociado. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_409_CONFLICT: {"description": "La cedula ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def create_doctor(
    session: SessionDep,
    actor: AdminUser,
    payload: DoctorCreate,
) -> DoctorRead:
    """Create a doctor."""
    return await doctor_service.create_doctor(session, actor=actor, payload=payload)


@router.get(
    "/{doctor_id}",
    response_model=DoctorRead,
    summary="Obtener un médico",
    description=(
        "Devuelve un medico por identificador, incluido si esta inactivo. Un "
        "MEDICO solo puede consultar su propio perfil; cualquier otro "
        "identificador responde 404. Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El medico no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def get_doctor(
    doctor_id: int,
    session: SessionDep,
    actor: ReaderUser,
) -> DoctorRead:
    """Return a doctor by identifier."""
    return await doctor_service.get_doctor(
        session,
        doctor_id=doctor_id,
        viewer_is_medico=_is_medico(actor),
        viewer_doctor_id=actor.doctor_id,
    )


@router.put(
    "/{doctor_id}",
    response_model=DoctorRead,
    summary="Reemplazar un médico",
    description=(
        "Reemplazo completo de name, cedula, lab_shift, specialty e is_active. "
        "Permite reactivar con is_active true y desactivar con false. No "
        "modifica la asociacion con usuarios. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El medico no existe"},
        status.HTTP_409_CONFLICT: {"description": "La cedula ya existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def update_doctor(
    doctor_id: int,
    payload: DoctorUpdate,
    session: SessionDep,
    actor: AdminUser,
) -> DoctorRead:
    """Replace the editable fields of a doctor."""
    return await doctor_service.update_doctor(
        session,
        actor=actor,
        doctor_id=doctor_id,
        payload=payload,
    )


@router.delete(
    "/{doctor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Desactivar médico",
    description=(
        "Desactiva logicamente un medico sin borrar la fila ni sus referencias. "
        "Se rechaza con 409 doctor_has_visits si el medico tiene visitas "
        "registradas. Es idempotente cuando ya esta inactivo. Solo accesible "
        "para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El medico no existe"},
        status.HTTP_409_CONFLICT: {"description": "El medico tiene visitas registradas"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def delete_doctor(
    doctor_id: int,
    session: SessionDep,
    actor: AdminUser,
) -> Response:
    """Logically deactivate a doctor."""
    await doctor_service.deactivate_doctor(session, actor=actor, doctor_id=doctor_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
