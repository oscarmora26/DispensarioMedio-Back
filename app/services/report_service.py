"""Visit report services (FEAT-014 rules 3-18)."""

from __future__ import annotations

import codecs
import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.user import User
from app.models.visit import Visit
from app.repositories import visit_repository
from app.schemas.report import (
    DATE_FROM_REQUIRED_MESSAGE,
    DATE_INVALID_MESSAGE,
    DATE_TO_REQUIRED_MESSAGE,
    FORMAT_INVALID_MESSAGE,
    PDF_FORMAT_MESSAGE,
    REPORT_FORMAT_CSV,
    REPORT_FORMAT_JSON,
    REPORT_FORMAT_PDF,
    REPORT_FORMAT_VALUES,
    STATUS_INVALID_MESSAGE,
    DoctorTotal,
    ReportFilters,
    ReportResponse,
    ReportSummary,
)
from app.schemas.visit import (
    DATE_RANGE_MESSAGE,
    DEFAULT_SORT,
    DOCTOR_FILTER_FORBIDDEN_MESSAGE,
    VISIT_STATUS_VALUES,
)
from app.services.visit_service import (
    SANTO_DOMINGO,
    calculate_pages,
    parse_sort,
    to_list_item,
    to_utc,
)

logger = structlog.get_logger(__name__)

INVALID_DATA_MESSAGE = "Datos inválidos"
MEDICO_ROLE = "MEDICO"

#: Spanish header of the CSV export (rule 16, D-5).
CSV_HEADER = [
    "id",
    "fecha",
    "medico",
    "cedula_medico",
    "paciente",
    "cedula_paciente",
    "sintomas",
    "recomendaciones",
    "estado",
    "creado_por",
]
CSV_FILENAME_PREFIX = "reporte_visitas"


def _invalid(field: str, message: str) -> AppError:
    """Build the 422 field-level error of rules 3-9."""
    return AppError(
        INVALID_DATA_MESSAGE,
        status_code=422,
        code="validation_error",
        errors=[{"field": field, "message": message}],
    )


@dataclass(frozen=True)
class ReportDateRange:
    """Validated range: the original strings for the echo and the UTC bounds."""

    raw_from: str
    raw_to: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ReportScope:
    """Effective doctor filter of the actor and whether the report is empty."""

    doctor_id: int | None
    empty: bool


@dataclass(frozen=True)
class ReportCsvExport:
    """CSV document of the report with its downloadable file name."""

    filename: str
    content: bytes

    @property
    def content_disposition(self) -> str:
        """Build the `Content-Disposition` header of rule 16."""
        return f'attachment; filename="{self.filename}"'


def _required_raw_date(field: str, value: str | None, message: str) -> str:
    """Reject a missing or blank date keeping the received string (rules 3 and 12)."""
    if value is None or not value.strip():
        raise _invalid(field, message)
    return value


def _parse_date(field: str, value: str) -> datetime:
    """Parse an ISO 8601 date assuming Santo Domingo for naive input (rules 4-5)."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise _invalid(field, DATE_INVALID_MESSAGE) from None
    return to_utc(parsed)


def resolve_report_dates(date_from: str | None, date_to: str | None) -> ReportDateRange:
    """Validate the mandatory range of rules 3-5 and normalize it to UTC."""
    raw_from = _required_raw_date("date_from", date_from, DATE_FROM_REQUIRED_MESSAGE)
    raw_to = _required_raw_date("date_to", date_to, DATE_TO_REQUIRED_MESSAGE)
    start = _parse_date("date_from", raw_from)
    end = _parse_date("date_to", raw_to)
    if start > end:
        raise _invalid("date_from", DATE_RANGE_MESSAGE)
    return ReportDateRange(raw_from=raw_from, raw_to=raw_to, start=start, end=end)


def resolve_report_format(value: str | None, *, default: str = REPORT_FORMAT_JSON) -> str:
    """Resolve the `format` parameter of rules 9-10, rejecting `pdf` explicitly."""
    candidate = (value or "").strip()
    if not candidate:
        return default
    if candidate == REPORT_FORMAT_PDF:
        raise _invalid("format", PDF_FORMAT_MESSAGE)
    if candidate not in REPORT_FORMAT_VALUES:
        raise _invalid("format", FORMAT_INVALID_MESSAGE)
    return candidate


def resolve_report_status(status: str | None) -> str | None:
    """Validate the closed `status` catalog of rule 7."""
    if status is None or not status.strip():
        return None
    candidate = status.strip()
    if candidate not in VISIT_STATUS_VALUES:
        raise _invalid("status", STATUS_INVALID_MESSAGE)
    return candidate


def resolve_report_scope(actor: User, doctor_id: int | None) -> ReportScope:
    """Apply the ownership rule of rule 6 from the role read in the database."""
    if actor.role.name == MEDICO_ROLE:
        if doctor_id is not None:
            raise _invalid("doctor_id", DOCTOR_FILTER_FORBIDDEN_MESSAGE)
        return ReportScope(doctor_id=actor.doctor_id, empty=actor.doctor_id is None)
    return ReportScope(doctor_id=doctor_id, empty=False)


def csv_filename(now: datetime | None = None) -> str:
    """Build `reporte_visitas_<AAAAMMDD>.csv` with the generation date in RD (rule 16)."""
    generated_at = to_utc(now) if now is not None else datetime.now(UTC)
    local_date = generated_at.astimezone(SANTO_DOMINGO)
    return f"{CSV_FILENAME_PREFIX}_{local_date:%Y%m%d}.csv"


def _csv_datetime(value: datetime) -> str:
    """ISO 8601 date of the CSV row in America/Santo_Domingo (RNF-12)."""
    return to_utc(value).astimezone(SANTO_DOMINGO).isoformat()


def _csv_row(visit: Visit) -> list[str]:
    """Build one CSV row of the D-5 columns (rule 16)."""
    return [
        str(visit.id),
        _csv_datetime(visit.visited_at),
        visit.doctor.name,
        visit.doctor.cedula,
        visit.patient.name,
        visit.patient.cedula,
        visit.symptoms,
        visit.recommendations or "",
        visit.status,
        str(visit.created_by),
    ]


def serialize_visits_csv(visits: Iterable[Visit]) -> bytes:
    """Serialize the report detail as CSV with the UTF-8 BOM Excel expects (rules 16-17)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for visit in visits:
        writer.writerow(_csv_row(visit))
    return codecs.BOM_UTF8 + buffer.getvalue().encode("utf-8")


def _log_report(
    *,
    actor: User,
    report_format: str,
    dates: ReportDateRange,
    doctor_id: int | None,
    patient_id: int | None,
    status: str | None,
) -> None:
    """Emit `report.generated` with ids and filters only (rule 18)."""
    logger.info(
        "report.generated",
        actor_id=actor.id,
        format=report_format,
        date_from=dates.raw_from,
        date_to=dates.raw_to,
        doctor_id=doctor_id,
        patient_id=patient_id,
        status=status,
    )


async def build_report(
    session: AsyncSession,
    *,
    actor: User,
    date_from: str | None,
    date_to: str | None,
    doctor_id: int | None = None,
    patient_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    sort: str = DEFAULT_SORT,
) -> ReportResponse:
    """Build the paginated JSON report of rules 11-14."""
    dates = resolve_report_dates(date_from, date_to)
    resolved_status = resolve_report_status(status)
    sort_field, sort_desc = parse_sort(sort)
    scope = resolve_report_scope(actor, doctor_id)

    total = 0
    doctor_totals: list[tuple[int, str, int]] = []
    visits: list[Visit] = []
    if not scope.empty:
        total, doctor_totals = await visit_repository.summarize_visits(
            session,
            doctor_id=scope.doctor_id,
            patient_id=patient_id,
            date_from=dates.start,
            date_to=dates.end,
            status=resolved_status,
        )
        if total:
            visits, _ = await visit_repository.list_visits(
                session,
                page=page,
                size=size,
                doctor_id=scope.doctor_id,
                patient_id=patient_id,
                date_from=dates.start,
                date_to=dates.end,
                medication_id=None,
                status=resolved_status,
                q=None,
                sort_field=sort_field,
                sort_desc=sort_desc,
            )

    _log_report(
        actor=actor,
        report_format=REPORT_FORMAT_JSON,
        dates=dates,
        doctor_id=scope.doctor_id,
        patient_id=patient_id,
        status=resolved_status,
    )
    return ReportResponse(
        filters=ReportFilters(
            date_from=dates.raw_from,
            date_to=dates.raw_to,
            doctor_id=doctor_id,
            patient_id=patient_id,
            status=resolved_status,
        ),
        summary=ReportSummary(
            total_visits=total,
            by_doctor=[
                DoctorTotal(doctor_id=item_doctor_id, doctor_name=name, total=item_total)
                for item_doctor_id, name, item_total in doctor_totals
            ],
        ),
        items=[to_list_item(visit) for visit in visits],
        total=total,
        page=page,
        size=size,
        pages=calculate_pages(total, size),
    )


async def export_report_csv(
    session: AsyncSession,
    *,
    actor: User,
    date_from: str | None,
    date_to: str | None,
    doctor_id: int | None = None,
    patient_id: int | None = None,
    status: str | None = None,
) -> ReportCsvExport:
    """Export the whole report as CSV, without pagination, ordered `-visited_at` (D-7)."""
    dates = resolve_report_dates(date_from, date_to)
    resolved_status = resolve_report_status(status)
    scope = resolve_report_scope(actor, doctor_id)

    visits: list[Visit] = []
    if not scope.empty:
        total, _ = await visit_repository.summarize_visits(
            session,
            doctor_id=scope.doctor_id,
            patient_id=patient_id,
            date_from=dates.start,
            date_to=dates.end,
            status=resolved_status,
        )
        if total:
            visits, _ = await visit_repository.list_visits(
                session,
                page=1,
                size=total,
                doctor_id=scope.doctor_id,
                patient_id=patient_id,
                date_from=dates.start,
                date_to=dates.end,
                medication_id=None,
                status=resolved_status,
                q=None,
                sort_field="visited_at",
                sort_desc=True,
            )

    _log_report(
        actor=actor,
        report_format=REPORT_FORMAT_CSV,
        dates=dates,
        doctor_id=scope.doctor_id,
        patient_id=patient_id,
        status=resolved_status,
    )
    return ReportCsvExport(filename=csv_filename(), content=serialize_visits_csv(visits))
