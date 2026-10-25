"""Visit report endpoints (FEAT-014 D-8)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.report import (
    REPORT_FORMAT_CSV,
    REPORT_FORMAT_JSON,
    REPORT_SORT_PATTERN,
    ReportResponse,
)
from app.schemas.visit import DEFAULT_SORT, VISIT_STATUS_PATTERN
from app.services import report_service

router = APIRouter(prefix="/reports/visits", tags=["reports"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReportUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]

CSV_MEDIA_TYPE = "text/csv"

_COMMON_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
    status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo o sin permisos"},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Parametros invalidos"},
}

DateFromQuery = Annotated[
    str | None,
    Query(
        description=(
            "Fecha inicial inclusive del reporte sobre visited_at; obligatoria. "
            "Sin zona horaria se asume America/Santo_Domingo (UTC-4)."
        )
    ),
]
DateToQuery = Annotated[
    str | None,
    Query(
        description=(
            "Fecha final inclusive del reporte sobre visited_at; obligatoria. "
            "Sin zona horaria se asume America/Santo_Domingo (UTC-4)."
        )
    ),
]
DoctorIdQuery = Annotated[
    int | None,
    Query(gt=0, description="Filtra por medico; solo el administrador puede usarlo"),
]
PatientIdQuery = Annotated[int | None, Query(gt=0, description="Filtra por paciente")]
StatusQuery = Annotated[
    str | None,
    Query(alias="status", pattern=VISIT_STATUS_PATTERN, description="REGISTRADA o ANULADA"),
]
FormatQuery = Annotated[
    str | None,
    Query(
        alias="format",
        description="json o csv; el formato pdf no esta disponible en la version 1",
    ),
]
PageQuery = Annotated[int, Query(ge=1, description="Pagina del detalle, desde 1")]
SizeQuery = Annotated[int, Query(ge=1, le=100, description="Elementos por pagina del detalle")]
SortQuery = Annotated[
    str,
    Query(
        pattern=REPORT_SORT_PATTERN,
        description="visited_at, created_at, id o status; prefijo '-' descendente",
    ),
]


def _csv_response(export: report_service.ReportCsvExport) -> Response:
    """Build the CSV response with the attachment header of rule 16."""
    return Response(
        content=export.content,
        media_type=CSV_MEDIA_TYPE,
        headers={"Content-Disposition": export.content_disposition},
    )


@router.get(
    "",
    response_model=ReportResponse,
    summary="Generar el reporte de visitas",
    description=(
        "Devuelve el reporte de visitas de un rango de fechas obligatorio e inclusivo: "
        "los filtros aplicados, el resumen de visitas por medico y el detalle paginado "
        "en la forma resumida de las visitas. Un MEDICO solo genera su propio reporte y "
        "no puede filtrar por medico. Con format=csv responde el mismo CSV de la "
        "exportacion, es decir el detalle completo sin paginar. El formato pdf no esta "
        "disponible en la version 1. Accesible para ADMIN y MEDICO."
    ),
    responses={
        status.HTTP_200_OK: {
            "description": (
                "Reporte paginado en JSON o, con format=csv, la exportacion CSV completa "
                "con BOM y Content-Disposition de adjunto"
            ),
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
        },
        **_COMMON_RESPONSES,
    },
)
async def get_visit_report(
    session: SessionDep,
    actor: ReportUser,
    date_from: DateFromQuery = None,
    date_to: DateToQuery = None,
    doctor_id: DoctorIdQuery = None,
    patient_id: PatientIdQuery = None,
    status_filter: StatusQuery = None,
    report_format: FormatQuery = None,
    page: PageQuery = 1,
    size: SizeQuery = 20,
    sort: SortQuery = DEFAULT_SORT,
) -> ReportResponse | Response:
    """Return the visit report as JSON or as the CSV export."""
    resolved_format = report_service.resolve_report_format(
        report_format, default=REPORT_FORMAT_JSON
    )
    if resolved_format == REPORT_FORMAT_CSV:
        export = await report_service.export_report_csv(
            session,
            actor=actor,
            date_from=date_from,
            date_to=date_to,
            doctor_id=doctor_id,
            patient_id=patient_id,
            status=status_filter,
        )
        return _csv_response(export)

    return await report_service.build_report(
        session,
        actor=actor,
        date_from=date_from,
        date_to=date_to,
        doctor_id=doctor_id,
        patient_id=patient_id,
        status=status_filter,
        page=page,
        size=size,
        sort=sort,
    )


@router.get(
    "/export",
    response_class=Response,
    summary="Exportar el reporte de visitas a CSV",
    description=(
        "Exporta el reporte completo, sin paginar, en CSV UTF-8 con BOM y encabezado en "
        "espanol, con fechas ISO 8601 en America/Santo_Domingo y orden descendente por "
        "fecha de atencion. Aplica los mismos filtros y la misma propiedad que el reporte "
        "JSON: un MEDICO solo exporta sus propias visitas. Los parametros page, size y sort "
        "se aceptan por compatibilidad con el reporte JSON y no alteran la exportacion. El "
        "formato pdf no esta disponible en la version 1. Accesible para ADMIN y MEDICO."
    ),
    responses={
        status.HTTP_200_OK: {
            "description": (
                "CSV del reporte con Content-Disposition de adjunto y nombre "
                "reporte_visitas_AAAAMMDD.csv"
            ),
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
        },
        **_COMMON_RESPONSES,
    },
)
async def export_visit_report(
    session: SessionDep,
    actor: ReportUser,
    date_from: DateFromQuery = None,
    date_to: DateToQuery = None,
    doctor_id: DoctorIdQuery = None,
    patient_id: PatientIdQuery = None,
    status_filter: StatusQuery = None,
    report_format: FormatQuery = None,
    page: PageQuery = 1,
    size: SizeQuery = 20,
    sort: SortQuery = DEFAULT_SORT,
) -> Response:
    """Export the visit report as a CSV attachment."""
    report_service.resolve_report_format(report_format, default=REPORT_FORMAT_CSV)
    export = await report_service.export_report_csv(
        session,
        actor=actor,
        date_from=date_from,
        date_to=date_to,
        doctor_id=doctor_id,
        patient_id=patient_id,
        status=status_filter,
    )
    return _csv_response(export)
