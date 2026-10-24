"""Autorizacion de FEAT-011 (CA-1 a CA-4)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers
from tests.db_support import unique_cedula, unique_suffix
from tests.patients_support import (
    PATIENTS_PATH,
    admin_actor,
    assert_error_envelope,
    create_patient_row,
    fetch_patient_row,
    medico_actor,
    patient_body,
    patient_path,
)

INVALID_TOKEN = "Bearer not-a-valid-token"


async def test_ca1_both_roles_can_read_list_and_detail(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: `ADMIN` y `MEDICO` obtienen `200` en listado y detalle."""
    patient_id = await create_patient_row(db_conn, name=f"Lectura {unique_suffix()}")

    for actor in (admin_actor, medico_actor):
        _, headers = await actor(db_conn)
        listing = await auth_client.get(PATIENTS_PATH, headers=headers)
        assert listing.status_code == 200, (actor.__name__, listing.text)

        detail = await auth_client.get(patient_path(patient_id), headers=headers)
        assert detail.status_code == 200, (actor.__name__, detail.text)


async def test_ca2_medico_cannot_write_and_row_does_not_change(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: `POST`/`PUT`/`DELETE` con `MEDICO` ⇒ 403 sin efectos en la base."""
    _, admin_headers = await admin_actor(db_conn)
    tag = unique_suffix()
    patient_id = await create_patient_row(db_conn, name=f"Protegido {tag}")
    before = await fetch_patient_row(db_conn, patient_id)
    assert before is not None

    _, medico_headers = await medico_actor(db_conn)
    cedula = unique_cedula()

    created = await auth_client.post(
        PATIENTS_PATH, headers=medico_headers, json=patient_body(tag, cedula=cedula)
    )
    body = assert_error_envelope(created, 403, "forbidden")
    assert body["detail"] == "No tiene permisos para realizar esta operación"

    updated = await auth_client.put(
        patient_path(patient_id),
        headers=medico_headers,
        json={
            "name": "Intento",
            "cedula": before["cedula"],
            "patient_type": "EMPLEADO",
            "is_active": False,
        },
    )
    assert_error_envelope(updated, 403, "forbidden")

    deleted = await auth_client.delete(patient_path(patient_id), headers=medico_headers)
    assert_error_envelope(deleted, 403, "forbidden")

    after = await fetch_patient_row(db_conn, patient_id)
    assert after == before

    count = await db_conn.scalar(
        text("SELECT count(*) FROM patients WHERE cedula = :cedula"), {"cedula": cedula}
    )
    assert count == 0

    still_readable = await auth_client.get(patient_path(patient_id), headers=admin_headers)
    assert still_readable.status_code == 200


async def test_ca3_missing_or_invalid_token_is_401(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: sin cabecera y con token invalido ⇒ 401."""
    patient_id = await create_patient_row(db_conn, name=f"Sin token {unique_suffix()}")

    requests = [
        ("get", PATIENTS_PATH, {}),
        ("post", PATIENTS_PATH, {"json": patient_body(unique_suffix())}),
        ("get", patient_path(patient_id), {}),
        (
            "put",
            patient_path(patient_id),
            {
                "json": {
                    "name": "X",
                    "cedula": unique_cedula(),
                    "patient_type": "EMPLEADO",
                    "is_active": True,
                }
            },
        ),
        ("delete", patient_path(patient_id), {}),
    ]
    for method, url, kwargs in requests:
        missing = await getattr(auth_client, method)(url, **kwargs)
        assert_error_envelope(missing, 401, "not_authenticated")

        invalid = await getattr(auth_client, method)(
            url, headers=auth_headers(INVALID_TOKEN), **kwargs
        )
        assert_error_envelope(invalid, 401, "invalid_token")


async def test_ca4_inactive_user_loses_access_even_with_valid_token(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4: un usuario desactivado recibe `403 inactive_user` en los cinco endpoints."""
    user_id, headers = await admin_actor(db_conn)
    patient_id = await create_patient_row(db_conn, name=f"Inactivo {unique_suffix()}")
    await db_conn.execute(
        text("UPDATE users SET is_active = false WHERE id = :user_id"), {"user_id": user_id}
    )

    requests = [
        ("get", PATIENTS_PATH, {}),
        ("post", PATIENTS_PATH, {"json": patient_body(unique_suffix())}),
        ("get", patient_path(patient_id), {}),
        (
            "put",
            patient_path(patient_id),
            {
                "json": {
                    "name": "X",
                    "cedula": unique_cedula(),
                    "patient_type": "EMPLEADO",
                    "is_active": True,
                }
            },
        ),
        ("delete", patient_path(patient_id), {}),
    ]
    for method, url, kwargs in requests:
        response = await getattr(auth_client, method)(url, headers=headers, **kwargs)
        body = assert_error_envelope(response, 403, "inactive_user")
        assert body["detail"] == "El usuario está inactivo"
