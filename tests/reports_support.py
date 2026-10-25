"""Utilidades de apoyo para los tests de FEAT-014 (reporte de visitas)."""

from __future__ import annotations

import codecs
import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import Response
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services.report_service import CSV_HEADER
from app.services.visit_service import SANTO_DOMINGO
from tests.db_support import fetch_all
from tests.visits_support import (
    admin_actor,
    assert_error_envelope,
    assert_field_error,
    doctor_row,
    medico_actor,
    patient_row,
    register_visit,
)

REPORTS_PATH = "/api/v1/reports/visits"
REPORT_EXPORT_PATH = f"{REPORTS_PATH}/export"

#: Claves exactas de la respuesta JSON del reporte (reglas 11-14, CA-9 a CA-11).
REPORT_KEYS = {"filters", "summary", "items", "total", "page", "size", "pages"}
FILTER_KEYS = {"date_from", "date_to", "doctor_id", "patient_id", "status"}
SUMMARY_KEYS = {"total_visits", "by_doctor"}
DOCTOR_TOTAL_KEYS = {"doctor_id", "doctor_name", "total"}

#: Rango amplio por defecto: cada test aisla sus visitas con medico/paciente propios.
WIDE_FROM = "2000-01-01T00:00:00"
WIDE_TO = "2099-12-31T23:59:59"

__all__ = [
    "CSV_HEADER",
    "DOCTOR_TOTAL_KEYS",
    "FILTER_KEYS",
    "REPORTS_PATH",
    "REPORT_EXPORT_PATH",
    "REPORT_KEYS",
    "SUMMARY_KEYS",
    "WIDE_FROM",
    "WIDE_TO",
    "admin_actor",
    "assert_csv_disposition",
    "assert_error_envelope",
    "assert_field_error",
    "csv_datetime",
    "doctor_identity",
    "doctor_row",
    "expected_filenames",
    "medico_actor",
    "parse_csv",
    "patient_identity",
    "patient_row",
    "register_visit",
    "report_params",
]


def report_params(
    *,
    date_from: str = WIDE_FROM,
    date_to: str = WIDE_TO,
    **extra: Any,
) -> dict[str, Any]:
    """Query del reporte con el rango obligatorio y los filtros indicados."""
    params: dict[str, Any] = {"date_from": date_from, "date_to": date_to}
    params.update(extra)
    return params


def parse_csv(response: Response) -> list[dict[str, str]]:
    """Valida BOM, `text/csv` y encabezado espanol exacto; devuelve las filas."""
    assert response.headers["content-type"].startswith("text/csv"), response.headers
    assert response.content.startswith(codecs.BOM_UTF8), response.content[:16]
    text = response.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    assert header == CSV_HEADER, header
    return [dict(zip(CSV_HEADER, row, strict=True)) for row in reader]


def expected_filenames(*, now: datetime | None = None) -> set[str]:
    """Nombres de archivo admisibles: la fecha de hoy en RD (+/- 2 min de margen)."""
    reference = now or datetime.now(UTC)
    names = set()
    for delta in (timedelta(0), timedelta(minutes=-2), timedelta(minutes=2)):
        local = (reference + delta).astimezone(SANTO_DOMINGO)
        names.add(f"reporte_visitas_{local:%Y%m%d}.csv")
    return names


def assert_csv_disposition(response: Response) -> str:
    """Comprueba `attachment; filename="reporte_visitas_<AAAAMMDD>.csv"` (regla 16)."""
    disposition = response.headers.get("content-disposition", "")
    prefix = 'attachment; filename="'
    assert disposition.startswith(prefix), disposition
    assert disposition.endswith('"'), disposition
    filename = disposition[len(prefix) : -1]
    assert filename in expected_filenames(), (filename, expected_filenames())
    return filename


def csv_datetime(value: datetime) -> str:
    """Fecha ISO 8601 del CSV, expresada en America/Santo_Domingo (RNF-12)."""
    return value.astimezone(SANTO_DOMINGO).isoformat()


async def doctor_identity(conn: AsyncConnection, doctor_id: int) -> tuple[str, str]:
    """Devuelve `(nombre, cedula)` actuales del medico."""
    rows = await fetch_all(
        conn,
        "SELECT name, cedula FROM doctors WHERE id = :doctor_id",
        {"doctor_id": doctor_id},
    )
    assert rows, doctor_id
    name, cedula = rows[0]
    return str(name), str(cedula)


async def patient_identity(conn: AsyncConnection, patient_id: int) -> tuple[str, str]:
    """Devuelve `(nombre, cedula)` actuales del paciente."""
    rows = await fetch_all(
        conn,
        "SELECT name, cedula FROM patients WHERE id = :patient_id",
        {"patient_id": patient_id},
    )
    assert rows, patient_id
    name, cedula = rows[0]
    return str(name), str(cedula)
