"""Restablecimiento de contrasena de FEAT-004 (CA-30 a CA-33)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import LOGIN_PATH, auth_headers
from tests.users_support import (
    NEW_PASSWORD,
    PASSWORD,
    admin_actor,
    assert_error_envelope,
    fetch_user_row,
    medico_user,
    nonexistent_user_id,
    reset_path,
)


async def test_ca30_reset_password_replaces_hash_and_credentials(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-30: reset con 8 caracteres, mensaje exacto y cambio real de contrasena."""
    _, headers = await admin_actor(db_conn)
    target_id, username, _ = await medico_user(db_conn)
    before = await fetch_user_row(db_conn, target_id)
    assert before is not None
    new_password = "Abcd1234"

    response = await auth_client.post(
        reset_path(target_id), headers=headers, json={"new_password": new_password}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Contraseña restablecida correctamente"}
    assert new_password not in response.text

    after = await fetch_user_row(db_conn, target_id)
    assert after is not None
    assert after["password_hash"] != before["password_hash"]
    assert after["password_hash"].startswith("$argon2id$")
    assert after["updated_at"] > before["updated_at"]

    old_login = await auth_client.post(
        LOGIN_PATH, json={"username": username, "password": PASSWORD}
    )
    assert_error_envelope(old_login, 401, "invalid_credentials")

    new_login = await auth_client.post(
        LOGIN_PATH, json={"username": username, "password": new_password}
    )
    assert new_login.status_code == 200, new_login.text


async def test_ca31_reset_on_inactive_user_keeps_it_inactive(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-31: reset sobre inactivo cambia el hash pero el login sigue en 403."""
    _, headers = await admin_actor(db_conn)
    target_id, username, _ = await medico_user(db_conn, is_active=False)
    before = await fetch_user_row(db_conn, target_id)
    assert before is not None

    response = await auth_client.post(
        reset_path(target_id), headers=headers, json={"new_password": NEW_PASSWORD}
    )

    assert response.status_code == 200, response.text
    after = await fetch_user_row(db_conn, target_id)
    assert after is not None
    assert after["password_hash"] != before["password_hash"]
    assert after["is_active"] is False

    login = await auth_client.post(
        LOGIN_PATH, json={"username": username, "password": NEW_PASSWORD}
    )
    assert_error_envelope(login, 403, "inactive_user")


async def test_ca32_reset_own_account_is_allowed(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-32: el ADMIN puede restablecer su propia contrasena (D-7)."""
    actor_id, headers = await admin_actor(db_conn)
    actor = await fetch_user_row(db_conn, actor_id)
    assert actor is not None

    response = await auth_client.post(
        reset_path(actor_id), headers=headers, json={"new_password": NEW_PASSWORD}
    )
    assert response.status_code == 200, response.text

    login = await auth_client.post(
        LOGIN_PATH, json={"username": actor["username"], "password": NEW_PASSWORD}
    )
    assert login.status_code == 200, login.text


async def test_ca33_reset_errors_and_medico_is_forbidden(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-33: 404, 422 con `field=new_password` y 403 para MEDICO sin cambios."""
    _, headers = await admin_actor(db_conn)
    target_id, _, _ = await medico_user(db_conn)
    before = await fetch_user_row(db_conn, target_id)
    assert before is not None

    missing = await auth_client.post(
        reset_path(await nonexistent_user_id(db_conn)),
        headers=headers,
        json={"new_password": NEW_PASSWORD},
    )
    assert_error_envelope(missing, 404, "user_not_found")

    for body in (
        {"new_password": "x" * 7},
        {"new_password": "x" * 129},
        {},
    ):
        response = await auth_client.post(reset_path(target_id), headers=headers, json=body)
        assert response.status_code == 422, (body, response.text)
        assert response.json()["code"] == "validation_error"
        assert {item["field"] for item in response.json()["errors"]} == {"new_password"}

    _, _, medico_token = await medico_user(db_conn)
    forbidden = await auth_client.post(
        reset_path(target_id),
        headers=auth_headers(medico_token),
        json={"new_password": NEW_PASSWORD},
    )
    assert_error_envelope(forbidden, 403, "forbidden")

    after = await fetch_user_row(db_conn, target_id)
    assert after is not None
    assert after["password_hash"] == before["password_hash"]
