"""Unitarias sin base de datos de FEAT-014 (validacion, CSV y OpenAPI)."""

from __future__ import annotations

import codecs
import csv
import io
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.exceptions import AppError
from app.main import app
from app.schemas.report import (
    DATE_FROM_REQUIRED_MESSAGE,
    DATE_INVALID_MESSAGE,
    DATE_TO_REQUIRED_MESSAGE,
    FORMAT_INVALID_MESSAGE,
    PDF_FORMAT_MESSAGE,
    REPORT_FORMAT_VALUES,
    REPORT_SORT_PATTERN,
    STATUS_INVALID_MESSAGE,
    ReportResponse,
)
from app.schemas.visit import DATE_RANGE_MESSAGE, SORT_PATTERN
from app.services.report_service import (
    CSV_HEADER,
    ReportCsvExport,
    ReportDateRange,
    csv_filename,
    resolve_report_dates,
    resolve_report_format,
    resolve_report_status,
    serialize_visits_csv,
)

REPORTS_OPENAPI_PATH = "/api/v1/reports/visits"
EXPORT_OPENAPI_PATH = "/api/v1/reports/visits/export"


def _assert_invalid(error: AppError, field: str, message: str) -> None:
    """Comprueba el 422 de campo unico que devuelve el servicio."""
    assert error.status_code == 422
    assert error.code == "validation_error"
    assert error.errors == [{"field": field, "message": message}]


def _fake_visit(**overrides: Any) -> SimpleNamespace:
    """Visita minimima con la forma que consume el serializador CSV."""
    values: dict[str, Any] = {
        "id": 7,
        "visited_at": datetime(2026, 10, 15, 14, 0, tzinfo=UTC),
        "symptoms": "Dolor, fiebre y tos",
        "recommendations": None,
        "status": "REGISTRADA",
        "created_by": 5,
        "doctor": SimpleNamespace(id=3, name="Dra. Ana Pérez", cedula="00112345678"),
        "patient": SimpleNamespace(id=9, name="Luis Pérez", cedula="00298765432"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# --- CA-3 / CA-4: rango obligatorio, eco literal y conversion a UTC -------------


def test_ca3_both_dates_are_required_with_their_own_field() -> None:
    """CA-3: sin `date_from` o sin `date_to` ⇒ 422 con el campo correspondiente."""
    with pytest.raises(AppError) as missing_from:
        resolve_report_dates(None, "2026-10-31T23:59:59")
    _assert_invalid(missing_from.value, "date_from", DATE_FROM_REQUIRED_MESSAGE)

    with pytest.raises(AppError) as missing_to:
        resolve_report_dates("2026-10-01T00:00:00", None)
    _assert_invalid(missing_to.value, "date_to", DATE_TO_REQUIRED_MESSAGE)

    for blank in ("", "   "):
        with pytest.raises(AppError) as blank_from:
            resolve_report_dates(blank, "2026-10-31")
        _assert_invalid(blank_from.value, "date_from", DATE_FROM_REQUIRED_MESSAGE)

        with pytest.raises(AppError) as blank_to:
            resolve_report_dates("2026-10-01", blank)
        _assert_invalid(blank_to.value, "date_to", DATE_TO_REQUIRED_MESSAGE)


def test_ca4_range_converts_naive_to_santo_domingo_and_echoes_the_raw_query() -> None:
    """CA-4: naive se asume en RD (UTC-4) y `filters` conserva la cadena original."""
    resolved = resolve_report_dates("2026-10-01T08:30:00-04:00", "2026-10-31T23:59:59")
    assert isinstance(resolved, ReportDateRange)
    assert (resolved.raw_from, resolved.raw_to) == (
        "2026-10-01T08:30:00-04:00",
        "2026-10-31T23:59:59",
    )
    assert resolved.start == datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
    assert resolved.end == datetime(2026, 11, 1, 3, 59, 59, tzinfo=UTC)

    date_only = resolve_report_dates("2026-10-01", "2026-10-01")
    assert date_only.start == datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
    assert date_only.end == date_only.start

    aware = resolve_report_dates("2026-10-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00")
    assert aware.start == datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


def test_ca4_inverted_and_malformed_dates_are_rejected_with_their_field() -> None:
    """CA-4: `date_from > date_to` ⇒ 422 en `date_from`; texto invalido ⇒ 422."""
    with pytest.raises(AppError) as inverted:
        resolve_report_dates("2026-10-31", "2026-10-01")
    _assert_invalid(inverted.value, "date_from", DATE_RANGE_MESSAGE)
    assert inverted.value.errors[0]["message"] == (
        "La fecha inicial no puede ser mayor que la final"
    )

    with pytest.raises(AppError) as malformed_from:
        resolve_report_dates("31/10/2026", "2026-10-31")
    _assert_invalid(malformed_from.value, "date_from", DATE_INVALID_MESSAGE)

    with pytest.raises(AppError) as malformed_to:
        resolve_report_dates("2026-10-01", "no-es-fecha")
    _assert_invalid(malformed_to.value, "date_to", DATE_INVALID_MESSAGE)


# --- CA-5 / CA-12: catalogos de estado, orden y formato ------------------------


def test_ca5_status_catalog_is_closed() -> None:
    """CA-5: `status` fuera de `REGISTRADA`/`ANULADA` ⇒ 422 con `field == "status"`."""
    assert resolve_report_status(None) is None
    assert resolve_report_status("   ") is None
    assert resolve_report_status("REGISTRADA") == "REGISTRADA"
    assert resolve_report_status("ANULADA") == "ANULADA"

    with pytest.raises(AppError) as invalid:
        resolve_report_status("BORRADA")
    _assert_invalid(invalid.value, "status", STATUS_INVALID_MESSAGE)


def test_ca5_sort_pattern_is_the_closed_feat013_catalog() -> None:
    """CA-5: el catalogo de `sort` es el de FEAT-013 D-6, con prefijo `-`."""
    assert REPORT_SORT_PATTERN == SORT_PATTERN
    for accepted in (
        "visited_at",
        "-visited_at",
        "created_at",
        "-created_at",
        "id",
        "-id",
        "status",
        "-status",
    ):
        assert re.fullmatch(REPORT_SORT_PATTERN, accepted), accepted
    for rejected in ("doctor", "visited", "-", "", "doctor_name", "-doctor"):
        assert re.fullmatch(REPORT_SORT_PATTERN, rejected) is None, rejected


def test_ca12_format_catalog_default_and_pdf_rejection() -> None:
    """CA-12: `json` por defecto, `csv` aceptado y `pdf` rechazado con su mensaje."""
    assert REPORT_FORMAT_VALUES == ("json", "csv")
    assert resolve_report_format(None, default="json") == "json"
    assert resolve_report_format("  ", default="csv") == "csv"
    assert resolve_report_format("json", default="csv") == "json"
    assert resolve_report_format("csv", default="json") == "csv"

    with pytest.raises(AppError) as pdf:
        resolve_report_format("pdf", default="json")
    _assert_invalid(pdf.value, "format", PDF_FORMAT_MESSAGE)
    assert pdf.value.errors[0]["message"] == "El formato PDF no está disponible en la versión 1"

    with pytest.raises(AppError) as unknown:
        resolve_report_format("xlsx", default="csv")
    _assert_invalid(unknown.value, "format", FORMAT_INVALID_MESSAGE)


# --- CA-13 / CA-14: serializacion CSV ------------------------------------------


def test_ca13_csv_starts_with_bom_and_the_exact_spanish_header() -> None:
    """CA-13: BOM UTF-8 y encabezado espanol exacto; sin filas queda solo la cabecera."""
    document = serialize_visits_csv([])
    assert document.startswith(codecs.BOM_UTF8)
    assert document.decode("utf-8-sig") == ",".join(CSV_HEADER) + "\r\n"
    assert CSV_HEADER == [
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


def test_ca13_filename_uses_the_generation_date_in_santo_domingo() -> None:
    """CA-13: `reporte_visitas_<AAAAMMDD>.csv` con la fecha de generacion en RD."""
    assert csv_filename(datetime(2026, 10, 24, 23, 30, tzinfo=UTC)) == (
        "reporte_visitas_20261024.csv"
    )
    assert csv_filename(datetime(2026, 10, 25, 2, 0, tzinfo=UTC)) == "reporte_visitas_20261024.csv"
    assert csv_filename(datetime(2026, 10, 25, 4, 0, tzinfo=UTC)) == "reporte_visitas_20261025.csv"
    assert csv_filename(datetime(2026, 10, 24, 23, 30)) == "reporte_visitas_20261024.csv"

    export = ReportCsvExport(filename="reporte_visitas_20261024.csv", content=b"")
    assert export.content_disposition == 'attachment; filename="reporte_visitas_20261024.csv"'


def test_ca14_rows_use_iso_dates_quotes_commas_and_empty_recommendations() -> None:
    """CA-14: fechas ISO en RD, `recomendaciones` vacia si null y quoting del modulo `csv`."""
    empty_recommendations = _fake_visit()
    quoted = _fake_visit(
        id=8,
        symptoms='Dijo "ay" ayer, con fiebre',
        recommendations="Reposo total",
    )
    document = serialize_visits_csv([empty_recommendations, quoted])
    text = document.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))

    assert rows[0] == CSV_HEADER
    assert rows[1] == [
        "7",
        "2026-10-15T10:00:00-04:00",
        "Dra. Ana Pérez",
        "00112345678",
        "Luis Pérez",
        "00298765432",
        "Dolor, fiebre y tos",
        "",
        "REGISTRADA",
        "5",
    ]
    assert rows[2][6] == 'Dijo "ay" ayer, con fiebre'
    assert rows[2][7] == "Reposo total"
    assert '"Dolor, fiebre y tos"' in text
    assert '"Dijo ""ay"" ayer, con fiebre"' in text


# --- CA-9 / CA-10 / CA-11: forma del JSON (ejemplo literal de la regla 11) ------


def test_rules11_to14_report_response_shape_contains_filters_summary_and_items() -> None:
    """CA-9 a CA-11: la forma del §12 valida contra `ReportResponse` sin perder claves."""
    report = ReportResponse.model_validate(
        {
            "filters": {
                "date_from": "2026-10-01",
                "date_to": "2026-10-31",
                "doctor_id": None,
                "patient_id": None,
                "status": "REGISTRADA",
            },
            "summary": {
                "total_visits": 120,
                "by_doctor": [{"doctor_id": 5, "doctor_name": "Dra. Ana Pérez", "total": 80}],
            },
            "items": [
                {
                    "id": 11,
                    "doctor": {"id": 5, "name": "Dra. Ana Pérez"},
                    "patient": {"id": 9, "name": "Luis Pérez", "cedula": "00298765432"},
                    "visited_at": "2026-10-15T10:00:00-04:00",
                    "symptoms": "Dolor de cabeza",
                    "status": "REGISTRADA",
                    "created_by": 3,
                }
            ],
            "total": 1,
            "page": 1,
            "size": 20,
            "pages": 1,
        }
    )
    dumped = report.model_dump(mode="json")

    assert set(dumped) == {"filters", "summary", "items", "total", "page", "size", "pages"}
    assert set(dumped["filters"]) == {"date_from", "date_to", "doctor_id", "patient_id", "status"}
    assert dumped["filters"] == {
        "date_from": "2026-10-01",
        "date_to": "2026-10-31",
        "doctor_id": None,
        "patient_id": None,
        "status": "REGISTRADA",
    }
    assert set(dumped["summary"]) == {"total_visits", "by_doctor"}
    assert dumped["summary"]["total_visits"] == 120
    assert set(dumped["summary"]["by_doctor"][0]) == {"doctor_id", "doctor_name", "total"}
    assert set(dumped["items"][0]) == {
        "id",
        "doctor",
        "patient",
        "visited_at",
        "symptoms",
        "status",
        "created_by",
    }
    assert set(dumped["items"][0]["doctor"]) == {"id", "name"}
    assert set(dumped["items"][0]["patient"]) == {"id", "name", "cedula"}


# --- CA-17: contrato OpenAPI ---------------------------------------------------


def test_ca17_openapi_documents_both_report_routes() -> None:
    """CA-17: las dos rutas con tag `reports`, summary, description y codigos."""
    spec = app.openapi()
    paths = spec["paths"]

    report_paths = sorted(path for path in paths if path.startswith("/api/v1/reports"))
    assert report_paths == [REPORTS_OPENAPI_PATH, EXPORT_OPENAPI_PATH]

    report = paths[REPORTS_OPENAPI_PATH]["get"]
    export = paths[EXPORT_OPENAPI_PATH]["get"]

    for operation in (report, export):
        assert operation["tags"] == ["reports"]
        assert operation["summary"]
        assert operation["description"]
        assert operation["security"]
        assert {"200", "401", "403", "422"} <= set(operation["responses"])
        parameters = {parameter["name"] for parameter in operation["parameters"]}
        assert {"date_from", "date_to", "doctor_id", "patient_id", "status", "format"} <= (
            parameters
        )

    assert set(report["responses"]["200"]["content"]) == {"application/json", "text/csv"}
    assert "text/csv" in export["responses"]["200"]["content"]
    assert set(report["responses"]) == {"200", "401", "403", "422"}
    assert set(export["responses"]) == {"200", "401", "403", "422"}
