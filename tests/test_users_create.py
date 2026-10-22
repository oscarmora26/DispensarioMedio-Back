"""Creacion de usuarios de FEAT-004 (CA-11 a CA-18)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import (
    LOGIN_PATH,
    ME_PATH,
    auth_headers,
    cached_password_hash,
    create_auth_user,
)
from tests.db_support import create_doctor, get_role_id, unique_suffix
from tests.users_support import (
    PASSWORD,
    USERS_PATH,
    admin_actor,
    assert_error_envelope,
    count_by_username,
    fetch_user_row,
)


def _create_payload(
    *,
    username: str,
    email: str,
    role_id: int,
    password: str = PASSWORD,
    **extra: object,
) -> dict[str, object]:
    body: dict[str, object] = {
        "username": username,
        "email": email,
        "password": password,
        "role_id": role_id,
    }
    body.update(extra)
    return body


async def test_ca11_create_medico_hashes_password_and_can_login(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11: 201 MEDICO con doctor, hash Argon2id en base y login + `/me`."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor(db_conn)
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    tag = unique_suffix()
    username = f"nuevo_{tag}"
    email = f"nuevo_{tag}@example.test"

    response = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=username, email=email, role_id=medico_role_id, doctor_id=doctor_id
        ),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "username", "email", "role", "doctor_id", "is_active"}
    assert body["username"] == username
    assert body["email"] == email
    assert body["role"] == "MEDICO"
    assert body["doctor_id"] == doctor_id
    assert body["is_active"] is True
    assert "password" not in body
    assert "password_hash" not in body

    row = await fetch_user_row(db_conn, body["id"])
    assert row is not None
    assert row["password_hash"].startswith("$argon2id$")
    assert row["role_id"] == medico_role_id
    assert row["created_at"] is not None
    assert row["updated_at"] is not None
    assert row["last_login_at"] is None

    login = await auth_client.post(LOGIN_PATH, json={"username": username, "password": PASSWORD})
    assert login.status_code == 200, login.text
    me = await auth_client.get(ME_PATH, headers=auth_headers(login.json()["access_token"]))
    assert me.status_code == 200
    assert me.json()["role"] == "MEDICO"


async def test_ca12_create_admin_and_inactive_user(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: ADMIN sin doctor y alta inactiva (login 403 `inactive_user`)."""
    _, headers = await admin_actor(db_conn)
    admin_role_id = await get_role_id(db_conn, "ADMIN")
    tag = unique_suffix()

    admin_response = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=f"nuevoadmin_{tag}",
            email=f"nuevoadmin_{tag}@example.test",
            role_id=admin_role_id,
        ),
    )
    assert admin_response.status_code == 201, admin_response.text
    assert admin_response.json()["doctor_id"] is None
    assert admin_response.json()["role"] == "ADMIN"

    inactive_username = f"inactivo_{tag}"
    inactive_response = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=inactive_username,
            email=f"inactivo_{tag}@example.test",
            role_id=admin_role_id,
            is_active=False,
        ),
    )
    assert inactive_response.status_code == 201, inactive_response.text
    assert inactive_response.json()["is_active"] is False
    row = await fetch_user_row(db_conn, inactive_response.json()["id"])
    assert row is not None
    assert row["is_active"] is False

    login = await auth_client.post(
        LOGIN_PATH, json={"username": inactive_username, "password": PASSWORD}
    )
    assert_error_envelope(login, 403, "inactive_user")


async def test_ca13_duplicates_are_case_insensitive_and_leave_original_untouched(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: duplicados CITEXT de username/email ⇒ 409 y sin cambios."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    username = f"original_{tag}"
    email = f"original_{tag}@example.test"
    user_id = await create_auth_user(
        db_conn,
        username=username,
        email=email,
        password=PASSWORD,
        role="MEDICO",
        password_hash=cached_password_hash(),
    )
    before = await fetch_user_row(db_conn, user_id)
    assert before is not None
    medico_role_id = await get_role_id(db_conn, "MEDICO")

    duplicate_username = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=username.upper(),
            email=f"otro_{tag}@example.test",
            role_id=medico_role_id,
        ),
    )
    assert_error_envelope(duplicate_username, 409, "user_exists")

    assert await count_by_username(db_conn, username) == 1
    after = await fetch_user_row(db_conn, user_id)
    assert after is not None
    assert after["password_hash"] == before["password_hash"]

    duplicate_email = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=f"distinto_{tag}",
            email=email.upper(),
            role_id=medico_role_id,
        ),
    )
    assert_error_envelope(duplicate_email, 409, "user_exists")
    assert await count_by_username(db_conn, f"distinto_{tag}") == 0


async def test_ca14_doctor_already_assigned_conflict(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-14: un `doctor_id` asignado ⇒ 409 y no se crea el usuario."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor(db_conn)
    await create_auth_user(
        db_conn,
        role="MEDICO",
        doctor_id=doctor_id,
        password_hash=cached_password_hash(),
    )
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    tag = unique_suffix()

    response = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=f"doctor_{tag}",
            email=f"doctor_{tag}@example.test",
            role_id=medico_role_id,
            doctor_id=doctor_id,
        ),
    )

    assert_error_envelope(response, 409, "doctor_already_assigned")
    assert await count_by_username(db_conn, f"doctor_{tag}") == 0


async def test_ca15_admin_cannot_have_doctor(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-15: ADMIN con `doctor_id` no nulo ⇒ 400 y no se crea la fila."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor(db_conn)
    admin_role_id = await get_role_id(db_conn, "ADMIN")
    tag = unique_suffix()

    response = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=f"adminmed_{tag}",
            email=f"adminmed_{tag}@example.test",
            role_id=admin_role_id,
            doctor_id=doctor_id,
        ),
    )

    assert_error_envelope(response, 400, "admin_cannot_have_doctor")
    assert await count_by_username(db_conn, f"adminmed_{tag}") == 0


async def test_ca16_invalid_role_and_doctor_with_precedence(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-16: `invalid_role`/`invalid_doctor` y precedencia sobre duplicados."""
    _, headers = await admin_actor(db_conn)
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    tag = unique_suffix()

    invalid_role = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=f"role_{tag}", email=f"role_{tag}@example.test", role_id=999_999
        ),
    )
    assert_error_envelope(invalid_role, 400, "invalid_role")

    invalid_doctor = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=f"doc_{tag}",
            email=f"doc_{tag}@example.test",
            role_id=medico_role_id,
            doctor_id=999_999,
        ),
    )
    assert_error_envelope(invalid_doctor, 400, "invalid_doctor")

    duplicate_username = f"duplicado_{tag}"
    await create_auth_user(
        db_conn,
        username=duplicate_username,
        email=f"dup_{tag}@example.test",
        role="MEDICO",
        password_hash=cached_password_hash(),
    )
    precedence = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=duplicate_username,
            email=f"nuevo_{tag}@example.test",
            role_id=999_999,
        ),
    )
    assert_error_envelope(precedence, 400, "invalid_role")


async def test_ca17_validation_errors_and_identity_normalization(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-17: 422 con `field` correcto, limites 8/128 y `strip` sin perder capitalizacion."""
    _, headers = await admin_actor(db_conn)
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    tag = unique_suffix()

    invalid_payloads = [
        (
            _create_payload(
                username=f"pw7_{tag}",
                email=f"pw7_{tag}@example.test",
                password="x" * 7,
                role_id=medico_role_id,
            ),
            "password",
        ),
        (
            _create_payload(
                username=f"pw129_{tag}",
                email=f"pw129_{tag}@example.test",
                password="x" * 129,
                role_id=medico_role_id,
            ),
            "password",
        ),
        (
            _create_payload(username="abc", email=f"u3_{tag}@example.test", role_id=medico_role_id),
            "username",
        ),
        (
            _create_payload(
                username="u" * 51,
                email=f"u51_{tag}@example.test",
                role_id=medico_role_id,
            ),
            "username",
        ),
        (
            _create_payload(
                username="ab cd",
                email=f"uspace_{tag}@example.test",
                role_id=medico_role_id,
            ),
            "username",
        ),
        (
            _create_payload(
                username=f"noat_{tag}", email="sin-arroba.test", role_id=medico_role_id
            ),
            "email",
        ),
        (
            _create_payload(
                username=f"longmail_{tag}",
                email=f"{'x' * 250}@example.test",
                role_id=medico_role_id,
            ),
            "email",
        ),
    ]

    for payload, field in invalid_payloads:
        response = await auth_client.post(USERS_PATH, headers=headers, json=payload)
        assert response.status_code == 422, (payload, response.text)
        body = response.json()
        assert body["code"] == "validation_error"
        assert field in {item["field"] for item in body["errors"]}, body

    empty = await auth_client.post(USERS_PATH, headers=headers, json={})
    assert empty.status_code == 422
    assert {item["field"] for item in empty.json()["errors"]} == {
        "username",
        "email",
        "password",
        "role_id",
    }

    short_ok = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=f"pw8_{tag}",
            email=f"pw8_{tag}@example.test",
            password="x" * 8,
            role_id=medico_role_id,
        ),
    )
    assert short_ok.status_code == 201, short_ok.text

    long_ok = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=f"pw128_{tag}",
            email=f"pw128_{tag}@example.test",
            password="y" * 128,
            role_id=medico_role_id,
        ),
    )
    assert long_ok.status_code == 201, long_ok.text

    normalized = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(
            username=f"  Aperez{tag}  ",
            email=f"  X{tag}@Y.TEST  ",
            role_id=medico_role_id,
        ),
    )
    assert normalized.status_code == 201, normalized.text
    assert normalized.json()["username"] == f"Aperez{tag}"
    assert normalized.json()["email"] == f"X{tag}@Y.TEST"
    row = await fetch_user_row(db_conn, normalized.json()["id"])
    assert row is not None
    assert row["username"] == f"Aperez{tag}"
    assert row["email"] == f"X{tag}@Y.TEST"


async def test_ca18_response_and_logs_never_expose_credentials(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-18: el 201 no expone secretos y `user.created` solo lleva ids."""
    actor_id, headers = await admin_actor(db_conn)
    caplog.set_level(logging.INFO)
    tag = unique_suffix()
    username = f"logger_{tag}"
    email = f"logger_{tag}@example.test"
    medico_role_id = await get_role_id(db_conn, "MEDICO")

    response = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json=_create_payload(username=username, email=email, role_id=medico_role_id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "password" not in body
    assert "password_hash" not in body
    assert "argon2" not in response.text

    events = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "user.created"
    ]
    assert len(events) == 1
    assert events[0]["user_id"] == body["id"]
    assert events[0]["actor_id"] == actor_id

    logged = caplog.text
    assert PASSWORD not in logged
    assert "$argon2" not in logged
    assert email not in logged
    assert username not in logged
    assert "password_hash" not in logged
