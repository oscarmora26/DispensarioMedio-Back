"""Edicion de visitas de FEAT-013 bajo el contrato de FEAT-019 (CA-25 a CA-28).

El `MEDICO` dueño es el unico que puede editar: el medico de la visita sale del
token y no se puede reasignar por body.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.schemas.visit import (
    DUPLICATE_MEDICATION_MESSAGE,
    PATIENT_INACTIVE_MESSAGE,
    PATIENT_REQUIRED_MESSAGE,
    SYMPTOMS_RANGE_MESSAGE,
    VISITED_AT_FUTURE_MESSAGE,
    VISITED_AT_REQUIRED_MESSAGE,
)
from tests.visits_support import (
    DETAIL_KEYS,
    VISITS_PATH,
    annul_path,
    assert_error_envelope,
    assert_field_error,
    create_medication_row,
    fetch_visit_medication_rows,
    fetch_visit_row,
    past_visited_at,
    patient_row,
    register_visit,
    visit_body,
    visit_owner_actor,
    visit_path,
)


def _iso(value: datetime) -> str:
    """Serializa un datetime para el body JSON."""
    return value.isoformat()


async def test_ca25_full_put_preserves_created_at_and_refreshes_updated_at(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-25: `PUT` completo reemplaza los campos y conserva `created_at`/`created_by`.

    El medico no se puede cambiar por body (FEAT-019 R-3): la visita sigue siendo
    del medico del token.
    """
    actor_id, doctor_id, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    new_patient_id = await patient_row(db_conn)

    created = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            symptoms="Síntomas iniciales",
        ),
    )
    assert created.status_code == 201, created.text
    original = created.json()

    updated_at_instant = datetime.now(UTC) - timedelta(hours=1)
    updated = await auth_client.put(
        visit_path(original["id"]),
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=new_patient_id,
            visited_at=_iso(updated_at_instant),
            symptoms="Síntomas corregidos",
            recommendations="Nuevas recomendaciones",
        ),
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()

    assert set(body) == DETAIL_KEYS
    assert body["id"] == original["id"]
    assert body["created_by"] == actor_id
    assert datetime.fromisoformat(body["created_at"]) == datetime.fromisoformat(
        original["created_at"]
    )
    assert datetime.fromisoformat(body["updated_at"]) > datetime.fromisoformat(body["created_at"])
    assert body["doctor"]["id"] == doctor_id
    assert body["patient"]["id"] == new_patient_id
    assert datetime.fromisoformat(body["visited_at"]) == updated_at_instant
    assert body["symptoms"] == "Síntomas corregidos"
    assert body["recommendations"] == "Nuevas recomendaciones"
    assert body["status"] == "REGISTRADA"

    row = await fetch_visit_row(db_conn, original["id"])
    assert row is not None
    assert row["doctor_id"] == doctor_id
    assert row["patient_id"] == new_patient_id
    assert row["symptoms"] == "Síntomas corregidos"
    assert row["created_by"] == actor_id
    assert row["created_at"] == datetime.fromisoformat(original["created_at"])


async def test_ca26_put_replaces_the_medication_list(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-26: los detalles retirados se borran y la lista enviada sustituye a la anterior."""
    _, _, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    first = await create_medication_row(db_conn, description="Ibuprofeno")
    second = await create_medication_row(db_conn, description="Paracetamol")

    created = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            medications=[{"medication_id": first}, {"medication_id": second}],
        ),
    )
    assert created.status_code == 201, created.text
    visit_id = created.json()["id"]
    assert len(await fetch_visit_medication_rows(db_conn, visit_id)) == 2

    updated = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            medications=[{"medication_id": second, "quantity": 3, "instructions": "Cada 12 horas"}],
        ),
    )
    assert updated.status_code == 200, updated.text
    assert [item["medication_id"] for item in updated.json()["medications"]] == [second]
    assert updated.json()["medications"][0]["instructions"] == "Cada 12 horas"

    rows = await fetch_visit_medication_rows(db_conn, visit_id)
    assert [row["medication_id"] for row in rows] == [second]

    cleared = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json={
            "patient_id": patient_id,
            "visited_at": past_visited_at(),
            "symptoms": "Sin medicamentos",
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["medications"] == []
    assert await fetch_visit_medication_rows(db_conn, visit_id) == []


async def test_ca27_put_of_annulled_visit_is_conflict_without_changes(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-27: `PUT` sobre una visita anulada ⇒ 409 `visit_annulled` sin cambios."""
    actor_id, doctor_id, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=actor_id,
    )
    annulled = await auth_client.post(
        annul_path(visit_id),
        headers=headers,
        json={"reason": "Visita registrada por error"},
    )
    assert annulled.status_code == 200, annulled.text

    before = await fetch_visit_row(db_conn, visit_id)
    response = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            symptoms="Intento de edición",
        ),
    )
    body = assert_error_envelope(response, 409, "visit_annulled")
    assert body["detail"] == "La visita anulada no se puede editar"
    assert await fetch_visit_row(db_conn, visit_id) == before


async def test_ca28_partial_body_is_rejected_field_by_field(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-28: un body parcial ⇒ 422 con el campo ausente; `doctor_id` ya no es obligatorio."""
    actor_id, doctor_id, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=actor_id,
    )
    before = await fetch_visit_row(db_conn, visit_id)

    full = {
        "patient_id": patient_id,
        "visited_at": past_visited_at(),
        "symptoms": "Dolor de cabeza",
    }
    expectations = {
        "patient_id": PATIENT_REQUIRED_MESSAGE,
        "visited_at": VISITED_AT_REQUIRED_MESSAGE,
        "symptoms": SYMPTOMS_RANGE_MESSAGE,
    }
    for field, message in expectations.items():
        payload = {key: value for key, value in full.items() if key != field}
        response = await auth_client.put(visit_path(visit_id), headers=headers, json=payload)
        assert_field_error(response, 422, "validation_error", field, message)

    assert await fetch_visit_row(db_conn, visit_id) == before

    accepted = await auth_client.put(visit_path(visit_id), headers=headers, json=full)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["doctor"]["id"] == doctor_id


async def test_ca28_put_applies_the_same_validations_as_post(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-28: las validaciones 6-14 se aplican igual en `PUT`."""
    actor_id, doctor_id, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    inactive_patient = await patient_row(db_conn, is_active=False)
    medication_id = await create_medication_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=actor_id,
    )
    before = await fetch_visit_row(db_conn, visit_id)

    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    rejected_future = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            visited_at=future,
        ),
    )
    assert_field_error(
        rejected_future, 422, "validation_error", "visited_at", VISITED_AT_FUTURE_MESSAGE
    )

    rejected_patient = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=inactive_patient),
    )
    assert_field_error(
        rejected_patient, 422, "validation_error", "patient_id", PATIENT_INACTIVE_MESSAGE
    )

    rejected_medications = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            medications=[{"medication_id": medication_id}, {"medication_id": medication_id}],
        ),
    )
    assert_field_error(
        rejected_medications, 422, "validation_error", "medications", DUPLICATE_MEDICATION_MESSAGE
    )

    assert await fetch_visit_row(db_conn, visit_id) == before
