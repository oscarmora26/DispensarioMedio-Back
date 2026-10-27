"""Registro de visitas de FEAT-013 con el contrato de FEAT-019 (CA-4 a CA-15 y CA-33).

El `MEDICO` es el unico rol que escribe visitas: el medico se obtiene siempre del
token y la visita queda a su nombre.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.schemas.visit import (
    DOCTOR_FROM_TOKEN_MESSAGE,
    DOCTOR_INACTIVE_MESSAGE,
    DUPLICATE_MEDICATION_MESSAGE,
    INSTRUCTIONS_MAX_MESSAGE,
    MEDICATION_INACTIVE_MESSAGE,
    PATIENT_INACTIVE_MESSAGE,
    QUANTITY_POSITIVE_MESSAGE,
    RECOMMENDATIONS_MAX_MESSAGE,
    SYMPTOMS_MAX_MESSAGE,
    SYMPTOMS_RANGE_MESSAGE,
    VISITED_AT_FUTURE_MESSAGE,
)
from app.services.visit_service import DOCTOR_NOT_ASSOCIATED_MESSAGE
from tests.patients_support import nonexistent_patient_id
from tests.visits_support import (
    ANNULLED_KEYS,
    DETAIL_KEYS,
    DOCTOR_REF_KEYS,
    MEDICATION_KEYS,
    PATIENT_REF_KEYS,
    VISITS_PATH,
    assert_error_envelope,
    assert_field_error,
    count_visits,
    create_medication_row,
    doctor_row,
    fetch_visit_medication_rows,
    fetch_visit_row,
    past_visited_at,
    patient_row,
    register_visit,
    unlinked_medico_actor,
    visit_body,
    visit_owner_actor,
    visit_path,
)


async def test_ca4_medico_registers_with_its_own_doctor(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4: con `MEDICO` el `doctor_id` es el del token y otro valor ⇒ 422."""
    medico_id, doctor_id, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)

    created = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["doctor"]["id"] == doctor_id
    assert body["created_by"] == medico_id

    row = await fetch_visit_row(db_conn, body["id"])
    assert row is not None
    assert row["doctor_id"] == doctor_id

    other_doctor_id = await doctor_row(db_conn)
    before = await count_visits(db_conn)
    rejected = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=other_doctor_id, patient_id=patient_id),
    )
    assert_field_error(rejected, 422, "validation_error", "doctor_id", DOCTOR_FROM_TOKEN_MESSAGE)
    assert await count_visits(db_conn) == before


async def test_ca5_medico_without_associated_doctor_is_forbidden(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: `MEDICO` sin medico asociado ⇒ `403` `doctor_not_associated`."""
    patient_id = await patient_row(db_conn)
    _, headers = await unlinked_medico_actor(db_conn)

    response = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id),
    )
    body = assert_error_envelope(response, 403, "doctor_not_associated")
    assert body["detail"] == DOCTOR_NOT_ASSOCIATED_MESSAGE
    assert await count_visits(db_conn) == 0


async def test_ca6_medico_with_inactive_doctor_cannot_write(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: D-5 el medico inactivo del token no registra ni edita visitas (422)."""
    owner_id, doctor_id, headers = await visit_owner_actor(db_conn, doctor_is_active=False)
    patient_id = await patient_row(db_conn)

    rejected = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id),
    )
    assert_field_error(rejected, 422, "validation_error", "doctor_id", DOCTOR_INACTIVE_MESSAGE)
    assert await count_visits(db_conn) == 0

    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=owner_id,
    )
    before = await fetch_visit_row(db_conn, visit_id)
    updated = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id, symptoms="Otros síntomas"),
    )
    assert_field_error(updated, 422, "validation_error", "doctor_id", DOCTOR_INACTIVE_MESSAGE)
    assert await fetch_visit_row(db_conn, visit_id) == before


async def test_ca7_visited_at_cannot_be_in_the_future(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: `visited_at` futura ⇒ 422; pasada (incluido hoy) ⇒ aceptada."""
    _, _, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)

    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    rejected = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id, visited_at=future),
    )
    assert_field_error(rejected, 422, "validation_error", "visited_at", VISITED_AT_FUTURE_MESSAGE)

    for minutes in (1, 60 * 24 * 30):
        accepted = await auth_client.post(
            VISITS_PATH,
            headers=headers,
            json=visit_body(
                doctor_id=None,
                patient_id=patient_id,
                visited_at=past_visited_at(minutes),
            ),
        )
        assert accepted.status_code == 201, accepted.text


async def test_ca7_naive_visited_at_is_assumed_in_santo_domingo(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """D-3: una `visited_at` sin zona se interpreta en `America/Santo_Domingo`."""
    _, _, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    naive_value = (datetime.now(UTC) - timedelta(hours=5)).replace(tzinfo=None, microsecond=0)
    expected = naive_value.replace(tzinfo=UTC) + timedelta(hours=4)

    created = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            visited_at=naive_value.isoformat(),
        ),
    )
    assert created.status_code == 201, created.text

    row = await fetch_visit_row(db_conn, created.json()["id"])
    assert row is not None
    assert row["visited_at"] == expected


async def test_ca8_symptoms_length_is_enforced(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-8: `symptoms` vacio o <3 ⇒ 422 de rango; >2000 ⇒ 422 de maximo."""
    _, _, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)

    for invalid in ("", "   ", "ab"):
        rejected = await auth_client.post(
            VISITS_PATH,
            headers=headers,
            json=visit_body(doctor_id=None, patient_id=patient_id, symptoms=invalid),
        )
        assert_field_error(rejected, 422, "validation_error", "symptoms", SYMPTOMS_RANGE_MESSAGE)

    too_long = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id, symptoms="a" * 2001),
    )
    assert_field_error(too_long, 422, "validation_error", "symptoms", SYMPTOMS_MAX_MESSAGE)

    missing = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json={
            "patient_id": patient_id,
            "visited_at": past_visited_at(),
        },
    )
    assert_field_error(missing, 422, "validation_error", "symptoms", SYMPTOMS_RANGE_MESSAGE)

    for accepted in ("abc", "a" * 2000):
        created = await auth_client.post(
            VISITS_PATH,
            headers=headers,
            json=visit_body(doctor_id=None, patient_id=patient_id, symptoms=accepted),
        )
        assert created.status_code == 201, created.text
        assert created.json()["symptoms"] == accepted.strip()


async def test_ca9_ca14_recommendations_optional_and_bounded(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-9/CA-14: ausente o de solo espacios ⇒ `null`; >2000 ⇒ 422; ≤2000 ok."""
    _, _, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)

    body_without = visit_body(doctor_id=None, patient_id=patient_id)
    body_without.pop("recommendations")
    absent = await auth_client.post(VISITS_PATH, headers=headers, json=body_without)
    assert absent.status_code == 201, absent.text
    assert absent.json()["recommendations"] is None

    blank = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id, recommendations="    "),
    )
    assert blank.status_code == 201, blank.text
    assert blank.json()["recommendations"] is None
    row = await fetch_visit_row(db_conn, blank.json()["id"])
    assert row is not None and row["recommendations"] is None

    too_long = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            recommendations="a" * 2001,
        ),
    )
    assert_field_error(
        too_long, 422, "validation_error", "recommendations", RECOMMENDATIONS_MAX_MESSAGE
    )

    accepted = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            recommendations="a" * 2000,
        ),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["recommendations"] == "a" * 2000


async def test_ca10_patient_must_exist_and_be_active(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: paciente inexistente ⇒ 404; inactivo ⇒ 422 en `patient_id`."""
    _, _, headers = await visit_owner_actor(db_conn)

    nonexistent = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=await nonexistent_patient_id(db_conn)),
    )
    assert_error_envelope(nonexistent, 404, "patient_not_found")

    inactive_id = await patient_row(db_conn, is_active=False)
    inactive = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=inactive_id),
    )
    assert_field_error(inactive, 422, "validation_error", "patient_id", PATIENT_INACTIVE_MESSAGE)
    assert await count_visits(db_conn) == 0


async def test_ca11_medications_must_exist_be_active_and_be_valid(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11: medicamento inexistente/inactivo, duplicado, cantidad e instrucciones."""
    _, _, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    medication_id = await create_medication_row(db_conn)

    def body(*medications: dict[str, object]) -> dict[str, object]:
        return visit_body(
            doctor_id=None,
            patient_id=patient_id,
            medications=list(medications),
        )

    nonexistent = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=body({"medication_id": medication_id + 10_000}),
    )
    assert_error_envelope(nonexistent, 404, "medication_not_found")

    inactive_id = await create_medication_row(db_conn, is_active=False)
    inactive = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=body({"medication_id": inactive_id}),
    )
    assert_field_error(
        inactive, 422, "validation_error", "medications", MEDICATION_INACTIVE_MESSAGE
    )

    duplicate = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=body(
            {"medication_id": medication_id},
            {"medication_id": medication_id, "quantity": 1},
        ),
    )
    assert_field_error(
        duplicate, 422, "validation_error", "medications", DUPLICATE_MEDICATION_MESSAGE
    )

    for quantity in (0, -1, "0"):
        invalid_quantity = await auth_client.post(
            VISITS_PATH,
            headers=headers,
            json=body({"medication_id": medication_id, "quantity": quantity}),
        )
        assert_field_error(
            invalid_quantity,
            422,
            "validation_error",
            "quantity",
            QUANTITY_POSITIVE_MESSAGE,
        )

    long_instructions = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=body({"medication_id": medication_id, "instructions": "a" * 256}),
    )
    assert_field_error(
        long_instructions, 422, "validation_error", "instructions", INSTRUCTIONS_MAX_MESSAGE
    )
    assert await count_visits(db_conn) == 0


async def test_ca12_create_without_medications_returns_the_detail_shape(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: `POST` valido sin medicamentos ⇒ 201 con la forma D-7."""
    actor_id, doctor_id, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)

    created = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id),
    )
    assert created.status_code == 201, created.text
    body = created.json()

    assert set(body) == DETAIL_KEYS
    assert body["status"] == "REGISTRADA"
    assert body["created_by"] == actor_id
    assert set(body["doctor"]) == DOCTOR_REF_KEYS
    assert body["doctor"]["id"] == doctor_id
    assert set(body["patient"]) == PATIENT_REF_KEYS
    assert body["patient"]["id"] == patient_id
    assert body["medications"] == []
    assert datetime.fromisoformat(body["created_at"])
    assert datetime.fromisoformat(body["visited_at"])

    row = await fetch_visit_row(db_conn, body["id"])
    assert row is not None
    assert row["status"] == "REGISTRADA"
    assert row["created_by"] == actor_id
    assert row["annulled_by"] is None


async def test_ca13_create_with_medications_persists_the_details(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: `POST` con medicamentos devuelve y guarda los detalles."""
    _, _, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    first = await create_medication_row(db_conn, description="Ibuprofeno", dose="400 mg")
    second = await create_medication_row(db_conn, description="Paracetamol", dose="500 mg")

    created = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            medications=[
                {"medication_id": first, "quantity": 2, "instructions": "Cada 8 horas"},
                {"medication_id": second},
            ],
        ),
    )
    assert created.status_code == 201, created.text
    body = created.json()

    medications = {item["medication_id"]: item for item in body["medications"]}
    assert set(medications) == {first, second}
    for item in body["medications"]:
        assert set(item) == MEDICATION_KEYS
    assert medications[first]["description"] == "Ibuprofeno"
    assert medications[first]["dose"] == "400 mg"
    assert Decimal(str(medications[first]["quantity"])) == Decimal("2")
    assert medications[first]["instructions"] == "Cada 8 horas"
    assert medications[second]["quantity"] is None
    assert medications[second]["instructions"] is None

    rows = await fetch_visit_medication_rows(db_conn, body["id"])
    assert [row["medication_id"] for row in rows] == sorted([first, second])
    stored = {row["medication_id"]: row for row in rows}
    assert stored[first]["quantity"] == Decimal("2.00")
    assert stored[first]["instructions"] == "Cada 8 horas"
    assert stored[second]["quantity"] is None


async def test_ca15_response_never_includes_annulment_fields(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-15: una visita recien registrada no expone los campos de anulacion."""
    _, _, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)

    created = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id),
    )
    assert created.status_code == 201, created.text
    assert set(created.json()) & ANNULLED_KEYS == set()

    detail = await auth_client.get(visit_path(created.json()["id"]), headers=headers)
    assert detail.status_code == 200, detail.text
    assert set(detail.json()) & ANNULLED_KEYS == set()


async def test_ca33_unique_violation_at_commit_is_translated_and_session_stays_usable(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-33: la violacion de `quantity_positive` en el `commit` ⇒ 422 y sesion usable.

    `0.004` pasa la validacion de la API pero PostgreSQL lo ajusta a `0.00` en
    `NUMERIC(10,2)`, de modo que la constraint se viola en la base (D-9).
    """
    _, _, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    medication_id = await create_medication_row(db_conn)

    rejected = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            medications=[{"medication_id": medication_id, "quantity": 0.004}],
        ),
    )
    assert_field_error(rejected, 422, "validation_error", "quantity", QUANTITY_POSITIVE_MESSAGE)
    assert await count_visits(db_conn) == 0

    listing = await auth_client.get(VISITS_PATH, headers=headers)
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0

    accepted = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            medications=[{"medication_id": medication_id, "quantity": 1}],
        ),
    )
    assert accepted.status_code == 201, accepted.text
