"""Logs del reporte de visitas (CA-16): ids y filtros, sin datos personales."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import unique_suffix
from tests.reports_support import (
    REPORT_EXPORT_PATH,
    REPORTS_PATH,
    WIDE_FROM,
    WIDE_TO,
    admin_actor,
    doctor_identity,
    doctor_row,
    patient_identity,
    patient_row,
    register_visit,
    report_params,
)


def _events(records: list[logging.LogRecord], name: str) -> list[logging.LogRecord]:
    """Filtra los registros de structlog por nombre de evento."""
    return [
        record
        for record in records
        if isinstance(record.msg, dict) and record.msg.get("event") == name
    ]


async def test_ca16_report_generated_carries_ids_and_filters_only(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-16: `report.generated` con `actor_id`, `format` y filtros; sin textos clinicos."""
    actor_id, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    doctor_id = await doctor_row(db_conn, name=f"Dr. Reservado {tag}")
    patient_id = await patient_row(db_conn, name=f"Paciente Reservado {tag}")
    _, doctor_cedula = await doctor_identity(db_conn, doctor_id)
    _, patient_cedula = await patient_identity(db_conn, patient_id)
    symptoms = f"Sintoma confidencial {tag}"
    recommendations = f"Recomendacion confidencial {tag}"
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=actor_id,
        symptoms=symptoms,
        recommendations=recommendations,
    )

    params = report_params(doctor_id=doctor_id, patient_id=patient_id)
    caplog.set_level(logging.INFO)

    json_report = await auth_client.get(REPORTS_PATH, headers=headers, params=params)
    assert json_report.status_code == 200, json_report.text
    inline_csv = await auth_client.get(
        REPORTS_PATH, headers=headers, params={**params, "format": "csv"}
    )
    assert inline_csv.status_code == 200, inline_csv.text
    exported = await auth_client.get(REPORT_EXPORT_PATH, headers=headers, params=params)
    assert exported.status_code == 200, exported.text

    events = _events(list(caplog.records), "report.generated")
    assert len(events) == 3
    payloads = [record.msg for record in events]
    assert [payload["format"] for payload in payloads] == ["json", "csv", "csv"]

    for record, payload in zip(events, payloads, strict=True):
        assert payload["actor_id"] == actor_id
        assert payload["date_from"] == WIDE_FROM
        assert payload["date_to"] == WIDE_TO
        assert payload["doctor_id"] == doctor_id
        assert payload["patient_id"] == patient_id
        assert payload["status"] is None
        assert {"event", "actor_id", "format"} <= set(payload)

        text = f"{payload} {record.getMessage()}"
        for forbidden in (
            symptoms,
            recommendations,
            tag,
            f"Dr. Reservado {tag}",
            f"Paciente Reservado {tag}",
            doctor_cedula,
            patient_cedula,
        ):
            assert forbidden not in text
        for forbidden_key in (
            "symptoms",
            "recommendations",
            "cedula",
            "doctor_name",
            "patient_name",
            "items",
        ):
            assert forbidden_key not in payload
