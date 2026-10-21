"""Autorizacion y contrato de errores de FEAT-004 (CA-1, CA-2, CA-3, CA-36)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, create_auth_user, token_for
from tests.db_support import create_doctor, unique_suffix
from tests.users_support import (
    NEW_PASSWORD,
    PASSWORD,
    USERS_PATH,
    admin_actor,
    assert_error_envelope,
    count_by_username,
    disable_other_admins,
    fetch_user_row,
    get_role_id,
    medico_user,
    nonexistent_user_id,
    put_body,
    reset_path,
    user_path,
)


async def test_ca01_medico_is_forbidden_on_all_six_endpoints_without_writes(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: MEDICO recibe 403 `forbidden` en los seis endpoints y no escribe."""
    doctor_id = await create_doctor(db_conn)
    _, _, actor_token = await medico_user(db_conn, doctor_id=doctor_id)
    target_id = await create_auth_user(db_conn, username=f"target_{unique_suffix()}", role="MEDICO")
    before = await fetch_user_row(db_conn, target_id)
    assert before is not None

    suffix = unique_suffix()
    new_username = f"nuevo_{suffix}"
    headers = auth_headers(actor_token)

    responses = [
        await auth_client.get(USERS_PATH, headers=headers),
        await auth_client.post(
            USERS_PATH,
            headers=headers,
            json={
                "username": new_username,
                "email": f"{new_username}@example.test",
                "password": PASSWORD,
                "role_id": 2,
            },
        ),
        await auth_client.get(user_path(target_id), headers=headers),
        await auth_client.put(
            user_path(target_id),
            headers=headers,
            json=put_body(
                username=f"otro_{suffix}",
                email=f"otro_{suffix}@example.test",
                role_id=1,
                doctor_id=None,
                is_active=False,
            ),
        ),
        await auth_client.delete(user_path(target_id), headers=headers),
        await auth_client.post(
            reset_path(target_id), headers=headers, json={"new_password": NEW_PASSWORD}
        ),
    ]

    for response in responses:
        body = assert_error_envelope(response, 403, "forbidden")
        assert body["errors"] == []

    after = await fetch_user_row(db_conn, target_id)
    assert after is not None
    assert after["password_hash"] == before["password_hash"]
    assert after["is_active"] is True
    assert await count_by_username(db_conn, new_username) == 0


async def test_ca02_authentication_failures_share_the_contract(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: 401 sin cabecera/Basic/token invalido y 403 de ADMIN inactivo."""
    actor_id, _ = await admin_actor(db_conn)

    missing = await auth_client.get(USERS_PATH)
    body = assert_error_envelope(missing, 401, "not_authenticated")
    assert missing.headers["www-authenticate"].lower().startswith("bearer")
    assert body["errors"] == []

    basic = await auth_client.get(USERS_PATH, headers={"Authorization": "Basic abc"})
    assert_error_envelope(basic, 401, "not_authenticated")
    assert basic.headers["www-authenticate"].lower().startswith("bearer")

    garbage = await auth_client.get(USERS_PATH, headers={"Authorization": "Bearer basura"})
    assert_error_envelope(garbage, 401, "invalid_token")

    other_secret = token_for(actor_id, secret="otro-secreto-de-prueba-1234567890")
    foreign = await auth_client.get(USERS_PATH, headers=auth_headers(other_secret))
    assert_error_envelope(foreign, 401, "invalid_token")

    unknown = await auth_client.get(USERS_PATH, headers=auth_headers(token_for(999_999_999)))
    assert_error_envelope(unknown, 401, "invalid_token")

    expired = await auth_client.get(
        USERS_PATH, headers=auth_headers(token_for(actor_id, expires_minutes=-1))
    )
    assert_error_envelope(expired, 401, "invalid_token")

    inactive_id = await create_auth_user(
        db_conn, username=f"inactivo_{unique_suffix()}", role="ADMIN", is_active=False
    )
    inactive_token = token_for(inactive_id)
    for response in (
        await auth_client.get(USERS_PATH, headers=auth_headers(inactive_token)),
        await auth_client.post(
            USERS_PATH,
            headers=auth_headers(inactive_token),
            json={
                "username": f"x_{unique_suffix()}",
                "email": f"x_{unique_suffix()}@example.test",
                "password": PASSWORD,
                "role_id": 2,
            },
        ),
    ):
        assert_error_envelope(response, 403, "inactive_user")


async def test_ca03_unknown_user_returns_real_404_without_details_key(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: 404 `user_not_found` con envelope exacto y sin `details`."""
    _, headers = await admin_actor(db_conn)

    response = await auth_client.get(user_path(await nonexistent_user_id(db_conn)), headers=headers)

    body = assert_error_envelope(response, 404, "user_not_found")
    assert body["detail"] == "El usuario no existe"
    assert body["errors"] == []


async def test_ca36_envelope_and_codes_are_uniform_per_catalog(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-36: al menos un caso por codigo del catalogo con el envelope exacto."""
    actor_id, headers = await admin_actor(db_conn)
    actor = await fetch_user_row(db_conn, actor_id)
    assert actor is not None
    doctor_id = await create_doctor(db_conn)
    target_id, _, _ = await medico_user(db_conn, doctor_id=doctor_id)

    suffix = unique_suffix()
    duplicate_username = f"dup_{suffix}"
    await create_auth_user(
        db_conn, username=duplicate_username, email=f"{suffix}@example.test", role="MEDICO"
    )

    cases = [
        (
            await auth_client.get(user_path(await nonexistent_user_id(db_conn)), headers=headers),
            404,
            "user_not_found",
        ),
        (
            await auth_client.post(
                USERS_PATH,
                headers=headers,
                json={
                    "username": f"r_{suffix}",
                    "email": f"r_{suffix}@example.test",
                    "password": PASSWORD,
                    "role_id": 999_999,
                },
            ),
            400,
            "invalid_role",
        ),
        (
            await auth_client.post(
                USERS_PATH,
                headers=headers,
                json={
                    "username": f"d_{suffix}",
                    "email": f"d_{suffix}@example.test",
                    "password": PASSWORD,
                    "role_id": 2,
                    "doctor_id": 999_999,
                },
            ),
            400,
            "invalid_doctor",
        ),
        (
            await auth_client.post(
                USERS_PATH,
                headers=headers,
                json={
                    "username": f"a_{suffix}",
                    "email": f"a_{suffix}@example.test",
                    "password": PASSWORD,
                    "role_id": 1,
                    "doctor_id": doctor_id,
                },
            ),
            400,
            "admin_cannot_have_doctor",
        ),
        (
            await auth_client.post(
                USERS_PATH,
                headers=headers,
                json={
                    "username": duplicate_username,
                    "email": f"otro_{suffix}@example.test",
                    "password": PASSWORD,
                    "role_id": 2,
                },
            ),
            409,
            "user_exists",
        ),
        (
            await auth_client.post(
                USERS_PATH,
                headers=headers,
                json={
                    "username": f"o_{suffix}",
                    "email": f"o_{suffix}@example.test",
                    "password": PASSWORD,
                    "role_id": 2,
                    "doctor_id": doctor_id,
                },
            ),
            409,
            "doctor_already_assigned",
        ),
        (
            await auth_client.post(USERS_PATH, headers=headers, json={"username": f"v_{suffix}"}),
            422,
            "validation_error",
        ),
        (
            await auth_client.get(USERS_PATH, headers={"Authorization": "Bearer basura"}),
            401,
            "invalid_token",
        ),
        (await auth_client.get(USERS_PATH), 401, "not_authenticated"),
    ]

    for response, status, code in cases:
        assert_error_envelope(response, status, code)

    # `cannot_deactivate_self`.
    self_response = await auth_client.put(
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
    assert_error_envelope(self_response, 400, "cannot_deactivate_self")

    # `last_admin`: el actor es el unico ADMIN activo y se cambia el rol.
    await disable_other_admins(db_conn, actor_id)
    last_admin = await auth_client.put(
        user_path(actor_id),
        headers=headers,
        json=put_body(
            username=actor["username"],
            email=actor["email"],
            role_id=await get_role_id(db_conn, "MEDICO"),
            doctor_id=None,
            is_active=True,
        ),
    )
    assert_error_envelope(last_admin, 400, "last_admin")

    # `forbidden` (MEDICO) e `inactive_user`.
    medico_token = token_for(target_id, role="MEDICO", doctor_id=doctor_id)
    assert_error_envelope(
        await auth_client.get(USERS_PATH, headers=auth_headers(medico_token)), 403, "forbidden"
    )

    inactive_id = await create_auth_user(
        db_conn, username=f"in_{unique_suffix()}", role="ADMIN", is_active=False
    )
    assert_error_envelope(
        await auth_client.get(USERS_PATH, headers=auth_headers(token_for(inactive_id))),
        403,
        "inactive_user",
    )

    # D-13: los 422 de query y path no filtran la ubicacion del parametro.
    invalid_size = await auth_client.get(USERS_PATH, headers=headers, params={"size": 0})
    assert_error_envelope(invalid_size, 422, "validation_error")
    assert {item["field"] for item in invalid_size.json()["errors"]} == {"size"}

    invalid_path = await auth_client.get(f"{USERS_PATH}/abc", headers=headers)
    assert_error_envelope(invalid_path, 422, "validation_error")
    assert {item["field"] for item in invalid_path.json()["errors"]} == {"user_id"}
