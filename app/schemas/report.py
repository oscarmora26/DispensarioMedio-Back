"""Schemas for the visit report contract (FEAT-014 D-3, D-4, D-5, D-8)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.visit import SORT_PATTERN, VisitListItem

# Closed format catalog of the report (D-1, D-6): `pdf` is deferred to a human
# decision, so it is rejected explicitly instead of being accepted.
REPORT_FORMAT_VALUES = ("json", "csv")
REPORT_FORMAT_JSON = "json"
REPORT_FORMAT_CSV = "csv"
REPORT_FORMAT_PDF = "pdf"

#: Closed ordering catalog of the report detail: the FEAT-013 D-6 catalog.
REPORT_SORT_PATTERN = SORT_PATTERN

# Field-level messages of rules 3-9 (they travel in `errors[0]`).
DATE_FROM_REQUIRED_MESSAGE = "La fecha inicial es obligatoria"
DATE_TO_REQUIRED_MESSAGE = "La fecha final es obligatoria"
DATE_INVALID_MESSAGE = "La fecha no es válida"
PDF_FORMAT_MESSAGE = "El formato PDF no está disponible en la versión 1"
FORMAT_INVALID_MESSAGE = "El formato debe ser json o csv"
STATUS_INVALID_MESSAGE = "El estado debe ser REGISTRADA o ANULADA"


class ReportFilters(BaseModel):
    """Query echoed literally in the response (rule 12, D-4)."""

    date_from: str = Field(description="Fecha inicial tal como se recibio en la peticion")
    date_to: str = Field(description="Fecha final tal como se recibio en la peticion")
    doctor_id: int | None = Field(
        default=None,
        description="Medico filtrado por el administrador; null si no se envio",
    )
    patient_id: int | None = Field(
        default=None,
        description="Paciente filtrado; null si no se envio",
    )
    status: str | None = Field(
        default=None,
        description="Estado filtrado, REGISTRADA o ANULADA; null si no se envio",
    )


class DoctorTotal(BaseModel):
    """Visits per doctor of the run summary (rule 13)."""

    doctor_id: int = Field(description="Identificador del medico")
    doctor_name: str = Field(description="Nombre actual del medico")
    total: int = Field(description="Visitas del medico que cumplen los filtros")


class ReportSummary(BaseModel):
    """Aggregated totals of the report (rule 13)."""

    total_visits: int = Field(description="Visitas que cumplen los filtros; coincide con total")
    by_doctor: list[DoctorTotal] = Field(
        description="Totales por medico, ordenados por total descendente y nombre ascendente"
    )


class ReportResponse(BaseModel):
    """Paginated report of rules 11-14 (D-3): filters, summary, items and page envelope."""

    filters: ReportFilters = Field(description="Eco literal de los filtros recibidos")
    summary: ReportSummary = Field(description="Resumen agregado del reporte")
    items: list[VisitListItem] = Field(description="Pagina del detalle en la forma resumida D-8")
    total: int = Field(description="Visitas que cumplen los filtros")
    page: int = Field(description="Pagina devuelta, desde 1")
    size: int = Field(description="Elementos por pagina")
    pages: int = Field(description="Cantidad de paginas; 0 cuando no hay visitas")
