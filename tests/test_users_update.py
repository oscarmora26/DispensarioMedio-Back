"""Actualizacion de usuarios de FEAT-004 (CA-20 a CA-27)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import LOGIN_PATH, auth_headers, create_auth_user
from tests.db_support import create_doctor, get_role_id, unique_suffix
from tests.users_support import (
    PASSWORD,
    USERS_PATH,
    admin_actor,
    assert_error_envelope,
    count_active_admins,
    disable_other_admins,
    fetch_user_row,
    medico_user,
    nonexistent_user_id,
    put_body,
    user_path,
)


async def test_ca20_full_replacement_keeps_created_at_and_password_hash(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-20: PUT completo cambia identidad/rol, conserva `created_at` y el hash."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor(db_conn)
    target_id, _, _ = await medico_user(db_conn, doctor_id=doctor_id)
    before = await fetch_user_row(db_conn, target_id)
    assert before is not None
    admin_role_id = await get_role_id(db_conn, "ADMIN")
    tag = unique_suffix()

    response = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=f"renombrado_{tag}",
            email=f"renombrado_{tag}@example.test",
            role_id=admin_role_id,
            doctor_id=None,
            is_active=True,
        ),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"id", "username", "email", "role", "doctor_id", "is_active"}
    assert body["username"] == f"renombrado_{tag}"
    assert body["email"] == f"renombrado_{tag}@example.test"
    assert body["role"] == "ADMIN"
    assert body["doctor_id"] is None
    assert body["is_active"] is True

    after = await fetch_user_row(db_conn, target_id)
    assert after is not None
    assert after["username"] == f"renombrado_{tag}"
    assert after["email"] == f"renombrado_{tag}@example.test"
    assert after["role_id"] == admin_role_id
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] > before["updated_at"]
    assert after["password_hash"] == before["password_hash"]

    login = await auth_client.post(
        LOGIN_PATH,
        json={"username": f"renombrado_{tag}", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text


async def test_ca21_update_reactivates_user(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-21: `is_active: true` reactiva y el login pasa de 403 a 200."""
    _, headers = await admin_actor(db_conn)
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    target_id, username, _ = await medico_user(db_conn, is_active=False)

    inactive_login = await auth_client.post(
        LOGIN_PATH, json={"username": username, "password": PASSWORD}
    )
    assert_error_envelope(inactive_login, 403, "inactive_user")

    response = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=username,
            email=f"{username}@example.test",
            role_id=medico_role_id,
            doctor_id=None,
            is_active=True,
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True

    login = await auth_client.post(LOGIN_PATH, json={"username": username, "password": PASSWORD})
    assert login.status_code == 200, login.text


async def test_ca22_update_deactivates_user_and_revokes_live_token(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-22: desactivar bloquea el login y el token emitido antes recibe 403."""
    _, headers = await admin_actor(db_conn)
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    target_id, username, target_token = await medico_user(db_conn)

    response = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=username,
            email=f"{username}@example.test",
            role_id=medico_role_id,
            doctor_id=None,
            is_active=False,
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False

    login = await auth_client.post(LOGIN_PATH, json={"username": username, "password": PASSWORD})
    assert_error_envelope(login, 403, "inactive_user")

    stale = await auth_client.get(USERS_PATH, headers=auth_headers(target_token))
    assert_error_envelope(stale, 403, "inactive_user")


async def test_ca23_cannot_deactivate_self(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-23: PUT sobre el actor con `is_active: false` ⇒ 400 y sin cambios."""
    actor_id, headers = await admin_actor(db_conn)
    actor = await fetch_user_row(db_conn, actor_id)
    assert actor is not None

    response = await auth_client.put(
        user_path(actor_id),
        headers=headers,
        json=put_body(
            username=actor["username"],
            email=actor["email"],
            role_id=actor["role_id"],
            doctor_id=None,
            is_active=False,
        ),
    )

    assert_error_envelope(response, 400, "cannot_deactivate_self")
    after = await fetch_user_row(db_conn, actor_id)
    assert after is not None
    assert after["is_active"] is True
    assert after["password_hash"] == actor["password_hash"]


async def test_ca24_last_admin_rule_on_self_demotion(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-24: el unico ADMIN activo no puede dejar de serlo; con otro, si."""
    actor_id, headers = await admin_actor(db_conn)
    actor = await fetch_user_row(db_conn, actor_id)
    assert actor is not None
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    await disable_other_admins(db_conn, actor_id)

    blocked = await auth_client.put(
        user_path(actor_id),
        headers=headers,
        json=put_body(
            username=actor["username"],
            email=actor["email"],
            role_id=medico_role_id,
            doctor_id=None,
            is_active=True,
        ),
    )
    assert_error_envelope(blocked, 400, "last_admin")
    still_admin = await auth_client.get(user_path(actor_id), headers=headers)
    assert still_admin.status_code == 200
    assert still_admin.json()["role"] == "ADMIN"

    await create_auth_user(db_conn, role="ADMIN")

    allowed = await auth_client.put(
        user_path(actor_id),
        headers=headers,
        json=put_body(
            username=actor["username"],
            email=actor["email"],
            role_id=medico_role_id,
            doctor_id=None,
            is_active=True,
        ),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["role"] == "MEDICO"

    lost_access = await auth_client.get(USERS_PATH, headers=headers)
    assert_error_envelope(lost_access, 403, "forbidden")


async def test_ca25_update_duplicates_exclude_self(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-25: duplicados ajenos ⇒ 409; los propios (con otra caja) ⇒ 200."""
    _, headers = await admin_actor(db_conn)
    doctor_a = await create_doctor(db_conn)
    doctor_b = await create_doctor(db_conn)
    target_id, target_username, _ = await medico_user(db_conn, doctor_id=doctor_a)
    other_id, other_username, _ = await medico_user(db_conn, doctor_id=doctor_b)
    other = await fetch_user_row(db_conn, other_id)
    target = await fetch_user_row(db_conn, target_id)
    assert other is not None and target is not None
    medico_role_id = await get_role_id(db_conn, "MEDICO")

    other_username_conflict = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=other_username.upper(),
            email=target["email"],
            role_id=medico_role_id,
            doctor_id=doctor_a,
            is_active=True,
        ),
    )
    assert_error_envelope(other_username_conflict, 409, "user_exists")

    other_email_conflict = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=target_username,
            email=other["email"].upper(),
            role_id=medico_role_id,
            doctor_id=doctor_a,
            is_active=True,
        ),
    )
    assert_error_envelope(other_email_conflict, 409, "user_exists")

    other_doctor_conflict = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=target_username,
            email=target["email"],
            role_id=medico_role_id,
            doctor_id=doctor_b,
            is_active=True,
        ),
    )
    assert_error_envelope(other_doctor_conflict, 409, "doctor_already_assigned")

    own_values = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=target_username.upper(),
            email=target["email"].upper(),
            role_id=medico_role_id,
            doctor_id=doctor_a,
            is_active=True,
        ),
    )
    assert own_values.status_code == 200, own_values.text
    assert own_values.json()["username"] == target_username.upper()
    assert own_values.json()["doctor_id"] == doctor_a


async def test_ca26_update_body_errors_partial_body_and_password_ignored(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-26: errores de body, 404, body parcial y `password` ignorado."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor(db_conn)
    target_id, target_username, _ = await medico_user(db_conn, doctor_id=doctor_id)
    target = await fetch_user_row(db_conn, target_id)
    assert target is not None
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    admin_role_id = await get_role_id(db_conn, "ADMIN")

    base = put_body(
        username=target_username,
        email=target["email"],
        role_id=medico_role_id,
        doctor_id=doctor_id,
        is_active=True,
    )

    invalid_role = await auth_client.put(
        user_path(target_id), headers=headers, json={**base, "role_id": 999_999}
    )
    assert_error_envelope(invalid_role, 400, "invalid_role")

    invalid_doctor = await auth_client.put(
        user_path(target_id), headers=headers, json={**base, "doctor_id": 999_999}
    )
    assert_error_envelope(invalid_doctor, 400, "invalid_doctor")

    admin_with_doctor = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json={**base, "role_id": admin_role_id, "doctor_id": doctor_id},
    )
    assert_error_envelope(admin_with_doctor, 400, "admin_cannot_have_doctor")

    missing_user = await auth_client.put(
        user_path(await nonexistent_user_id(db_conn)), headers=headers, json=base
    )
    assert_error_envelope(missing_user, 404, "user_not_found")

    partial = {key: value for key, value in base.items() if key != "is_active"}
    partial_response = await auth_client.put(user_path(target_id), headers=headers, json=partial)
    assert partial_response.status_code == 422
    assert {item["field"] for item in partial_response.json()["errors"]} == {"is_active"}

    with_password = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json={**base, "password": "OtraClave999"},
    )
    assert with_password.status_code == 200, with_password.text
    after = await fetch_user_row(db_conn, target_id)
    assert after is not None
    assert after["password_hash"] == target["password_hash"]
    login = await auth_client.post(
        LOGIN_PATH, json={"username": target_username, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text


async def test_ca27_deactivate_another_admin_counts_other_admins(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-27: desactivar a otro ADMIN con un tercero activo ⇒ 200 y baja el conteo."""
    actor_id, headers = await admin_actor(db_conn)
    target_id = await create_auth_user(db_conn, role="ADMIN")
    await create_auth_user(db_conn, role="ADMIN")
    target = await fetch_user_row(db_conn, target_id)
    assert target is not None
    admin_role_id = await get_role_id(db_conn, "ADMIN")
    before = await count_active_admins(db_conn)
    assert actor_id != target_id

    response = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=target["username"],
            email=target["email"],
            role_id=admin_role_id,
            doctor_id=None,
            is_active=False,
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False
    assert await count_active_admins(db_conn) == before - 1
