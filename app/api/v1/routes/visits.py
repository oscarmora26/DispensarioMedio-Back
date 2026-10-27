"""Visit management endpoints (FEAT-013 D-13)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.visit import (
    DEFAULT_SORT,
    SORT_PATTERN,
    VISIT_STATUS_PATTERN,
    VisitAnnulRequest,
    VisitAnnulResponse,
    VisitCreate,
    VisitListResponse,
    VisitRead,
    VisitUpdate,
)
from app.services import visit_service

router = APIRouter(prefix="/visits", tags=["visits"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
MedicoUser = Annotated[User, Depends(require_role("MEDICO"))]

_COMMON_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
    status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo o sin permisos"},
}


@router.get(
    "",
    response_model=VisitListResponse,
    summary="Listar visitas",
    description=(
        "Devuelve una pagina de visitas con filtros combinables por medico, "
        "paciente, rango de fechas de atencion, medicamento entregado, estado y "
        "texto libre en sintomas o recomendaciones, con orden por el catalogo "
        "permitido (defecto -visited_at). Un MEDICO solo ve sus propias visitas y "
        "no puede filtrar por medico. Accesible para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Parametros invalidos"},
    },
)
async def list_visits(
    session: SessionDep,
    actor: ReaderUser,
    page: Annotated[int, Query(ge=1, description="Pagina, desde 1")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Elementos por pagina")] = 20,
    doctor_id: Annotated[
        int | None,
        Query(gt=0, description="Filtra por medico; solo el administrador puede usarlo"),
    ] = None,
    patient_id: Annotated[
        int | None,
        Query(gt=0, description="Filtra por paciente"),
    ] = None,
    date_from: Annotated[
        datetime | None,
        Query(description="Fecha inicial inclusive de visited_at; sin zona se asume UTC-4"),
    ] = None,
    date_to: Annotated[
        datetime | None,
        Query(description="Fecha final inclusive de visited_at; sin zona se asume UTC-4"),
    ] = None,
    medication_id: Annotated[
        int | None,
        Query(gt=0, description="Filtra visitas que incluyen ese medicamento"),
    ] = None,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            pattern=VISIT_STATUS_PATTERN,
            description="REGISTRADA o ANULADA",
        ),
    ] = None,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Busqueda parcial en sintomas o recomendaciones"),
    ] = None,
    sort: Annotated[
        str,
        Query(
            pattern=SORT_PATTERN,
            description="visited_at, created_at, id o status; prefijo '-' descendente",
        ),
    ] = DEFAULT_SORT,
) -> VisitListResponse:
    """List visits with pagination, filters and ownership rules."""
    return await visit_service.list_visits(
        session,
        actor=actor,
        page=page,
        size=size,
        doctor_id=doctor_id,
        patient_id=patient_id,
        date_from=date_from,
        date_to=date_to,
        medication_id=medication_id,
        status=status_filter,
        q=q,
        sort=sort,
    )


@router.post(
    "",
    response_model=VisitRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar una visita",
    description=(
        "Registra la atencion de un paciente por un medico con fecha no futura, "
        "sintomas obligatorios y medicamentos activos opcionales. El medico se "
        "obtiene del token y no se puede indicar otro. Solo accesible para MEDICO; "
        "el ADMIN solo puede leer visitas (FEAT-019)."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {
            "description": "El medico, el paciente o el medicamento no existe"
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def create_visit(
    session: SessionDep,
    actor: MedicoUser,
    payload: VisitCreate,
) -> VisitRead:
    """Register a visit."""
    return await visit_service.create_visit(session, actor=actor, payload=payload)


@router.get(
    "/{visit_id}",
    response_model=VisitRead,
    summary="Obtener una visita",
    description=(
        "Devuelve el detalle de una visita, incluidos sus medicamentos y, si esta "
        "anulada, el motivo y la auditoria de la anulacion. Un MEDICO solo puede "
        "consultar sus propias visitas; cualquier otra responde 404. Accesible "
        "para ADMIN y MEDICO."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "La visita no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def get_visit(
    visit_id: int,
    session: SessionDep,
    actor: ReaderUser,
) -> VisitRead:
    """Return a visit by identifier."""
    return await visit_service.get_visit(session, actor=actor, visit_id=visit_id)


@router.put(
    "/{visit_id}",
    response_model=VisitRead,
    summary="Reemplazar una visita",
    description=(
        "Reemplazo completo de doctor_id, patient_id, visited_at, symptoms, "
        "recommendations y medications: la lista de medicamentos enviada sustituye "
        "a la anterior. Una visita anulada no se puede editar (409 visit_annulled); "
        "una visita de otro medico responde 404. Solo accesible para MEDICO, y solo "
        "sobre sus propias visitas (FEAT-019)."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {
            "description": "La visita, el medico, el paciente o el medicamento no existe"
        },
        status.HTTP_409_CONFLICT: {"description": "La visita anulada no se puede editar"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def update_visit(
    visit_id: int,
    payload: VisitUpdate,
    session: SessionDep,
    actor: MedicoUser,
) -> VisitRead:
    """Replace the editable fields of a visit."""
    return await visit_service.update_visit(
        session,
        actor=actor,
        visit_id=visit_id,
        payload=payload,
    )


@router.post(
    "/{visit_id}/annul",
    response_model=VisitAnnulResponse,
    summary="Anular una visita",
    description=(
        "Anula una visita una sola vez, guardando el usuario, la fecha y el motivo "
        "de la anulacion. Repetir la anulacion responde 409 visit_already_annulled. "
        "Solo accesible para MEDICO, y solo sobre sus propias visitas (FEAT-019)."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "La visita no existe"},
        status.HTTP_409_CONFLICT: {"description": "La visita ya esta anulada"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def annul_visit(
    visit_id: int,
    payload: VisitAnnulRequest,
    session: SessionDep,
    actor: MedicoUser,
) -> VisitAnnulResponse:
    """Annull a visit."""
    return await visit_service.annul_visit(
        session,
        actor=actor,
        visit_id=visit_id,
        payload=payload,
    )
