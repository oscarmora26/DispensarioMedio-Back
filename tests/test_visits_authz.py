"""Autorizacion de visitas tras FEAT-019: ADMIN solo lectura, MEDICO escribe lo suyo.

Matriz cubierta:
- `ADMIN`: `403 forbidden` en las tres escrituras; `200` en listado y detalle.
- `MEDICO` dueño: `201`/`200` en sus propias visitas.
- `MEDICO` sobre visita ajena: `404 visit_not_found` en detalle, `PUT` y anulacion,
  incluso si la visita ajena esta anulada (la propiedad precede al estado).
- Sin token o con token invalido: `401`; usuario inactivo: `403 inactive_user`.
- `doctor_id` del body distinto del token: `422`; `MEDICO` sin doctor: `403`.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.schemas.visit import DOCTOR_FROM_TOKEN_MESSAGE
from app.services.visit_service import DOCTOR_NOT_ASSOCIATED_MESSAGE
from tests.visits_support import (
    VISITS_PATH,
    admin_actor,
    annul_path,
    annul_visit_row,
    assert_error_envelope,
    assert_field_error,
    count_visits,
    doctor_row,
    fetch_visit_row,
    patient_row,
    register_visit,
    unlinked_medico_actor,
    visit_body,
    visit_owner_actor,
    visit_path,
)

INVALID_TOKEN = {"Authorization": "Bearer not-a-valid-token"}
ANNUL_BODY = {"reason": "Visita registrada por error"}


async def _visit_fixture(db_conn: AsyncConnection) -> tuple[int, int, int, int]:
    """Crea un medico dueño, un paciente y una visita suya registrada."""
    owner_id, doctor_id, _ = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=owner_id,
    )
    return owner_id, doctor_id, patient_id, visit_id


def _endpoint_calls(
    *,
    patient_id: int,
    visit_id: int,
) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Las cinco operaciones con cuerpos validos de MEDICO, para aislar auth."""
    body = visit_body(doctor_id=None, patient_id=patient_id)
    return [
        ("GET", VISITS_PATH, None),
        ("POST", VISITS_PATH, body),
        ("GET", visit_path(visit_id), None),
        ("PUT", visit_path(visit_id), body),
        ("POST", annul_path(visit_id), ANNUL_BODY),
    ]


async def test_ca1_admin_is_read_only_for_visits(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1/CA-2: `ADMIN` lee listado y detalle, pero `403 forbidden` si escribe."""
    _, doctor_id, patient_id, visit_id = await _visit_fixture(db_conn)
    _, admin_headers = await admin_actor(db_conn)
    before = await fetch_visit_row(db_conn, visit_id)
    visits_before = await count_visits(db_conn)

    listing = await auth_client.get(VISITS_PATH, headers=admin_headers)
    assert listing.status_code == 200, listing.text
    assert visit_id in {item["id"] for item in listing.json()["items"]}

    detail = await auth_client.get(visit_path(visit_id), headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["doctor"]["id"] == doctor_id

    created = await auth_client.post(
        VISITS_PATH,
        headers=admin_headers,
        json=visit_body(doctor_id=doctor_id, patient_id=patient_id),
    )
    body = assert_error_envelope(created, 403, "forbidden")
    assert body["detail"] == "No tiene permisos para realizar esta operación"

    updated = await auth_client.put(
        visit_path(visit_id),
        headers=admin_headers,
        json=visit_body(doctor_id=doctor_id, patient_id=patient_id, symptoms="Otros síntomas"),
    )
    assert_error_envelope(updated, 403, "forbidden")

    annulled = await auth_client.post(
        annul_path(visit_id),
        headers=admin_headers,
        json=ANNUL_BODY,
    )
    assert_error_envelope(annulled, 403, "forbidden")

    assert await count_visits(db_conn) == visits_before
    assert await fetch_visit_row(db_conn, visit_id) == before


async def test_ca3_medico_performs_every_write_on_its_own_visits(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: el `MEDICO` dueño crea, edita, anula y lee sus propias visitas."""
    owner_id, doctor_id, headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)

    created = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id),
    )
    assert created.status_code == 201, created.text
    visit_id = created.json()["id"]
    assert created.json()["doctor"]["id"] == doctor_id
    assert created.json()["created_by"] == owner_id

    updated = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id, symptoms="Síntomas corregidos"),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["symptoms"] == "Síntomas corregidos"

    annulled = await auth_client.post(
        annul_path(visit_id),
        headers=headers,
        json=ANNUL_BODY,
    )
    assert annulled.status_code == 200, annulled.text
    assert annulled.json()["annulled_by"] == owner_id

    detail = await auth_client.get(visit_path(visit_id), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "ANULADA"

    listing = await auth_client.get(VISITS_PATH, headers=headers)
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()["items"]] == [visit_id]


async def test_ca4_medico_gets_404_on_another_doctors_visit(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4: detalle, `PUT` y anulacion de una visita ajena ⇒ `404 visit_not_found`."""
    _, _, headers = await visit_owner_actor(db_conn)
    other_id, other_doctor_id, other_headers = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    foreign_visit = await register_visit(
        db_conn,
        doctor_id=other_doctor_id,
        patient_id=patient_id,
        created_by=other_id,
    )
    before = await fetch_visit_row(db_conn, foreign_visit)

    detail = await auth_client.get(visit_path(foreign_visit), headers=headers)
    body = assert_error_envelope(detail, 404, "visit_not_found")
    assert body["detail"] == "La visita no existe"

    updated = await auth_client.put(
        visit_path(foreign_visit),
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id, symptoms="Intento ajeno"),
    )
    assert_error_envelope(updated, 404, "visit_not_found")

    annulled = await auth_client.post(
        annul_path(foreign_visit),
        headers=headers,
        json=ANNUL_BODY,
    )
    assert_error_envelope(annulled, 404, "visit_not_found")

    assert await fetch_visit_row(db_conn, foreign_visit) == before

    mine = await auth_client.get(visit_path(foreign_visit), headers=other_headers)
    assert mine.status_code == 200, mine.text


async def test_ca7_annulled_foreign_visit_is_404_not_409(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: la propiedad precede al estado: una visita anulada ajena responde 404."""
    _, _, headers = await visit_owner_actor(db_conn)
    other_id, other_doctor_id, _ = await visit_owner_actor(db_conn)
    patient_id = await patient_row(db_conn)
    foreign_visit = await register_visit(
        db_conn,
        doctor_id=other_doctor_id,
        patient_id=patient_id,
        created_by=other_id,
    )
    await annul_visit_row(db_conn, foreign_visit, annulled_by=other_id)
    before = await fetch_visit_row(db_conn, foreign_visit)

    detail = await auth_client.get(visit_path(foreign_visit), headers=headers)
    assert_error_envelope(detail, 404, "visit_not_found")

    updated = await auth_client.put(
        visit_path(foreign_visit),
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id),
    )
    assert_error_envelope(updated, 404, "visit_not_found")

    annulled = await auth_client.post(
        annul_path(foreign_visit),
        headers=headers,
        json=ANNUL_BODY,
    )
    assert_error_envelope(annulled, 404, "visit_not_found")

    assert await fetch_visit_row(db_conn, foreign_visit) == before


async def test_ca5_body_doctor_id_must_match_the_token(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: un `doctor_id` distinto del token ⇒ 422 `validation_error` en `doctor_id`."""
    owner_id, doctor_id, headers = await visit_owner_actor(db_conn)
    other_doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)

    rejected = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=other_doctor_id, patient_id=patient_id),
    )
    assert_field_error(rejected, 422, "validation_error", "doctor_id", DOCTOR_FROM_TOKEN_MESSAGE)
    assert await count_visits(db_conn) == 0

    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=owner_id,
    )
    before = await fetch_visit_row(db_conn, visit_id)
    rejected_update = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(doctor_id=other_doctor_id, patient_id=patient_id),
    )
    assert_field_error(
        rejected_update, 422, "validation_error", "doctor_id", DOCTOR_FROM_TOKEN_MESSAGE
    )
    assert await fetch_visit_row(db_conn, visit_id) == before

    accepted = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(doctor_id=doctor_id, patient_id=patient_id),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["doctor"]["id"] == doctor_id


async def test_ca6_medico_without_doctor_cannot_operate(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: `MEDICO` sin `doctor_id` ⇒ `403 doctor_not_associated` al registrar.

    En `PUT` y anulacion la comprobacion de propiedad corre antes (FEAT-019 R-5,
    R-6): como `visits.doctor_id` nunca es `NULL`, un medico sin asociacion no es
    dueño de ninguna visita y recibe `404 visit_not_found`.
    """
    other_id, other_doctor_id, _ = await visit_owner_actor(db_conn)
    _, headers = await unlinked_medico_actor(db_conn)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=other_doctor_id,
        patient_id=patient_id,
        created_by=other_id,
    )

    rejected = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id),
    )
    body = assert_error_envelope(rejected, 403, "doctor_not_associated")
    assert body["detail"] == DOCTOR_NOT_ASSOCIATED_MESSAGE
    assert await count_visits(db_conn) == 1

    updated = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(doctor_id=None, patient_id=patient_id),
    )
    assert_error_envelope(updated, 404, "visit_not_found")

    annulled = await auth_client.post(
        annul_path(visit_id),
        headers=headers,
        json=ANNUL_BODY,
    )
    assert_error_envelope(annulled, 404, "visit_not_found")

    assert await count_visits(db_conn) == 1


@pytest.mark.parametrize("headers", [None, INVALID_TOKEN], ids=["sin-token", "token-invalido"])
async def test_ca2_all_endpoints_reject_missing_or_invalid_token(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    headers: dict[str, str] | None,
) -> None:
    """CA-2: las cinco operaciones responden `401` sin cabecera o con token invalido."""
    _, _, patient_id, visit_id = await _visit_fixture(db_conn)
    expected_code = "not_authenticated" if headers is None else "invalid_token"

    before = await fetch_visit_row(db_conn, visit_id)
    for method, path, payload in _endpoint_calls(patient_id=patient_id, visit_id=visit_id):
        response = await auth_client.request(
            method,
            path,
            headers=headers,
            json=payload,
        )
        assert_error_envelope(response, 401, expected_code)

    assert await fetch_visit_row(db_conn, visit_id) == before


async def test_ca3_inactive_user_is_rejected_on_every_endpoint(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: un medico desactivado recibe `403` `inactive_user` en las cinco rutas."""
    owner_id, doctor_id, inactive_headers = await visit_owner_actor(db_conn, is_active=False)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=owner_id,
    )

    before = await fetch_visit_row(db_conn, visit_id)
    for method, path, payload in _endpoint_calls(patient_id=patient_id, visit_id=visit_id):
        response = await auth_client.request(
            method,
            path,
            headers=inactive_headers,
            json=payload,
        )
        body = assert_error_envelope(response, 403, "inactive_user")
        assert body["detail"] == "El usuario está inactivo"

    assert await fetch_visit_row(db_conn, visit_id) == before


async def test_ca32_error_envelope_has_no_details_key(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-32: todos los errores usan `{detail, code, errors}` sin `details`."""
    _, _, patient_id, visit_id = await _visit_fixture(db_conn)
    _, _, headers = await visit_owner_actor(db_conn)

    responses = [
        await auth_client.get(visit_path(visit_id + 500), headers=headers),
        await auth_client.get(f"{VISITS_PATH}/abc", headers=headers),
        await auth_client.get(VISITS_PATH, headers=headers, params={"sort": "doctor"}),
        await auth_client.post(annul_path(visit_id), headers=headers, json={"reason": "   "}),
        await auth_client.put(
            visit_path(visit_id),
            headers=headers,
            json=visit_body(doctor_id=None, patient_id=patient_id, symptoms="ab"),
        ),
    ]
    codes = [
        "visit_not_found",
        "validation_error",
        "validation_error",
        "validation_error",
        "validation_error",
    ]
    for response, code in zip(responses, codes, strict=True):
        body = assert_error_envelope(response, response.status_code, code)
        assert set(body) == {"detail", "code", "errors"}
