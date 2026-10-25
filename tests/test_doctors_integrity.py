"""Integridad y desactivacion de FEAT-012 (CA-29 a CA-36)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import create_auth_user
from tests.db_support import get_role_id, unique_cedula, unique_suffix
from tests.doctors_support import (
    DOCTORS_PATH,
    admin_actor,
    assert_error_envelope,
    create_doctor_row,
    doctor_body,
    doctor_path,
    fetch_doctor_row,
    medico_actor,
    nonexistent_doctor_id,
    visit_for_doctor,
)

USERS_PATH = "/api/v1/users"


async def test_ca29_ca30_delete_is_idempotent_and_keeps_updated_at(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-29/CA-30: `DELETE` ⇒ 204 sin cuerpo; repetido no cambia `updated_at`."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)

    deleted = await auth_client.delete(doctor_path(doctor_id), headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b""

    row = await fetch_doctor_row(db_conn, doctor_id)
    assert row is not None and row["is_active"] is False

    again = await auth_client.delete(doctor_path(doctor_id), headers=headers)
    assert again.status_code == 204, again.text
    assert (await fetch_doctor_row(db_conn, doctor_id))["updated_at"] == row["updated_at"]


async def test_ca31_delete_missing_is_404_and_invalid_is_422(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-31: `DELETE` de id inexistente ⇒ 404; no entero ⇒ 422."""
    _, headers = await admin_actor(db_conn)

    missing = await auth_client.delete(
        doctor_path(await nonexistent_doctor_id(db_conn)), headers=headers
    )
    assert_error_envelope(missing, 404, "doctor_not_found")

    invalid = await auth_client.delete(f"{DOCTORS_PATH}/abc", headers=headers)
    assert_error_envelope(invalid, 422, "validation_error")


async def test_ca32_delete_with_visits_is_409_and_doctor_stays_active(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-32: medico con al menos una visita (cualquier estado) ⇒ 409."""
    _, headers = await admin_actor(db_conn)
    registered = await create_doctor_row(db_conn)
    annulled = await create_doctor_row(db_conn)
    await visit_for_doctor(db_conn, registered, status="REGISTRADA")
    await visit_for_doctor(db_conn, annulled, status="ANULADA")

    for doctor_id in (registered, annulled):
        response = await auth_client.delete(doctor_path(doctor_id), headers=headers)
        body = assert_error_envelope(response, 409, "doctor_has_visits")
        assert body["detail"] == "El médico tiene visitas registradas"

        row = await fetch_doctor_row(db_conn, doctor_id)
        assert row is not None and row["is_active"] is True


async def test_ca32_retry_on_inactive_doctor_with_visits_still_409(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Regla 34: la comprobacion de visitas precede a la idempotencia del DELETE."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)
    await visit_for_doctor(db_conn, doctor_id)
    await db_conn.execute(
        text("UPDATE doctors SET is_active = false WHERE id = :doctor_id"),
        {"doctor_id": doctor_id},
    )

    response = await auth_client.delete(doctor_path(doctor_id), headers=headers)
    assert_error_envelope(response, 409, "doctor_has_visits")


async def test_ca33_delete_with_user_association_keeps_it(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-33: medico sin visitas pero con usuario asociado se desactiva;
    la asociacion se conserva."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)
    await medico_actor(db_conn, doctor_id=doctor_id)

    deleted = await auth_client.delete(doctor_path(doctor_id), headers=headers)
    assert deleted.status_code == 204, deleted.text

    linked = await db_conn.scalar(
        text("SELECT count(*) FROM users WHERE doctor_id = :doctor_id"),
        {"doctor_id": doctor_id},
    )
    assert linked == 1


async def test_ca34_error_envelope_is_uniform(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-34: el envelope siempre tiene `detail`, `code` y `errors`, nunca `details`."""
    _, headers = await admin_actor(db_conn)

    missing = await auth_client.get(
        doctor_path(await nonexistent_doctor_id(db_conn)), headers=headers
    )
    body = assert_error_envelope(missing, 404, "doctor_not_found")
    assert set(body) == {"detail", "code", "errors"}
    assert "details" not in body
    assert body["errors"] == []

    duplicate = await auth_client.post(
        DOCTORS_PATH,
        headers=headers,
        json=doctor_body(unique_suffix(), cedula="00112233445"),
    )
    assert duplicate.status_code == 201, duplicate.text
    again = await auth_client.post(
        DOCTORS_PATH,
        headers=headers,
        json=doctor_body(unique_suffix(), cedula="00112233445"),
    )
    conflict = assert_error_envelope(again, 409, "doctor_exists")
    assert set(conflict) == {"detail", "code", "errors"}
    assert "details" not in conflict


async def test_ca35_integrity_error_on_commit_becomes_409_and_session_survives(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-35: violacion de unicidad en el `commit` ⇒ 409 y la sesion sigue usable."""
    _, headers = await admin_actor(db_conn)
    cedula = unique_cedula()
    await create_doctor_row(db_conn, cedula=cedula)

    response = await auth_client.post(
        DOCTORS_PATH, headers=headers, json=doctor_body(unique_suffix(), cedula=cedula)
    )
    assert_error_envelope(response, 409, "doctor_exists")

    next_request = await auth_client.get(DOCTORS_PATH, headers=headers)
    assert next_request.status_code == 200, next_request.text


async def test_ca36_users_association_regressions_hold(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-36: la asociacion usuario-medico de FEAT-004 sigue intacta."""
    _, admin_headers = await admin_actor(db_conn)
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    admin_role_id = await get_role_id(db_conn, "ADMIN")

    doctor_a = await create_doctor_row(db_conn)
    doctor_b = await create_doctor_row(db_conn)
    user_a = await create_auth_user(db_conn, role="MEDICO", doctor_id=doctor_a)
    user_b = await create_auth_user(db_conn, role="MEDICO")

    def update_body(role_id: int, doctor_id: int | None) -> dict[str, object]:
        suffix = unique_suffix()
        return {
            "username": f"reg_{suffix}",
            "email": f"reg_{suffix}@example.test",
            "role_id": role_id,
            "doctor_id": doctor_id,
            "is_active": True,
        }

    reassign = await auth_client.put(
        f"{USERS_PATH}/{user_b}",
        headers=admin_headers,
        json=update_body(medico_role_id, doctor_a),
    )
    body = assert_error_envelope(reassign, 409, "doctor_already_assigned")
    assert body["detail"] == "El médico ya está asignado a otro usuario"

    admin_with_doctor = await auth_client.put(
        f"{USERS_PATH}/{user_a}",
        headers=admin_headers,
        json=update_body(admin_role_id, doctor_b),
    )
    body = assert_error_envelope(admin_with_doctor, 400, "admin_cannot_have_doctor")
    assert body["detail"] == "Un administrador no puede tener un médico asociado"
