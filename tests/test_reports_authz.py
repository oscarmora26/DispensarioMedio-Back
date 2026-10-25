"""Autorizacion y propiedad del reporte de visitas (CA-1, CA-2, CA-6)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.schemas.visit import DOCTOR_FILTER_FORBIDDEN_MESSAGE
from tests.auth_support import auth_headers, create_auth_user, token_for
from tests.db_support import unique_suffix
from tests.reports_support import (
    REPORT_EXPORT_PATH,
    REPORTS_PATH,
    admin_actor,
    assert_error_envelope,
    assert_field_error,
    doctor_row,
    medico_actor,
    parse_csv,
    patient_row,
    register_visit,
    report_params,
)

INVALID_TOKEN = {"Authorization": "Bearer not-a-valid-token"}
REPORT_ENDPOINTS = (REPORTS_PATH, REPORT_EXPORT_PATH)


async def _fixture(
    db_conn: AsyncConnection,
) -> tuple[int, dict[str, str], int, int, int, int, int]:
    """ADMIN, dos medicos, un paciente y una visita de cada medico."""
    admin_id, admin_headers = await admin_actor(db_conn)
    own_doctor = await doctor_row(db_conn)
    other_doctor = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    own_visit = await register_visit(
        db_conn,
        doctor_id=own_doctor,
        patient_id=patient_id,
        created_by=admin_id,
    )
    other_visit = await register_visit(
        db_conn,
        doctor_id=other_doctor,
        patient_id=patient_id,
        created_by=admin_id,
    )
    return (
        admin_id,
        admin_headers,
        own_doctor,
        other_doctor,
        patient_id,
        own_visit,
        other_visit,
    )


async def test_ca1_admin_and_medico_read_json_and_csv(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: los dos endpoints responden `200` a `ADMIN` y a `MEDICO` (propio)."""
    _, admin_headers, own_doctor, _, patient_id, own_visit, other_visit = await _fixture(db_conn)
    _, medico_headers = await medico_actor(db_conn, doctor_id=own_doctor)
    params = report_params(patient_id=patient_id)

    admin_report = await auth_client.get(REPORTS_PATH, headers=admin_headers, params=params)
    assert admin_report.status_code == 200, admin_report.text
    assert {item["id"] for item in admin_report.json()["items"]} == {own_visit, other_visit}

    medico_report = await auth_client.get(REPORTS_PATH, headers=medico_headers, params=params)
    assert medico_report.status_code == 200, medico_report.text
    assert [item["id"] for item in medico_report.json()["items"]] == [own_visit]

    admin_export = await auth_client.get(REPORT_EXPORT_PATH, headers=admin_headers, params=params)
    assert admin_export.status_code == 200, admin_export.text
    assert {row["id"] for row in parse_csv(admin_export)} == {str(own_visit), str(other_visit)}

    medico_export = await auth_client.get(REPORT_EXPORT_PATH, headers=medico_headers, params=params)
    assert medico_export.status_code == 200, medico_export.text
    assert [row["id"] for row in parse_csv(medico_export)] == [str(own_visit)]


@pytest.mark.parametrize("headers", [None, INVALID_TOKEN], ids=["sin-token", "token-invalido"])
async def test_ca2_both_endpoints_reject_missing_or_invalid_token(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    headers: dict[str, str] | None,
) -> None:
    """CA-2: sin token o con token invalido ⇒ `401` en los dos endpoints."""
    *_, patient_id, _, _ = await _fixture(db_conn)
    expected_code = "not_authenticated" if headers is None else "invalid_token"

    for path in REPORT_ENDPOINTS:
        response = await auth_client.get(
            path,
            headers=headers,
            params=report_params(patient_id=patient_id),
        )
        assert_error_envelope(response, 401, expected_code)


async def test_ca2_inactive_user_is_rejected_on_both_endpoints(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: usuario desactivado ⇒ `403` `inactive_user` en los dos endpoints."""
    *_, patient_id, _, _ = await _fixture(db_conn)
    suffix = unique_suffix()
    inactive_id = await create_auth_user(
        db_conn,
        username=f"rinactivo-{suffix}",
        email=f"rinactivo.{suffix}@example.test",
        role="ADMIN",
        is_active=False,
    )
    inactive_headers = auth_headers(token_for(inactive_id))

    for path in REPORT_ENDPOINTS:
        response = await auth_client.get(
            path,
            headers=inactive_headers,
            params=report_params(patient_id=patient_id),
        )
        body = assert_error_envelope(response, 403, "inactive_user")
        assert body["detail"] == "El usuario está inactivo"


async def test_ca6_medico_sees_only_own_visits_and_cannot_filter_by_doctor(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: propiedad en `total_visits`, `by_doctor` e `items`; `doctor_id` ⇒ 422."""
    admin_id, _, own_doctor, _, _, _, _ = await _fixture(db_conn)
    other_doctor = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    first_own = await register_visit(
        db_conn,
        doctor_id=own_doctor,
        patient_id=patient_id,
        created_by=admin_id,
    )
    second_own = await register_visit(
        db_conn,
        doctor_id=own_doctor,
        patient_id=patient_id,
        created_by=admin_id,
    )
    await register_visit(
        db_conn,
        doctor_id=other_doctor,
        patient_id=patient_id,
        created_by=admin_id,
    )
    _, medico_headers = await medico_actor(db_conn, doctor_id=own_doctor)

    report = await auth_client.get(
        REPORTS_PATH,
        headers=medico_headers,
        params=report_params(patient_id=patient_id),
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["total"] == 2
    assert body["summary"]["total_visits"] == 2
    assert [entry["doctor_id"] for entry in body["summary"]["by_doctor"]] == [own_doctor]
    assert body["summary"]["by_doctor"][0]["total"] == 2
    assert {item["id"] for item in body["items"]} == {first_own, second_own}
    assert {item["doctor"]["id"] for item in body["items"]} == {own_doctor}

    for path in REPORT_ENDPOINTS:
        forbidden = await auth_client.get(
            path,
            headers=medico_headers,
            params=report_params(doctor_id=other_doctor),
        )
        assert_field_error(
            forbidden,
            422,
            "validation_error",
            "doctor_id",
            DOCTOR_FILTER_FORBIDDEN_MESSAGE,
        )


async def test_ca6_medico_without_doctor_gets_an_empty_report(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: un `MEDICO` sin medico asociado obtiene reporte vacio."""
    admin_id, _, doctor_id, _, patient_id, _, _ = await _fixture(db_conn)
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )
    _, orphan_headers = await medico_actor(db_conn)
    params: dict[str, Any] = report_params(patient_id=patient_id)

    report = await auth_client.get(REPORTS_PATH, headers=orphan_headers, params=params)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["filters"] == {
        "date_from": params["date_from"],
        "date_to": params["date_to"],
        "doctor_id": None,
        "patient_id": patient_id,
        "status": None,
    }
    assert body["summary"] == {"total_visits": 0, "by_doctor": []}
    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0

    export = await auth_client.get(REPORT_EXPORT_PATH, headers=orphan_headers, params=params)
    assert export.status_code == 200, export.text
    assert parse_csv(export) == []
