"""Tests de refresh tokens: emision, rotacion, reuso, logout y cascada (FEAT-020)."""

from __future__ import annotations

import asyncio
import itertools
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.exceptions import UnauthorizedError
from app.core.security import generate_refresh_token, hash_refresh_token
from app.services import auth_service
from app.services.auth_service import RefreshResult
from tests.auth_support import (
    DEFAULT_PASSWORD,
    LOGIN_PATH,
    ME_PATH,
    auth_headers,
    create_auth_user,
    token_payload,
)
from tests.db_support import get_role_id, resolve_test_database_url, unique_suffix
from tests.users_support import admin_actor, put_body, user_path

REFRESH_PATH = "/api/v1/auth/refresh"
LOGOUT_PATH = "/api/v1/auth/logout"
CHANGE_PASSWORD_PATH = "/api/v1/auth/change-password"

ACCESS_TTL_SECONDS = 900
REFRESH_TTL_SECONDS = 30 * 86400


async def login(
    client: AsyncClient, username: str, *, user_agent: str | None = None
) -> dict[str, Any]:
    """Hace login y devuelve el body completo de la respuesta."""
    headers = {"User-Agent": user_agent} if user_agent else None
    response = await client.post(
        LOGIN_PATH,
        json={"username": username, "password": DEFAULT_PASSWORD},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def refresh(client: AsyncClient, token: str) -> Any:
    """Llama a `/auth/refresh` con el token indicado."""
    return await client.post(REFRESH_PATH, json={"refresh_token": token})


async def fetch_tokens(conn: AsyncConnection, user_id: int) -> list[dict[str, Any]]:
    """Filas de refresh tokens del usuario ordenadas por id."""
    result = await conn.execute(
        text(
            """
            SELECT id, token_hash, family_id, expires_at, last_used_at, revoked_at,
                   revoked_reason, replaced_by_id, created_by_ip, user_agent
            FROM refresh_tokens
            WHERE user_id = :user_id
            ORDER BY id
            """
        ),
        {"user_id": user_id},
    )
    return [dict(row) for row in result.mappings()]


async def total_tokens(conn: AsyncConnection) -> int:
    """Cantidad total de refresh tokens (el endpoint corre en otra conexion)."""
    result = await conn.execute(text("SELECT count(*) FROM refresh_tokens"))
    return int(result.scalar_one())


# --- CA-1…CA-4: login y emision ------------------------------------------------


async def test_ca1_login_returns_the_refresh_pair(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: el login agrega `refresh_token` y `refresh_expires_in` (900 s / 30 dias)."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)

    body = await login(auth_client, username)

    assert set(body) == {
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "refresh_expires_in",
        "user",
    }
    assert body["expires_in"] == ACCESS_TTL_SECONDS
    assert body["refresh_expires_in"] == REFRESH_TTL_SECONDS
    assert isinstance(body["refresh_token"], str)
    assert len(body["refresh_token"]) >= 64


async def test_ca2_only_the_hash_is_persisted(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: se guarda el SHA-256, con familia nueva y la IP del cliente."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)

    body = await login(auth_client, username)
    rows = await fetch_tokens(db_conn, user_id)

    assert len(rows) == 1
    row = rows[0]
    assert row["token_hash"] == hash_refresh_token(body["refresh_token"])
    assert len(row["token_hash"]) == 64
    assert body["refresh_token"] not in row["token_hash"]
    assert row["family_id"] is not None
    assert row["created_by_ip"] == "127.0.0.1"
    assert row["last_used_at"] is None
    assert row["revoked_at"] is None
    assert row["replaced_by_id"] is None


async def test_ca2b_user_agent_is_recorded_and_truncated(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: el `user_agent` se guarda truncado a 255 caracteres."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)

    await login(auth_client, username, user_agent="x" * 400)
    rows = await fetch_tokens(db_conn, user_id)

    assert len(rows[0]["user_agent"]) == 255


async def test_ca3_each_login_opens_a_new_family(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: dos logins del mismo usuario crean familias distintas."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)

    await login(auth_client, username)
    await login(auth_client, username)
    rows = await fetch_tokens(db_conn, user_id)

    assert len(rows) == 2
    assert rows[0]["family_id"] != rows[1]["family_id"]
    assert rows[0]["token_hash"] != rows[1]["token_hash"]


async def test_ca4_expired_tokens_are_purged_on_login(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4: el login borra solo los tokens expirados de ese usuario."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    other_name = f"other_{unique_suffix()}"
    other_id = await create_auth_user(db_conn, username=other_name)

    await login(auth_client, username)
    await login(auth_client, other_name)
    first = await fetch_tokens(db_conn, user_id)

    # El token del usuario se marca expirado; el del otro usuario sigue vigente.
    await db_conn.execute(
        text("UPDATE refresh_tokens SET expires_at = :past"),
        {"past": datetime.now(UTC) - timedelta(days=1)},
    )
    await db_conn.execute(
        text("UPDATE refresh_tokens SET expires_at = :future WHERE user_id = :uid"),
        {"future": datetime.now(UTC) + timedelta(days=1), "uid": other_id},
    )

    await login(auth_client, username)

    rows = await fetch_tokens(db_conn, user_id)
    other_rows = await fetch_tokens(db_conn, other_id)
    surviving_ids = {row["id"] for row in rows}

    assert first[0]["id"] not in surviving_ids
    assert len(rows) == 1
    assert len(other_rows) == 1


# --- CA-5…CA-9: refresh feliz --------------------------------------------------


async def test_ca5_refresh_rotates_and_returns_a_new_pair(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: responde 200 con access y refresh nuevos, distintos del anterior."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)

    response = await refresh(auth_client, session["refresh_token"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "refresh_expires_in",
        "user",
    }
    assert body["expires_in"] == ACCESS_TTL_SECONDS
    assert body["refresh_expires_in"] == REFRESH_TTL_SECONDS
    assert body["refresh_token"] != session["refresh_token"]
    assert body["user"]["role"] == "ADMIN"
    # El JWT es determinista dentro del mismo segundo: se verifica la vigencia, no la
    # desigualdad literal de dos tokens emitidos con el mismo `iat`.
    claims = token_payload(body["access_token"])
    assert claims["exp"] - claims["iat"] == ACCESS_TTL_SECONDS
    assert claims["sub"] == str(body["user"]["id"])


async def test_ca6_rotation_links_predecessor_and_successor(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: el token usado apunta al sucesor y queda con `last_used_at`."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)

    await refresh(auth_client, session["refresh_token"])
    rows = await fetch_tokens(db_conn, user_id)

    assert len(rows) == 2
    predecessor, successor = rows
    assert predecessor["replaced_by_id"] == successor["id"]
    assert predecessor["last_used_at"] is not None
    assert predecessor["family_id"] == successor["family_id"]
    assert successor["replaced_by_id"] is None


async def test_ca7_refreshed_access_token_authenticates(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: el access token renovado sirve en `/auth/me`."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)

    response = await refresh(auth_client, session["refresh_token"])
    me = await auth_client.get(ME_PATH, headers=auth_headers(response.json()["access_token"]))

    assert me.status_code == 200
    assert me.json()["username"] == username


async def test_ca8_chain_of_refreshes_shares_one_family(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-8: la cadena completa comparte un solo `family_id`."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    current = (await login(auth_client, username))["refresh_token"]

    for _ in range(3):
        response = await refresh(auth_client, current)
        assert response.status_code == 200, response.text
        current = response.json()["refresh_token"]

    rows = await fetch_tokens(db_conn, user_id)
    assert len(rows) == 4
    assert len({row["family_id"] for row in rows}) == 1
    for older, newer in itertools.pairwise(rows):
        assert older["replaced_by_id"] == newer["id"]


async def test_ca9_current_role_and_doctor_travel_in_the_new_access_token(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-9: los claims se leen de la base, no del token viejo."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)

    await db_conn.execute(
        text(
            "UPDATE users SET role_id = (SELECT id FROM roles WHERE name = 'MEDICO') WHERE id = :id"
        ),
        {"id": user_id},
    )

    response = await refresh(auth_client, session["refresh_token"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["role"] == "MEDICO"
    assert token_payload(body["access_token"])["role"] == "MEDICO"


# --- CA-10…CA-17: errores y seguridad ------------------------------------------


async def test_ca10_all_failures_share_the_same_body(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: desconocido, expirado, revocado y rotado responden igual."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)

    unknown = await refresh(auth_client, "token-que-no-existe")

    expired_session = await login(auth_client, username)
    await db_conn.execute(
        text("UPDATE refresh_tokens SET expires_at = :past WHERE user_id = :uid"),
        {"past": datetime.now(UTC) - timedelta(days=1), "uid": user_id},
    )
    expired = await refresh(auth_client, expired_session["refresh_token"])

    revoked_session = await login(auth_client, username)
    await db_conn.execute(
        text(
            "UPDATE refresh_tokens SET revoked_at = :now, revoked_reason = 'logout' "
            "WHERE user_id = :uid AND revoked_at IS NULL"
        ),
        {"now": datetime.now(UTC), "uid": user_id},
    )
    revoked = await refresh(auth_client, revoked_session["refresh_token"])

    rotated_session = await login(auth_client, username)
    await refresh(auth_client, rotated_session["refresh_token"])
    rotated = await refresh(auth_client, rotated_session["refresh_token"])

    assert unknown.status_code == 401
    assert expired.status_code == 401
    assert revoked.status_code == 401
    assert rotated.status_code == 401
    bodies = [response.json() for response in (unknown, expired, revoked, rotated)]
    assert bodies[0] == bodies[1] == bodies[2] == bodies[3]
    assert bodies[0] == {"detail": "Token inválido", "code": "invalid_token", "errors": []}


async def test_ca11_expired_token_leaves_the_row_untouched(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11: un token expirado no se marca ni se rota."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)
    await db_conn.execute(
        text("UPDATE refresh_tokens SET expires_at = :past WHERE user_id = :uid"),
        {"past": datetime.now(UTC) - timedelta(days=1), "uid": user_id},
    )

    response = await refresh(auth_client, session["refresh_token"])
    rows = await fetch_tokens(db_conn, user_id)

    assert response.status_code == 401
    assert len(rows) == 1
    assert rows[0]["last_used_at"] is None
    assert rows[0]["replaced_by_id"] is None
    assert rows[0]["revoked_at"] is None


async def test_ca12_reuse_within_grace_keeps_the_family_alive(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: reusar el token viejo dentro de la ventana no revoca la familia."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)
    rotated = await refresh(auth_client, session["refresh_token"])
    successor = rotated.json()["refresh_token"]

    reuse = await refresh(auth_client, session["refresh_token"])
    still_valid = await refresh(auth_client, successor)
    rows = await fetch_tokens(db_conn, user_id)

    assert reuse.status_code == 401
    assert still_valid.status_code == 200, still_valid.text
    assert all(row["revoked_at"] is None for row in rows)


async def test_ca13_reuse_outside_grace_revokes_the_family(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: reusar fuera de la ventana revoca toda la familia."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)
    rotated = await refresh(auth_client, session["refresh_token"])
    successor = rotated.json()["refresh_token"]

    # El uso del predecesor queda 11 s atras: fuera de la ventana de gracia.
    await db_conn.execute(
        text(
            "UPDATE refresh_tokens SET last_used_at = :past "
            "WHERE user_id = :uid AND replaced_by_id IS NOT NULL"
        ),
        {"past": datetime.now(UTC) - timedelta(seconds=11), "uid": user_id},
    )

    reuse = await refresh(auth_client, session["refresh_token"])
    successor_after = await refresh(auth_client, successor)
    rows = await fetch_tokens(db_conn, user_id)

    assert reuse.status_code == 401
    assert successor_after.status_code == 401
    assert all(row["revoked_reason"] == "reuse_detected" for row in rows)
    assert all(row["revoked_at"] is not None for row in rows)


async def test_ca14_concurrent_refresh_produces_a_single_successor() -> None:
    """CA-14 (D-5): dos refrescos simultaneos del mismo token dejan un solo sucesor.

    Cada refresco corre en su propia conexion (motor propio con `NullPool`): el
    `SELECT ... FOR UPDATE` de la rotacion solo se ejerce entre transacciones
    reales, asi que dos peticiones simultaneas deben serializarse en la base y
    no en el event loop. Sin el bloqueo ambas rotarian y dejarian dos sucesores.
    """
    plain_token = generate_refresh_token()
    username = f"user_{unique_suffix()}"
    engines = [
        create_async_engine(resolve_test_database_url(), poolclass=NullPool) for _ in range(2)
    ]
    setup_engine = engines[0]
    barrier = asyncio.Barrier(len(engines))
    user_id: int | None = None

    try:
        async with setup_engine.begin() as conn:
            user_id = await create_auth_user(conn, username=username)
            await conn.execute(
                text(
                    """
                    INSERT INTO refresh_tokens
                        (user_id, token_hash, family_id, expires_at, created_by_ip, user_agent)
                    VALUES (:user_id, :token_hash, :family_id, :expires_at, '127.0.0.1', 'pytest')
                    """
                ),
                {
                    "user_id": user_id,
                    "token_hash": hash_refresh_token(plain_token),
                    "family_id": uuid.uuid4(),
                    "expires_at": datetime.now(UTC) + timedelta(days=1),
                },
            )

        async def rotate(engine: AsyncEngine) -> Any:
            async with AsyncSession(bind=engine, expire_on_commit=False) as rotate_session:
                await barrier.wait()
                try:
                    return await auth_service.refresh(rotate_session, refresh_token=plain_token)
                except UnauthorizedError as exc:
                    return exc

        results = await asyncio.gather(*(rotate(engine) for engine in engines))

        async with setup_engine.connect() as conn:
            rows = await fetch_tokens(conn, user_id)

        successes = [item for item in results if isinstance(item, RefreshResult)]
        rejections = [item for item in results if isinstance(item, UnauthorizedError)]

        assert len(successes) == 1
        assert len(rejections) == 1
        assert rejections[0].code == "invalid_token"
        assert len(rows) == 2
        assert sum(1 for row in rows if row["replaced_by_id"] is not None) == 1
        assert rows[0]["replaced_by_id"] == rows[1]["id"]
    finally:
        if user_id is not None:
            async with setup_engine.begin() as conn:
                # `replaced_by_id` es RESTRICT: se rompe el eslabon antes de borrar.
                await conn.execute(
                    text("UPDATE refresh_tokens SET replaced_by_id = NULL WHERE user_id = :uid"),
                    {"uid": user_id},
                )
                await conn.execute(
                    text("DELETE FROM refresh_tokens WHERE user_id = :uid"),
                    {"uid": user_id},
                )
                await conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        for engine in engines:
            await engine.dispose()


async def test_ca15_inactive_user_cannot_refresh_and_family_dies(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-15: un usuario desactivado no renueva y su familia queda revocada."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)

    await db_conn.execute(
        text("UPDATE users SET is_active = false WHERE id = :id"), {"id": user_id}
    )

    response = await refresh(auth_client, session["refresh_token"])
    rows = await fetch_tokens(db_conn, user_id)

    assert response.status_code == 401
    assert rows[0]["revoked_reason"] == "user_deactivated"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"refresh_token": ""},
        {"refresh_token": "x" * 513},
    ],
)
async def test_ca16_invalid_payload_is_422(
    auth_client: AsyncClient, payload: dict[str, str]
) -> None:
    """CA-16: token ausente, vacio o demasiado largo => 422 con el envelope."""
    response = await auth_client.post(REFRESH_PATH, json=payload)

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"detail", "code", "errors"}


async def test_ca17_refresh_does_not_need_authorization(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-17: `/auth/refresh` funciona sin cabecera Bearer."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)

    response = await auth_client.post(
        REFRESH_PATH, json={"refresh_token": session["refresh_token"]}
    )

    assert response.status_code == 200


# --- CA-18…CA-21: logout ------------------------------------------------------


async def test_ca18_logout_revokes_the_token(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-18: logout devuelve 204 y el token ya no renueva."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)

    logout = await auth_client.post(LOGOUT_PATH, json={"refresh_token": session["refresh_token"]})
    rows = await fetch_tokens(db_conn, user_id)
    after = await refresh(auth_client, session["refresh_token"])

    assert logout.status_code == 204
    assert rows[0]["revoked_reason"] == "logout"
    assert rows[0]["revoked_at"] is not None
    assert after.status_code == 401


async def test_ca19_logout_is_idempotent_without_oracle(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-19: repetir el logout y usar un token inventado responden 204."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)

    first = await auth_client.post(LOGOUT_PATH, json={"refresh_token": session["refresh_token"]})
    second = await auth_client.post(LOGOUT_PATH, json={"refresh_token": session["refresh_token"]})
    unknown = await auth_client.post(LOGOUT_PATH, json={"refresh_token": "no-existe"})

    assert first.status_code == second.status_code == unknown.status_code == 204
    assert second.content == b""
    assert unknown.content == b""


async def test_ca19b_logout_revokes_a_rotated_token(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Regla 23: un token ya rotado tambien queda revocado al cerrar esa sesion."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)
    assert (await refresh(auth_client, session["refresh_token"])).status_code == 200
    body = {"refresh_token": session["refresh_token"]}
    assert (await auth_client.post(LOGOUT_PATH, json=body)).status_code == 204
    predecessor = (await fetch_tokens(db_conn, user_id))[0]
    assert predecessor["revoked_reason"] == "logout"
    assert predecessor["revoked_at"] is not None


async def test_ca20_logout_does_not_affect_other_sessions(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-20: cerrar una sesion deja vivas las demas."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)
    first = await login(auth_client, username)
    second = await login(auth_client, username)

    await auth_client.post(LOGOUT_PATH, json={"refresh_token": first["refresh_token"]})
    survivor = await refresh(auth_client, second["refresh_token"])

    assert survivor.status_code == 200, survivor.text


async def test_ca21_logout_does_not_need_authorization(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-21: `/auth/logout` funciona con el access token ya expirado."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)

    response = await auth_client.post(LOGOUT_PATH, json={"refresh_token": session["refresh_token"]})

    assert response.status_code == 204


# --- CA-22…CA-25: cascada ------------------------------------------------------


async def test_ca22_change_password_revokes_every_session(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-22: cambiar la contrasena revoca todas las sesiones del usuario."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    session = await login(auth_client, username)
    other = await login(auth_client, username)

    response = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=auth_headers(session["access_token"]),
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": "NuevaClave456",
            "confirm_password": "NuevaClave456",
        },
    )
    rows = await fetch_tokens(db_conn, user_id)
    after = await refresh(auth_client, other["refresh_token"])

    assert response.status_code == 200, response.text
    assert len(rows) == 2
    assert all(row["revoked_reason"] == "password_change" for row in rows)
    assert after.status_code == 401


async def test_ca23_admin_reset_revokes_every_session(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-23: el reset de admin revoca todas las sesiones del usuario."""
    target_name = f"user_{unique_suffix()}"
    target_id = await create_auth_user(db_conn, username=target_name, role="MEDICO")
    session = await login(auth_client, target_name)

    admin_name = f"admin_{unique_suffix()}"
    await create_auth_user(db_conn, username=admin_name, role="ADMIN")
    admin_session = await login(auth_client, admin_name)

    response = await auth_client.post(
        f"/api/v1/users/{target_id}/reset-password",
        headers=auth_headers(admin_session["access_token"]),
        json={"new_password": "Reseteada789"},
    )
    rows = await fetch_tokens(db_conn, target_id)
    after = await refresh(auth_client, session["refresh_token"])

    assert response.status_code == 200, response.text
    assert all(row["revoked_reason"] == "password_reset" for row in rows)
    assert after.status_code == 401


async def test_ca24_deactivation_revokes_sessions_and_blocks_access(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-24: desactivar revoca sesiones y corta el access token vigente."""
    target_name = f"user_{unique_suffix()}"
    target_id = await create_auth_user(db_conn, username=target_name, role="MEDICO")
    session = await login(auth_client, target_name)

    admin_name = f"admin_{unique_suffix()}"
    await create_auth_user(db_conn, username=admin_name, role="ADMIN")
    admin_session = await login(auth_client, admin_name)

    response = await auth_client.delete(
        f"/api/v1/users/{target_id}", headers=auth_headers(admin_session["access_token"])
    )
    rows = await fetch_tokens(db_conn, target_id)
    refresh_after = await refresh(auth_client, session["refresh_token"])
    me_after = await auth_client.get(ME_PATH, headers=auth_headers(session["access_token"]))

    assert response.status_code == 204
    assert all(row["revoked_reason"] == "user_deactivated" for row in rows)
    assert refresh_after.status_code == 401
    assert me_after.status_code == 403


async def test_ca24b_put_deactivation_revokes_every_session(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Regla 27: desactivar por `PUT /users/{id}` tambien revoca las sesiones."""
    tag = unique_suffix()
    target_name = f"user_{tag}"
    target_email = f"{tag}@example.test"
    target_id = await create_auth_user(
        db_conn, username=target_name, email=target_email, role="MEDICO"
    )
    await login(auth_client, target_name)
    await login(auth_client, target_name)
    _, admin_headers = await admin_actor(db_conn)
    medico_role_id = await get_role_id(db_conn, "MEDICO")

    response = await auth_client.put(
        user_path(target_id),
        headers=admin_headers,
        json=put_body(
            username=target_name,
            email=target_email,
            role_id=medico_role_id,
            doctor_id=None,
            is_active=False,
        ),
    )
    rows = await fetch_tokens(db_conn, target_id)

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False
    assert len(rows) == 2
    assert [row["revoked_reason"] for row in rows] == [
        "user_deactivated",
        "user_deactivated",
    ]
    assert all(row["revoked_at"] is not None for row in rows)


async def test_ca24c_put_update_without_deactivation_keeps_sessions_alive(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Regla 27: un PUT que mantiene la cuenta activa no revoca nada."""
    tag = unique_suffix()
    target_name = f"user_{tag}"
    target_email = f"{tag}@example.test"
    target_id = await create_auth_user(
        db_conn, username=target_name, email=target_email, role="MEDICO"
    )
    await login(auth_client, target_name)
    _, admin_headers = await admin_actor(db_conn)
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    before = await fetch_tokens(db_conn, target_id)

    response = await auth_client.put(
        user_path(target_id),
        headers=admin_headers,
        json=put_body(
            username=target_name,
            email=target_email,
            role_id=medico_role_id,
            doctor_id=None,
            is_active=True,
        ),
    )
    after = await fetch_tokens(db_conn, target_id)

    assert response.status_code == 200, response.text
    assert [row["revoked_at"] for row in after] == [row["revoked_at"] for row in before]
    assert all(row["revoked_reason"] is None for row in after)


async def test_ca25_deactivating_an_inactive_user_is_idempotent(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-25: desactivar un usuario ya inactivo no falla ni revoca de nuevo."""
    target_name = f"user_{unique_suffix()}"
    target_id = await create_auth_user(db_conn, username=target_name, role="MEDICO")
    await login(auth_client, target_name)
    await db_conn.execute(
        text("UPDATE users SET is_active = false WHERE id = :id"), {"id": target_id}
    )
    before = await fetch_tokens(db_conn, target_id)

    admin_name = f"admin_{unique_suffix()}"
    await create_auth_user(db_conn, username=admin_name, role="ADMIN")
    admin_session = await login(auth_client, admin_name)

    response = await auth_client.delete(
        f"/api/v1/users/{target_id}", headers=auth_headers(admin_session["access_token"])
    )
    after = await fetch_tokens(db_conn, target_id)

    assert response.status_code == 204
    assert [row["revoked_at"] for row in after] == [row["revoked_at"] for row in before]


# --- CA-26…CA-30: regresion y contrato -----------------------------------------


async def test_ca26_failed_login_emits_no_refresh_token(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-26: un login fallido no crea filas de refresh token."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)
    before = await total_tokens(db_conn)

    response = await auth_client.post(
        LOGIN_PATH, json={"username": username, "password": "clave-equivocada"}
    )

    assert response.status_code == 401
    assert set(response.json()) == {"detail", "code", "errors"}
    assert await total_tokens(db_conn) == before


def test_ca27_openapi_exposes_the_new_routes() -> None:
    """CA-27: OpenAPI registra `/auth/refresh` y `/auth/logout` bajo el tag auth."""
    from app.main import create_app

    schema = create_app().openapi()
    paths = schema["paths"]

    assert "post" in paths[REFRESH_PATH]
    assert "post" in paths[LOGOUT_PATH]
    assert paths[REFRESH_PATH]["post"]["tags"] == ["auth"]
    assert paths[LOGOUT_PATH]["post"]["tags"] == ["auth"]
    assert "401" in paths[REFRESH_PATH]["post"]["responses"]
    assert "204" in paths[LOGOUT_PATH]["post"]["responses"]


async def test_ca28_migration_created_the_table(db_conn: AsyncConnection) -> None:
    """CA-28: la tabla existe con las columnas e indices de la spec."""
    columns = await db_conn.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'refresh_tokens'
            """
        )
    )
    names = {row[0] for row in columns}

    assert names == {
        "id",
        "user_id",
        "token_hash",
        "family_id",
        "expires_at",
        "last_used_at",
        "revoked_at",
        "revoked_reason",
        "replaced_by_id",
        "created_by_ip",
        "user_agent",
        "created_at",
    }

    indexes = await db_conn.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'refresh_tokens'")
    )
    index_names = {row[0] for row in indexes}
    assert "ix_refresh_tokens_user_id" in index_names
    assert "ix_refresh_tokens_family_id" in index_names
    assert "uq_refresh_tokens_token_hash" in index_names


def test_ca29_env_example_documents_the_refresh_setting() -> None:
    """CA-29: `.env.example` documenta `REFRESH_TOKEN_EXPIRE_DAYS` y el TTL de 15 min."""
    from pathlib import Path

    content = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")

    assert "JWT_EXPIRE_MINUTES=15" in content
    assert "REFRESH_TOKEN_EXPIRE_DAYS=30" in content


def test_ca30_default_access_ttl_is_fifteen_minutes() -> None:
    """CA-30: el default de vigencia del access token es de 15 minutos."""
    from app.core.config import Settings

    settings = Settings(_env_file=None)

    assert settings.jwt_expire_minutes == 15
    assert settings.refresh_token_expire_days == 30
