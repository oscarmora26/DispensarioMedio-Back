"""Tests de integracion del login con email (FEAT-005, CA-1 a CA-11 y CA-18)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.rate_limit import get_login_rate_limiter
from tests.auth_support import (
    DEFAULT_PASSWORD,
    LOGIN_PATH,
    create_auth_user,
    fetch_user,
    token_payload,
)
from tests.db_support import unique_suffix

INVALID_EMAIL_MESSAGE = "El correo electrónico no tiene un formato válido"
EMPTY_EMAIL_MESSAGE = "El correo electrónico no puede estar vacío"
MISSING_IDENTIFIER_MESSAGE = "Indique 'username' o 'email' (exactamente uno)"


async def post_login_email(client: AsyncClient, email: str, password: str) -> Response:
    return await client.post(LOGIN_PATH, json={"email": email, "password": password})


async def post_login_username(client: AsyncClient, username: str, password: str) -> Response:
    return await client.post(LOGIN_PATH, json={"username": username, "password": password})


async def test_ca1_login_by_email_returns_the_exact_login_response(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: login por email devuelve la misma forma exacta que FEAT-002."""
    suffix = unique_suffix()
    username = f"email_user_{suffix}"
    email = f"email_{suffix}@example.test"
    user_id = await create_auth_user(db_conn, username=username, email=email)

    response = await post_login_email(auth_client, email, DEFAULT_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "user"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 1800
    assert body["access_token"].count(".") == 2
    assert set(body["user"]) == {"id", "username", "email", "role", "doctor_id"}
    assert body["user"] == {
        "id": user_id,
        "username": username,
        "email": email,
        "role": "ADMIN",
        "doctor_id": None,
    }


async def test_ca2_email_is_stripped_and_case_insensitive_but_keeps_stored_value(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: `strip` + CITEXT; la capitalizacion almacenada no se altera."""
    suffix = unique_suffix()
    stored = f"Mixed_{suffix}@Example.Test"
    username = f"mixed_{suffix}"
    await create_auth_user(db_conn, username=username, email=stored)

    padded_lower = await post_login_email(
        auth_client, f"  mixed_{suffix}@example.test  ", DEFAULT_PASSWORD
    )
    assert padded_lower.status_code == 200
    assert padded_lower.json()["user"]["email"] == stored

    mixed_case = await post_login_email(
        auth_client, f"MIXED_{suffix}@EXAMPLE.TEST", DEFAULT_PASSWORD
    )
    assert mixed_case.status_code == 200
    assert mixed_case.json()["user"]["email"] == stored


async def test_ca3_exactly_one_identifier_is_required(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: ni uno ni dos identificadores ⇒ 422 con `field="body"`."""
    suffix = unique_suffix()
    username = f"identifier_{suffix}"
    email = f"identifier_{suffix}@example.test"
    user_id = await create_auth_user(db_conn, username=username, email=email)
    limiter = get_login_rate_limiter()

    missing = await auth_client.post(LOGIN_PATH, json={"password": DEFAULT_PASSWORD})

    assert missing.status_code == 422
    body = missing.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "validation_error"
    assert body["detail"] == "Datos inválidos"
    assert body["errors"] == [{"field": "body", "message": MISSING_IDENTIFIER_MESSAGE}]

    both = await auth_client.post(
        LOGIN_PATH,
        json={"email": email, "username": username, "password": DEFAULT_PASSWORD},
    )

    assert both.status_code == 422
    both_body = both.json()
    assert both_body["code"] == "validation_error"
    assert both_body["detail"] == "Datos inválidos"
    assert both_body["errors"] == [{"field": "body", "message": MISSING_IDENTIFIER_MESSAGE}]

    assert (await fetch_user(db_conn, user_id))["last_login_at"] is None
    assert len(limiter) == 0


@pytest.mark.parametrize(
    ("invalid_email", "expected_message"),
    [
        ("sin-arroba", INVALID_EMAIL_MESSAGE),
        ("a@b", INVALID_EMAIL_MESSAGE),
        ("a b@c.d", INVALID_EMAIL_MESSAGE),
        ("a@.c", INVALID_EMAIL_MESSAGE),
        ("", EMPTY_EMAIL_MESSAGE),
        ("   ", EMPTY_EMAIL_MESSAGE),
    ],
)
async def test_ca4_invalid_email_returns_422_on_the_email_field(
    auth_client: AsyncClient, invalid_email: str, expected_message: str
) -> None:
    """CA-4: formato/vacio de email ⇒ 422 `field="email"` con mensaje en espanol."""
    response = await post_login_email(auth_client, invalid_email, DEFAULT_PASSWORD)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["detail"] == "Datos inválidos"
    assert body["errors"] == [{"field": "email", "message": expected_message}]


async def test_ca4_email_longer_than_255_returns_422_on_the_email_field(
    auth_client: AsyncClient,
) -> None:
    """CA-4: un email de 256 caracteres ⇒ 422 `field="email"` (mensaje de Pydantic)."""
    too_long = "a" * 251 + "@b.cd"
    assert len(too_long) == 256

    response = await post_login_email(auth_client, too_long, DEFAULT_PASSWORD)

    assert response.status_code == 422
    errors = response.json()["errors"]
    assert len(errors) == 1
    assert errors[0]["field"] == "email"
    assert "255" in errors[0]["message"]


async def test_ca4_well_formed_email_with_wrong_password_is_401(
    auth_client: AsyncClient,
) -> None:
    """CA-4: la forma valida no se rechaza en validacion; falla en credenciales."""
    response = await post_login_email(
        auth_client, f"nadie_{unique_suffix()}@b.c", "ClaveEquivocada999"
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


async def test_ca5_unknown_email_bad_password_and_unknown_username_are_identical(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: los tres 401 son byte a byte identicos y llevan `WWW-Authenticate`."""
    suffix = unique_suffix()
    email = f"known_{suffix}@example.test"
    await create_auth_user(db_conn, username=f"known_{suffix}", email=email)

    bad_password = await post_login_email(auth_client, email, "ClaveEquivocada999")
    unknown_email = await post_login_email(
        auth_client, f"missing_{suffix}@example.test", DEFAULT_PASSWORD
    )
    unknown_username = await post_login_username(auth_client, f"missing_{suffix}", DEFAULT_PASSWORD)

    for response in (bad_password, unknown_email, unknown_username):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        body = response.json()
        assert set(body) == {"detail", "code", "errors"}
        assert "details" not in body
        assert body["code"] == "invalid_credentials"
        assert body["detail"] == "Usuario o contraseña incorrectos"
        assert body["errors"] == []

    assert bad_password.content == unknown_email.content == unknown_username.content


async def test_ca6_inactive_user_by_email_requires_the_correct_password(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: inactivo + contrasena correcta ⇒ 403; incorrecta ⇒ 401."""
    suffix = unique_suffix()
    email = f"inactive_{suffix}@example.test"
    user_id = await create_auth_user(
        db_conn, username=f"inactive_{suffix}", email=email, is_active=False
    )

    wrong = await post_login_email(auth_client, email, "ClaveEquivocada999")
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "invalid_credentials"

    correct = await post_login_email(auth_client, email, DEFAULT_PASSWORD)
    assert correct.status_code == 403
    body = correct.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "inactive_user"
    assert body["detail"] == "El usuario está inactivo"
    assert body["errors"] == []

    assert (await fetch_user(db_conn, user_id))["last_login_at"] is None


async def test_ca7_last_login_at_is_updated_only_on_a_successful_email_login(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: exito por email escribe `last_login_at`; 401 y 403 no lo tocan."""
    suffix = unique_suffix()
    email = f"success_{suffix}@example.test"
    user_id = await create_auth_user(db_conn, username=f"success_{suffix}", email=email)
    assert (await fetch_user(db_conn, user_id))["last_login_at"] is None

    login = await post_login_email(auth_client, email, DEFAULT_PASSWORD)
    assert login.status_code == 200

    after_login = await fetch_user(db_conn, user_id)
    assert after_login["last_login_at"] is not None
    age = await db_conn.execute(
        text("SELECT extract(epoch FROM (now() - last_login_at)) FROM users WHERE id = :id"),
        {"id": user_id},
    )
    assert abs(float(age.scalar_one())) < 60
    snapshot = after_login["last_login_at"]

    failed = await post_login_email(auth_client, email, "ClaveEquivocada999")
    assert failed.status_code == 401
    assert (await fetch_user(db_conn, user_id))["last_login_at"] == snapshot

    inactive_email = f"inactive_success_{suffix}@example.test"
    inactive_id = await create_auth_user(
        db_conn,
        username=f"inactive_success_{suffix}",
        email=inactive_email,
        is_active=False,
    )
    forbidden = await post_login_email(auth_client, inactive_email, DEFAULT_PASSWORD)
    assert forbidden.status_code == 403
    assert (await fetch_user(db_conn, inactive_id))["last_login_at"] is None


async def test_ca8_response_and_claims_are_identical_for_both_identifiers(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-8: mismas claves, claims y `expires_in` por email y por username.

    Dos ADMIN equivalentes (mismo rol y `doctor_id=None`, porque `uq_users_doctor`
    impide compartir un medico entre cuentas).
    """
    suffix = unique_suffix()

    username_user = f"by_username_{suffix}"
    username_id = await create_auth_user(
        db_conn,
        username=username_user,
        email=f"by_username_{suffix}@example.test",
        role="ADMIN",
    )
    email_user = f"by_email_{suffix}"
    email = f"by_email_{suffix}@example.test"
    email_id = await create_auth_user(
        db_conn,
        username=email_user,
        email=email,
        role="ADMIN",
    )

    by_username = await post_login_username(auth_client, username_user, DEFAULT_PASSWORD)
    by_email = await post_login_email(auth_client, email, DEFAULT_PASSWORD)

    assert by_username.status_code == 200
    assert by_email.status_code == 200

    username_body = by_username.json()
    email_body = by_email.json()
    assert set(username_body) == set(email_body)
    assert set(username_body["user"]) == set(email_body["user"])
    assert username_body["expires_in"] == email_body["expires_in"] == 1800

    for body, user_id in ((username_body, username_id), (email_body, email_id)):
        claims = token_payload(body["access_token"])
        assert set(claims) == {"sub", "role", "doctor_id", "iat", "exp"}
        assert claims["sub"] == str(user_id)
        assert claims["role"] == "ADMIN"
        assert claims["doctor_id"] is None
        assert claims["exp"] - claims["iat"] == 1800


async def test_ca9_username_login_remains_unchanged(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-9: username con mayusculas/espacios sigue autenticando; limites intactos."""
    suffix = unique_suffix()
    username = f"compat_{suffix}"
    await create_auth_user(db_conn, username=username, email=f"compat_{suffix}@example.test")

    mixed = await post_login_username(auth_client, f"  {username.upper()}  ", DEFAULT_PASSWORD)
    assert mixed.status_code == 200
    assert mixed.json()["user"]["username"] == username

    missing_password = await auth_client.post(LOGIN_PATH, json={"username": username})
    assert missing_password.status_code == 422
    assert [item["field"] for item in missing_password.json()["errors"]] == ["password"]

    long_username = await post_login_username(auth_client, "u" * 51, DEFAULT_PASSWORD)
    assert long_username.status_code == 422
    assert [item["field"] for item in long_username.json()["errors"]] == ["username"]


async def test_ca10_email_in_the_username_field_does_not_authenticate_by_email(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: sin fallback entre columnas (D-5); solo vale si es el username real."""
    suffix = unique_suffix()
    email = f"no_fallback_{suffix}@example.test"
    await create_auth_user(db_conn, username=f"no_fallback_{suffix}", email=email)

    wrong_field = await post_login_username(auth_client, email, DEFAULT_PASSWORD)
    assert wrong_field.status_code == 401
    assert wrong_field.json()["code"] == "invalid_credentials"

    email_like_username = f"email_like_{suffix}@example.test"
    user_id = await create_auth_user(
        db_conn,
        username=email_like_username,
        email=f"other_{suffix}@example.test",
    )

    as_username = await post_login_username(auth_client, email_like_username, DEFAULT_PASSWORD)
    assert as_username.status_code == 200
    assert as_username.json()["user"]["id"] == user_id


async def test_ca11_username_in_the_email_field_is_validated_as_email(
    auth_client: AsyncClient,
) -> None:
    """CA-11: usuario sin forma de correo ⇒ 422; con forma y sin coincidencia ⇒ 401."""
    suffix = unique_suffix()
    not_an_email = f"usuario_sin_arroba_{suffix}"

    invalid = await post_login_email(auth_client, not_an_email, DEFAULT_PASSWORD)
    assert invalid.status_code == 422
    assert invalid.json()["errors"] == [{"field": "email", "message": INVALID_EMAIL_MESSAGE}]

    missing = await post_login_email(
        auth_client, f"usuario_{suffix}@example.test", DEFAULT_PASSWORD
    )
    assert missing.status_code == 401
    assert missing.json()["code"] == "invalid_credentials"
