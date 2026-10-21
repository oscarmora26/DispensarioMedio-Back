"""Tests de integracion del cambio de contrasena (FEAT-002, CA-23 a CA-29)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import (
    CHANGE_PASSWORD_PATH,
    DEFAULT_PASSWORD,
    LOGIN_PATH,
    ME_PATH,
    auth_headers,
    create_auth_user,
    fetch_user,
    token_for,
)
from tests.db_support import unique_suffix

NEW_PASSWORD = "ClaveNueva456"


async def login_status(client: AsyncClient, username: str, password: str) -> int:
    response = await client.post(LOGIN_PATH, json={"username": username, "password": password})
    return int(response.status_code)


async def test_ca23_successful_change_invalidates_old_password(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-23: 200, la contrasena antigua deja de autenticar y la nueva funciona."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    before = (await fetch_user(db_conn, user_id))["password_hash"]

    response = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=auth_headers(token_for(user_id)),
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Contraseña actualizada correctamente"}

    after = (await fetch_user(db_conn, user_id))["password_hash"]
    assert after != before
    assert after.startswith("$argon2id$")

    assert await login_status(auth_client, username, DEFAULT_PASSWORD) == 401
    assert await login_status(auth_client, username, NEW_PASSWORD) == 200


async def test_ca24_wrong_current_password_keeps_hash(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-24: `current_password` incorrecta => 400 y hash intacto."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    before = (await fetch_user(db_conn, user_id))["password_hash"]

    response = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=auth_headers(token_for(user_id)),
        json={
            "current_password": "OtraClave999",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "invalid_current_password"
    assert (await fetch_user(db_conn, user_id))["password_hash"] == before
    assert await login_status(auth_client, username, DEFAULT_PASSWORD) == 200


async def test_ca25_new_password_must_differ_from_current(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-25: nueva igual a la actual => 400 `password_unchanged`."""
    user_id = await create_auth_user(db_conn, username=f"user_{unique_suffix()}")
    before = (await fetch_user(db_conn, user_id))["password_hash"]

    response = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=auth_headers(token_for(user_id)),
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": DEFAULT_PASSWORD,
            "confirm_password": DEFAULT_PASSWORD,
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "password_unchanged"
    assert (await fetch_user(db_conn, user_id))["password_hash"] == before


async def test_ca26_confirmation_mismatch_is_422(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-26: confirmacion distinta => 422 con el ítem exacto."""
    user_id = await create_auth_user(db_conn, username=f"user_{unique_suffix()}")
    before = (await fetch_user(db_conn, user_id))["password_hash"]

    response = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=auth_headers(token_for(user_id)),
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": "OtraDistinta456",
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"] == [
        {"field": "confirm_password", "message": "Las contraseñas no coinciden"}
    ]
    assert (await fetch_user(db_conn, user_id))["password_hash"] == before


async def test_ca27_password_length_boundaries(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-27: 7 caracteres => 422, 8 => 200 y `current_password` de 129 => 422."""
    user_id = await create_auth_user(db_conn, username=f"user_{unique_suffix()}")
    headers = auth_headers(token_for(user_id))

    too_short = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=headers,
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": "1234567",
            "confirm_password": "1234567",
        },
    )
    assert too_short.status_code == 422
    assert any(item["field"] == "new_password" for item in too_short.json()["errors"])

    too_long_current = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=headers,
        json={
            "current_password": "x" * 129,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert too_long_current.status_code == 422
    assert any(item["field"] == "current_password" for item in too_long_current.json()["errors"])

    exact = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=headers,
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": "12345678",
            "confirm_password": "12345678",
        },
    )
    assert exact.status_code == 200


async def test_ca28_change_requires_authentication_and_active_user(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-28: sin token => 401; usuario inactivo => 403; hash intacto."""
    user_id = await create_auth_user(db_conn, username=f"user_{unique_suffix()}")
    before = (await fetch_user(db_conn, user_id))["password_hash"]
    payload = {
        "current_password": DEFAULT_PASSWORD,
        "new_password": NEW_PASSWORD,
        "confirm_password": NEW_PASSWORD,
    }

    anonymous = await auth_client.post(CHANGE_PASSWORD_PATH, json=payload)
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "not_authenticated"

    inactive_id = await create_auth_user(
        db_conn, username=f"inactivo_{unique_suffix()}", is_active=False
    )
    inactive_before = (await fetch_user(db_conn, inactive_id))["password_hash"]
    inactive = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=auth_headers(token_for(inactive_id)),
        json=payload,
    )
    assert inactive.status_code == 403
    assert inactive.json()["code"] == "inactive_user"

    assert (await fetch_user(db_conn, user_id))["password_hash"] == before
    assert (await fetch_user(db_conn, inactive_id))["password_hash"] == inactive_before


async def test_ca29_existing_token_stays_valid_after_change(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-29: D-3, el token vigente sigue valido y se puede volver a cambiar."""
    user_id = await create_auth_user(db_conn, username=f"user_{unique_suffix()}")
    token = token_for(user_id)

    first = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=auth_headers(token),
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert first.status_code == 200

    me = await auth_client.get(ME_PATH, headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["id"] == user_id

    second = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=auth_headers(token),
        json={
            "current_password": NEW_PASSWORD,
            "new_password": "TerceraClave789",
            "confirm_password": "TerceraClave789",
        },
    )
    assert second.status_code == 200
