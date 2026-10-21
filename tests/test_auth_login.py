"""Tests de integracion del login (FEAT-002, CA-10 a CA-17)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.services import auth_service
from tests.auth_support import (
    DEFAULT_PASSWORD,
    LOGIN_PATH,
    auth_headers,
    create_auth_user,
    fetch_user,
    token_payload,
)
from tests.db_support import create_doctor, unique_suffix


async def post_login(client: AsyncClient, username: str, password: str) -> Response:
    return await client.post(LOGIN_PATH, json={"username": username, "password": password})


async def test_ca10_admin_login_response_shape(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: login ADMIN con la forma exacta del contrato."""
    username = f"admin_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)

    response = await post_login(auth_client, username, DEFAULT_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "user"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 1800
    assert body["access_token"].count(".") == 2
    assert set(body["user"]) == {"id", "username", "email", "role", "doctor_id"}
    assert body["user"]["id"] == user_id
    assert body["user"]["username"] == username
    assert body["user"]["role"] == "ADMIN"
    assert body["user"]["doctor_id"] is None
    assert "is_active" not in body["user"]

    claims = token_payload(body["access_token"])
    assert claims["sub"] == str(user_id)


async def test_unknown_user_runs_dummy_hash_and_keeps_generic_401(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Minor ciclo 1: el hash ficticio no altera el 401 generico ni su cuerpo."""
    calls: list[str] = []
    original = auth_service.verify_password

    def spy(password: str, password_hash: str) -> bool:
        calls.append(password_hash)
        return original(password, password_hash)

    monkeypatch.setattr(auth_service, "verify_password", spy)

    response = await post_login(auth_client, f"no_existe_{unique_suffix()}", "Cualquiera123")

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "invalid_credentials"
    assert body["detail"] == "Usuario o contraseña incorrectos"
    assert body["errors"] == []
    assert len(calls) == 1
    assert calls[0].startswith("$argon2id$")


async def test_ca11_medico_login_exposes_doctor_id(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11: login MEDICO devuelve el `doctor_id` de la base."""
    doctor_id = await create_doctor(db_conn)
    username = f"medico_{unique_suffix()}"
    await create_auth_user(db_conn, username=username, role="MEDICO", doctor_id=doctor_id)

    response = await post_login(auth_client, username, DEFAULT_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "MEDICO"
    assert body["user"]["doctor_id"] == doctor_id


async def test_ca12_username_is_case_insensitive_and_stripped(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: CITEXT para mayusculas y `strip` antes de buscar."""
    username = f"admin_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)

    mixed = await post_login(auth_client, username.upper(), DEFAULT_PASSWORD)
    assert mixed.status_code == 200
    assert mixed.json()["user"]["username"] == username

    padded = await post_login(auth_client, f"  {username}  ", DEFAULT_PASSWORD)
    assert padded.status_code == 200
    assert padded.json()["user"]["username"] == username


async def test_ca13_invalid_credentials_are_indistinguishable(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: 401 identico byte a byte para clave mala y usuario inexistente."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)

    wrong_password = await post_login(auth_client, username, "ClaveEquivocada999")
    unknown_user = await post_login(auth_client, f"no_existe_{unique_suffix()}", DEFAULT_PASSWORD)

    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    assert wrong_password.content == unknown_user.content

    body = wrong_password.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "invalid_credentials"
    assert body["detail"] == "Usuario o contraseña incorrectos"
    assert body["errors"] == []
    assert "details" not in body
    assert wrong_password.headers["www-authenticate"] == "Bearer"


async def test_ca14_inactive_user_requires_correct_password(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-14: 403 solo con contraseña correcta; 401 con incorrecta."""
    username = f"inactivo_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username, is_active=False)

    wrong = await post_login(auth_client, username, "ClaveEquivocada999")
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "invalid_credentials"

    correct = await post_login(auth_client, username, DEFAULT_PASSWORD)
    assert correct.status_code == 403
    body = correct.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "inactive_user"
    assert body["detail"] == "El usuario está inactivo"
    assert body["errors"] == []

    assert (await fetch_user(db_conn, user_id))["last_login_at"] is None


async def test_ca15_missing_fields_and_length_limits(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-15: 422 normalizado con `field` por cada campo invalido."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)

    missing_password = await auth_client.post(LOGIN_PATH, json={"username": username})
    assert missing_password.status_code == 422
    body = missing_password.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "validation_error"
    assert body["detail"] == "Datos inválidos"
    assert any(item["field"] == "password" for item in body["errors"])
    assert all(set(item) == {"field", "message"} for item in body["errors"])

    empty = await auth_client.post(LOGIN_PATH, json={})
    assert empty.status_code == 422
    assert {item["field"] for item in empty.json()["errors"]} == {"password", "username"}

    long_username = await post_login(auth_client, "u" * 51, DEFAULT_PASSWORD)
    assert long_username.status_code == 422
    assert any(item["field"] == "username" for item in long_username.json()["errors"])


async def test_ca16_last_login_at_only_changes_on_success(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-16: `last_login_at` solo se escribe en un login exitoso."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)

    assert (await fetch_user(db_conn, user_id))["last_login_at"] is None

    login = await post_login(auth_client, username, DEFAULT_PASSWORD)
    assert login.status_code == 200
    after_login = await fetch_user(db_conn, user_id)
    assert after_login["last_login_at"] is not None

    result = await db_conn.execute(
        text("SELECT extract(epoch FROM (now() - last_login_at)) AS age FROM users WHERE id = :id"),
        {"id": user_id},
    )
    assert abs(float(result.scalar_one())) < 60

    snapshot = after_login["last_login_at"]
    token = login.json()["access_token"]

    failed = await post_login(auth_client, username, "ClaveEquivocada999")
    assert failed.status_code == 401
    assert (await fetch_user(db_conn, user_id))["last_login_at"] == snapshot

    me = await auth_client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert (await fetch_user(db_conn, user_id))["last_login_at"] == snapshot

    changed = await auth_client.post(
        "/api/v1/auth/change-password",
        headers=auth_headers(token),
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": "ClaveNueva456",
            "confirm_password": "ClaveNueva456",
        },
    )
    assert changed.status_code == 200
    assert (await fetch_user(db_conn, user_id))["last_login_at"] == snapshot


async def test_corrupt_hash_is_401_not_500(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Regla 7: un hash corrupto se trata como contrasena incorrecta."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username, password_hash="$2b$12$hash-invalido")

    response = await post_login(auth_client, username, DEFAULT_PASSWORD)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


async def test_ca17_jwt_expire_minutes_is_honoured_end_to_end(
    auth_client: AsyncClient, db_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CA-17: `JWT_EXPIRE_MINUTES` configurable de punta a punta."""
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "45")
    get_settings.cache_clear()

    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)

    response = await post_login(auth_client, username, DEFAULT_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == 2700
    claims = token_payload(body["access_token"])
    assert claims["exp"] - claims["iat"] == 2700
