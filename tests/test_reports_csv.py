"""Exportacion CSV del reporte de visitas de FEAT-014 (CA-13 a CA-15)."""

from __future__ import annotations

import codecs
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.reports_support import (
    REPORT_EXPORT_PATH,
    REPORTS_PATH,
    admin_actor,
    assert_csv_disposition,
    csv_datetime,
    doctor_identity,
    doctor_row,
    medico_actor,
    parse_csv,
    patient_identity,
    patient_row,
    register_visit,
    report_params,
)

FIRST_VISIT_AT = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
SECOND_VISIT_AT = datetime(2026, 1, 16, 14, 0, tzinfo=UTC)
THIRD_VISIT_AT = datetime(2026, 1, 17, 14, 0, tzinfo=UTC)


async def test_ca13_export_returns_csv_with_bom_header_and_filename(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: `200 text/csv` con BOM, encabezado espanol y `Content-Disposition`."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )

    response = await auth_client.get(
        REPORT_EXPORT_PATH, headers=headers, params=report_params(patient_id=patient_id)
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    filename = assert_csv_disposition(response)
    assert filename.startswith("reporte_visitas_")
    assert filename.endswith(".csv")
    assert response.content.startswith(codecs.BOM_UTF8)
    assert len(parse_csv(response)) == 1


async def test_ca14_rows_have_iso_dates_quoted_text_and_no_pagination(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-14: filas correctas, `recomendaciones` vacia si null y sin paginacion."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    doctor_name, doctor_cedula = await doctor_identity(db_conn, doctor_id)
    patient_name, patient_cedula = await patient_identity(db_conn, patient_id)

    first = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=FIRST_VISIT_AT,
        symptoms="Dolor, fiebre y tos",
        recommendations=None,
    )
    second = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=SECOND_VISIT_AT,
        symptoms='Dijo "ay" ayer',
        recommendations="Reposo total",
    )
    third = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=THIRD_VISIT_AT,
        symptoms="Control rutinario",
        recommendations="Hidratación",
    )

    response = await auth_client.get(
        REPORT_EXPORT_PATH,
        headers=headers,
        params=report_params(patient_id=patient_id, page=1, size=1),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith(codecs.BOM_UTF8)

    rows = parse_csv(response)
    assert [row["id"] for row in rows] == [str(third), str(second), str(first)]

    oldest = rows[-1]
    assert oldest == {
        "id": str(first),
        "fecha": csv_datetime(FIRST_VISIT_AT),
        "medico": doctor_name,
        "cedula_medico": doctor_cedula,
        "paciente": patient_name,
        "cedula_paciente": patient_cedula,
        "sintomas": "Dolor, fiebre y tos",
        "recomendaciones": "",
        "estado": "REGISTRADA",
        "creado_por": str(admin_id),
    }
    assert csv_datetime(FIRST_VISIT_AT) == "2026-01-15T10:00:00-04:00"
    assert rows[1]["sintomas"] == 'Dijo "ay" ayer'
    assert rows[1]["recomendaciones"] == "Reposo total"


async def test_ca15_format_csv_matches_the_export_and_respects_filters_and_ownership(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-15: `format=csv` devuelve el mismo CSV; el export respeta filtros y propiedad."""
    admin_id, headers = await admin_actor(db_conn)
    own_doctor = await doctor_row(db_conn)
    other_doctor = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    other_patient = await patient_row(db_conn)

    own_visit = await register_visit(
        db_conn,
        doctor_id=own_doctor,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=FIRST_VISIT_AT,
    )
    other_visit = await register_visit(
        db_conn,
        doctor_id=other_doctor,
        patient_id=other_patient,
        created_by=admin_id,
        visited_at=SECOND_VISIT_AT,
    )

    params = report_params(doctor_id=own_doctor, patient_id=patient_id)
    inline = await auth_client.get(
        REPORTS_PATH, headers=headers, params={**params, "format": "csv"}
    )
    assert inline.status_code == 200, inline.text
    assert inline.headers["content-type"].startswith("text/csv")
    assert_csv_disposition(inline)

    export = await auth_client.get(REPORT_EXPORT_PATH, headers=headers, params=params)
    assert export.status_code == 200, export.text
    assert inline.content == export.content
    assert [row["id"] for row in parse_csv(inline)] == [str(own_visit)]

    by_patient = await auth_client.get(
        REPORT_EXPORT_PATH,
        headers=headers,
        params=report_params(patient_id=other_patient),
    )
    assert [row["id"] for row in parse_csv(by_patient)] == [str(other_visit)]

    _, medico_headers = await medico_actor(db_conn, doctor_id=own_doctor)
    medico_export = await auth_client.get(
        REPORT_EXPORT_PATH, headers=medico_headers, params=report_params()
    )
    assert medico_export.status_code == 200, medico_export.text
    assert [row["id"] for row in parse_csv(medico_export)] == [str(own_visit)]

    medico_inline = await auth_client.get(
        REPORTS_PATH, headers=medico_headers, params={**report_params(), "format": "csv"}
    )
    assert medico_inline.status_code == 200, medico_inline.text
    assert medico_inline.content == medico_export.content
