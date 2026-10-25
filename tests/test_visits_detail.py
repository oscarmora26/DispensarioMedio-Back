"""Detalle de visitas de FEAT-013 (CA-23 y CA-24)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.schemas.visit import VISIT_STATUS_VALUES
from tests.visits_support import (
    ANNULLED_KEYS,
    DETAIL_KEYS,
    DOCTOR_REF_KEYS,
    MEDICATION_KEYS,
    PATIENT_REF_KEYS,
    VISITS_PATH,
    admin_actor,
    annul_path,
    assert_error_envelope,
    create_medication_row,
    doctor_row,
    medico_actor,
    nonexistent_visit_id,
    patient_row,
    register_visit,
    visit_path,
)


async def test_ca23_detail_returns_d7_shape_with_medications(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-23: el detalle trae la forma D-7 con los medicamentos de la visita."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    medication_id = await create_medication_row(db_conn, description="Amoxicilina", dose="500 mg")
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        symptoms="Dolor de garganta",
        recommendations="Reposo e hidratación",
        medications=[(medication_id, 2, "Cada 8 horas")],
    )

    detail = await auth_client.get(visit_path(visit_id), headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()

    assert set(body) == DETAIL_KEYS
    assert body["id"] == visit_id
    assert body["status"] in VISIT_STATUS_VALUES
    assert body["status"] == "REGISTRADA"
    assert body["doctor"]["id"] == doctor_id
    assert set(body["doctor"]) == DOCTOR_REF_KEYS
    assert set(body["patient"]) == PATIENT_REF_KEYS
    assert body["patient"]["id"] == patient_id
    assert body["symptoms"] == "Dolor de garganta"
    assert body["recommendations"] == "Reposo e hidratación"
    assert body["created_by"] == admin_id

    assert len(body["medications"]) == 1
    medication = body["medications"][0]
    assert set(medication) == MEDICATION_KEYS
    assert medication["medication_id"] == medication_id
    assert medication["description"] == "Amoxicilina"
    assert medication["dose"] == "500 mg"
    assert float(medication["quantity"]) == 2.0
    assert medication["instructions"] == "Cada 8 horas"


async def test_ca23_annulled_visit_detail_includes_annulment_fields(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-23: una visita anulada expone `annulled_at`, `annulled_by` y el motivo."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )

    annulled = await auth_client.post(
        annul_path(visit_id),
        headers=headers,
        json={"reason": "  Visita registrada por error  "},
    )
    assert annulled.status_code == 200, annulled.text

    detail = await auth_client.get(visit_path(visit_id), headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()

    assert set(body) == DETAIL_KEYS | ANNULLED_KEYS
    assert body["status"] == "ANULADA"
    assert body["annulled_by"] == admin_id
    assert body["annulment_reason"] == "Visita registrada por error"
    assert body["annulled_at"] == annulled.json()["annulled_at"]


async def test_ca24_missing_and_invalid_identifiers(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-24: visita inexistente ⇒ 404 `visit_not_found`; id no entero ⇒ 422."""
    _, headers = await admin_actor(db_conn)

    missing = await auth_client.get(
        visit_path(await nonexistent_visit_id(db_conn)), headers=headers
    )
    body = assert_error_envelope(missing, 404, "visit_not_found")
    assert body["detail"] == "La visita no existe"

    invalid = await auth_client.get(f"{VISITS_PATH}/abc", headers=headers)
    assert_error_envelope(invalid, 422, "validation_error")


async def test_ca24_medico_gets_404_for_foreign_visit(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-24: para `MEDICO` una visita ajena (exista o no) responde 404."""
    admin_id, _ = await admin_actor(db_conn)
    own_doctor = await doctor_row(db_conn)
    other_doctor = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    own_visit = await register_visit(
        db_conn,
        doctor_id=own_doctor,
        patient_id=patient_id,
        created_by=admin_id,
    )
    foreign_visit = await register_visit(
        db_conn,
        doctor_id=other_doctor,
        patient_id=patient_id,
        created_by=admin_id,
    )

    _, headers = await medico_actor(db_conn, doctor_id=own_doctor)
    own = await auth_client.get(visit_path(own_visit), headers=headers)
    assert own.status_code == 200, own.text

    foreign = await auth_client.get(visit_path(foreign_visit), headers=headers)
    body = assert_error_envelope(foreign, 404, "visit_not_found")
    assert body["detail"] == "La visita no existe"

    missing = await auth_client.get(
        visit_path(await nonexistent_visit_id(db_conn)), headers=headers
    )
    assert_error_envelope(missing, 404, "visit_not_found")
