"""Anulacion de visitas de FEAT-013 (CA-29 a CA-31, D-12)."""

from __future__ import annotations

from datetime import datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.schemas.visit import REASON_MAX_MESSAGE, REASON_REQUIRED_MESSAGE
from tests.visits_support import (
    ANNUL_RESPONSE_KEYS,
    VISITS_PATH,
    admin_actor,
    annul_path,
    assert_error_envelope,
    assert_field_error,
    doctor_row,
    fetch_visit_row,
    nonexistent_visit_id,
    past_visited_at,
    patient_row,
    register_visit,
    visit_body,
    visit_path,
)


async def test_ca29_annul_sets_audit_fields_and_returns_documented_shape(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-29: `annul` válido ⇒ 200 con la forma del §11 y la visita queda ANULADA."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )
    before = await fetch_visit_row(db_conn, visit_id)
    assert before is not None

    response = await auth_client.post(
        annul_path(visit_id),
        headers=headers,
        json={"reason": "  Visita registrada por error  "},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == ANNUL_RESPONSE_KEYS
    assert body["id"] == visit_id
    assert body["status"] == "ANULADA"
    assert body["annulled_by"] == admin_id
    assert body["annulment_reason"] == "Visita registrada por error"
    assert datetime.fromisoformat(body["annulled_at"])

    row = await fetch_visit_row(db_conn, visit_id)
    assert row is not None
    assert row["status"] == "ANULADA"
    assert row["annulled_by"] == admin_id
    assert row["annulment_reason"] == "Visita registrada por error"
    assert row["annulled_at"] is not None
    assert row["annulled_at"].isoformat() == datetime.fromisoformat(body["annulled_at"]).isoformat()
    assert row["updated_at"] > row["created_at"]
    assert row["doctor_id"] == before["doctor_id"]
    assert row["patient_id"] == before["patient_id"]
    assert row["symptoms"] == before["symptoms"]
    assert row["created_by"] == before["created_by"]


async def test_ca30_reason_is_required_and_bounded(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-30: motivo vacio o de solo espacios ⇒ 422; >500 ⇒ 422; nada se anula."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )
    before = await fetch_visit_row(db_conn, visit_id)

    for invalid in ("", "    "):
        rejected = await auth_client.post(
            annul_path(visit_id), headers=headers, json={"reason": invalid}
        )
        assert_field_error(rejected, 422, "validation_error", "reason", REASON_REQUIRED_MESSAGE)

    too_long = await auth_client.post(
        annul_path(visit_id), headers=headers, json={"reason": "a" * 501}
    )
    assert_field_error(too_long, 422, "validation_error", "reason", REASON_MAX_MESSAGE)

    missing = await auth_client.post(annul_path(visit_id), headers=headers, json={})
    assert_field_error(missing, 422, "validation_error", "reason")

    assert await fetch_visit_row(db_conn, visit_id) == before


async def test_ca30_annul_missing_visit_is_404_and_invalid_id_is_422(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-30: anular una visita inexistente ⇒ 404; id no entero ⇒ 422."""
    _, headers = await admin_actor(db_conn)

    missing = await auth_client.post(
        annul_path(await nonexistent_visit_id(db_conn)),
        headers=headers,
        json={"reason": "Motivo"},
    )
    body = assert_error_envelope(missing, 404, "visit_not_found")
    assert body["detail"] == "La visita no existe"

    invalid = await auth_client.post(
        f"{VISITS_PATH}/abc/annul",
        headers=headers,
        json={"reason": "Motivo"},
    )
    assert_error_envelope(invalid, 422, "validation_error")


async def test_ca31_annulment_is_not_idempotent(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-31: repetir la anulacion ⇒ 409 `visit_already_annulled` sin cambios."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )

    first = await auth_client.post(
        annul_path(visit_id),
        headers=headers,
        json={"reason": "Visita registrada por error"},
    )
    assert first.status_code == 200, first.text
    after_first = await fetch_visit_row(db_conn, visit_id)
    assert after_first is not None

    again = await auth_client.post(
        annul_path(visit_id),
        headers=headers,
        json={"reason": "Otro motivo distinto"},
    )
    body = assert_error_envelope(again, 409, "visit_already_annulled")
    assert body["detail"] == "La visita ya está anulada"
    assert await fetch_visit_row(db_conn, visit_id) == after_first

    edit = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(
            doctor_id=doctor_id,
            patient_id=patient_id,
            visited_at=past_visited_at(),
        ),
    )
    edit_body = assert_error_envelope(edit, 409, "visit_annulled")
    assert edit_body["detail"] == "La visita anulada no se puede editar"
    assert await fetch_visit_row(db_conn, visit_id) == after_first
