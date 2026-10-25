"""Autorizacion de FEAT-013 (CA-1 a CA-3) y envelope uniforme (CA-32)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, create_auth_user, token_for
from tests.db_support import unique_suffix
from tests.visits_support import (
    VISITS_PATH,
    admin_actor,
    annul_path,
    assert_error_envelope,
    doctor_row,
    fetch_visit_row,
    medico_actor,
    patient_row,
    register_visit,
    visit_body,
    visit_path,
)

INVALID_TOKEN = {"Authorization": "Bearer not-a-valid-token"}


async def _visit_fixture(db_conn: AsyncConnection) -> tuple[int, int, int, int]:
    """Crea un medico, un paciente, un ADMIN y una visita registrada."""
    admin_id, _ = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    visit_id = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )
    return admin_id, doctor_id, patient_id, visit_id


def _endpoint_calls(
    *,
    doctor_id: int,
    patient_id: int,
    visit_id: int,
) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Las cinco operaciones con cuerpos validos, para aislar auth de validacion."""
    body = visit_body(doctor_id=doctor_id, patient_id=patient_id)
    return [
        ("GET", VISITS_PATH, None),
        ("POST", VISITS_PATH, body),
        ("GET", visit_path(visit_id), None),
        ("PUT", visit_path(visit_id), body),
        ("POST", annul_path(visit_id), {"reason": "Visita registrada por error"}),
    ]


async def test_ca1_admin_and_medico_read_and_register(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: `ADMIN` y `MEDICO` obtienen `200`/`201` en lectura y registro."""
    _, doctor_id, patient_id, visit_id = await _visit_fixture(db_conn)
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn, doctor_id=doctor_id)

    for name, headers, doctor_in_body in (
        ("ADMIN", admin_headers, doctor_id),
        ("MEDICO", medico_headers, None),
    ):
        listing = await auth_client.get(VISITS_PATH, headers=headers)
        assert listing.status_code == 200, (name, listing.text)

        detail = await auth_client.get(visit_path(visit_id), headers=headers)
        assert detail.status_code == 200, (name, detail.text)

        created = await auth_client.post(
            VISITS_PATH,
            headers=headers,
            json=visit_body(doctor_id=doctor_in_body, patient_id=patient_id),
        )
        assert created.status_code == 201, (name, created.text)
        assert created.json()["doctor"]["id"] == doctor_id


async def test_ca1_medico_cannot_edit_or_annul_and_row_does_not_change(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: `PUT` y `annul` con `MEDICO` ⇒ 403 `forbidden` sin efectos."""
    _, doctor_id, patient_id, visit_id = await _visit_fixture(db_conn)
    _, medico_headers = await medico_actor(db_conn, doctor_id=doctor_id)
    before = await fetch_visit_row(db_conn, visit_id)
    assert before is not None

    updated = await auth_client.put(
        visit_path(visit_id),
        headers=medico_headers,
        json=visit_body(doctor_id=None, patient_id=patient_id, symptoms="Otros síntomas"),
    )
    body = assert_error_envelope(updated, 403, "forbidden")
    assert body["detail"] == "No tiene permisos para realizar esta operación"

    annulled = await auth_client.post(
        annul_path(visit_id),
        headers=medico_headers,
        json={"reason": "Intento de anulación"},
    )
    assert_error_envelope(annulled, 403, "forbidden")

    assert await fetch_visit_row(db_conn, visit_id) == before


@pytest.mark.parametrize("headers", [None, INVALID_TOKEN], ids=["sin-token", "token-invalido"])
async def test_ca2_all_endpoints_reject_missing_or_invalid_token(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    headers: dict[str, str] | None,
) -> None:
    """CA-2: las cinco operaciones responden `401` sin cabecera o con token invalido."""
    _, doctor_id, patient_id, visit_id = await _visit_fixture(db_conn)
    expected_code = "not_authenticated" if headers is None else "invalid_token"

    before = await fetch_visit_row(db_conn, visit_id)
    for method, path, body in _endpoint_calls(
        doctor_id=doctor_id,
        patient_id=patient_id,
        visit_id=visit_id,
    ):
        response = await auth_client.request(
            method,
            path,
            headers=headers,
            json=body,
        )
        assert_error_envelope(response, 401, expected_code)

    assert await fetch_visit_row(db_conn, visit_id) == before


async def test_ca3_inactive_user_is_rejected_on_every_endpoint(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: un usuario desactivado recibe `403` `inactive_user` en las cinco rutas."""
    _, doctor_id, patient_id, visit_id = await _visit_fixture(db_conn)
    suffix = unique_suffix()
    inactive_id = await create_auth_user(
        db_conn,
        username=f"inactivo-{suffix}",
        email=f"inactivo.{suffix}@example.test",
        role="ADMIN",
        is_active=False,
    )
    inactive_headers = auth_headers(token_for(inactive_id))

    before = await fetch_visit_row(db_conn, visit_id)
    for method, path, body in _endpoint_calls(
        doctor_id=doctor_id,
        patient_id=patient_id,
        visit_id=visit_id,
    ):
        response = await auth_client.request(
            method,
            path,
            headers=inactive_headers,
            json=body,
        )
        body_json = assert_error_envelope(response, 403, "inactive_user")
        assert body_json["detail"] == "El usuario está inactivo"

    assert await fetch_visit_row(db_conn, visit_id) == before


async def test_ca32_error_envelope_has_no_details_key(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-32: todos los errores usan `{detail, code, errors}` sin `details`."""
    _, _, patient_id, visit_id = await _visit_fixture(db_conn)

    _, headers = await admin_actor(db_conn)

    responses = [
        await auth_client.get(visit_path(visit_id + 500), headers=headers),
        await auth_client.get(f"{VISITS_PATH}/abc", headers=headers),
        await auth_client.get(VISITS_PATH, headers=headers, params={"sort": "doctor"}),
        await auth_client.post(annul_path(visit_id), headers=headers, json={"reason": "   "}),
        await auth_client.put(
            visit_path(visit_id),
            headers=headers,
            json=visit_body(doctor_id=None, patient_id=patient_id),
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
