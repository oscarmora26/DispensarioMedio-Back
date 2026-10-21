"""Tests de integracion de sesion y autorizacion (FEAT-002, CA-18 a CA-22)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.api.deps import require_role
from app.core.exceptions import ForbiddenError
from app.repositories import user_repository
from tests.auth_support import (
    DEFAULT_PASSWORD,
    LOGIN_PATH,
    ME_PATH,
    auth_headers,
    create_auth_user,
    now_epoch,
    signed_token,
    token_for,
)
from tests.db_support import create_doctor, unique_suffix


async def login_token(client: AsyncClient, username: str) -> str:
    response = await client.post(
        LOGIN_PATH, json={"username": username, "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


async def test_ca18_me_returns_medico_identity(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-18: `/me` devuelve la identidad completa del MEDICO."""
    doctor_id = await create_doctor(db_conn)
    username = f"medico_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username, role="MEDICO", doctor_id=doctor_id)

    response = await auth_client.get(ME_PATH, headers=auth_headers(token_for(user_id)))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "username", "email", "role", "doctor_id", "is_active"}
    assert body["id"] == user_id
    assert body["username"] == username
    assert body["role"] == "MEDICO"
    assert body["doctor_id"] == doctor_id
    assert body["is_active"] is True


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"}])
async def test_ca19_missing_or_foreign_scheme_is_not_authenticated(
    auth_client: AsyncClient, headers: dict[str, str]
) -> None:
    """CA-19: sin Bearer => 401 `not_authenticated` con `WWW-Authenticate`."""
    response = await auth_client.get(ME_PATH, headers=headers)

    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "not_authenticated"
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_ca20_invalid_tokens_are_rejected(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-20: basura, firma ajena, expirado, sin `exp`, `sub` no numerico."""
    user_id = await create_auth_user(db_conn, username=f"user_{unique_suffix()}")
    valid = token_for(user_id)
    secret = "s" * 32

    tampered = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    without_exp = signed_token({"sub": str(user_id), "role": "ADMIN", "iat": now_epoch()})
    non_numeric_sub = signed_token(
        {"sub": "abc", "role": "ADMIN", "iat": now_epoch(), "exp": now_epoch() + 600}
    )

    tokens = {
        "garbage": "abc.def.ghi",
        "foreign_secret": token_for(user_id, secret=secret),
        "expired": token_for(user_id, expires_minutes=-1),
        "missing_exp": without_exp,
        "non_numeric_sub": non_numeric_sub,
        "tampered": tampered,
    }

    for name, token in tokens.items():
        response = await auth_client.get(ME_PATH, headers=auth_headers(token))
        assert response.status_code == 401, name
        body = response.json()
        assert body["code"] == "invalid_token", name
        assert body["errors"] == [], name
        assert response.headers["www-authenticate"] == "Bearer", name


async def test_ca20_deleted_user_token_is_invalid(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-20: token valido de un usuario borrado dentro de la transaccion."""
    user_id = await create_auth_user(db_conn, username=f"user_{unique_suffix()}")
    token = token_for(user_id)

    await db_conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})

    response = await auth_client.get(ME_PATH, headers=auth_headers(token))

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


async def test_ca21_deactivation_applies_immediately(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """CA-21: desactivar al usuario invalida su token vigente en la siguiente request."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)
    token = await login_token(auth_client, username)

    await db_conn.execute(
        text("UPDATE users SET is_active = false WHERE id = :id"), {"id": user_id}
    )
    db_session.expire_all()

    deactivated = await auth_client.get(ME_PATH, headers=auth_headers(token))
    assert deactivated.status_code == 403
    assert deactivated.json()["code"] == "inactive_user"

    await db_conn.execute(text("UPDATE users SET is_active = true WHERE id = :id"), {"id": user_id})
    db_session.expire_all()

    reactivated = await auth_client.get(ME_PATH, headers=auth_headers(token))
    assert reactivated.status_code == 200
    assert reactivated.json()["id"] == user_id


async def test_ca22_role_claim_is_not_trusted(
    auth_client: AsyncClient, db_conn: AsyncConnection, db_session: AsyncSession
) -> None:
    """CA-22: el rol efectivo sale de la base, no del claim."""
    doctor_id = await create_doctor(db_conn)
    medico_id = await create_auth_user(
        db_conn, username=f"medico_{unique_suffix()}", role="MEDICO", doctor_id=doctor_id
    )
    admin_id = await create_auth_user(db_conn, username=f"admin_{unique_suffix()}", role="ADMIN")

    forged = token_for(medico_id, role="ADMIN", doctor_id=None)
    me = await auth_client.get(ME_PATH, headers=auth_headers(forged))
    assert me.status_code == 200
    assert me.json()["role"] == "MEDICO"

    medico = await user_repository.get_user_by_id(db_session, medico_id)
    admin = await user_repository.get_user_by_id(db_session, admin_id)
    assert medico is not None and admin is not None

    dependency = require_role("ADMIN")
    with pytest.raises(ForbiddenError) as forbidden:
        await dependency(medico)
    assert forbidden.value.status_code == 403
    assert forbidden.value.code == "forbidden"

    assert await dependency(admin) is admin

    with pytest.raises(ValueError):
        require_role()
    with pytest.raises(ValueError):
        require_role("SUPERVISOR")
