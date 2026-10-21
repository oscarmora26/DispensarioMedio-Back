"""Tests de rate limiting del login por email (FEAT-005, CA-12 a CA-14)."""

from __future__ import annotations

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.rate_limit import build_key, get_login_rate_limiter
from tests.auth_support import DEFAULT_PASSWORD, LOGIN_PATH, create_auth_user, fetch_user
from tests.db_support import unique_suffix

CLIENT_IP = "127.0.0.1"


async def post_login_email(client: AsyncClient, email: str, password: str) -> Response:
    return await client.post(LOGIN_PATH, json={"email": email, "password": password})


async def post_login_username(client: AsyncClient, username: str, password: str) -> Response:
    return await client.post(LOGIN_PATH, json={"username": username, "password": password})


async def test_ca12_fifth_email_failure_blocks_the_sixth_correct_attempt(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: 5 fallos por email y el 6.º con contrasena correcta ⇒ 429."""
    suffix = unique_suffix()
    email = f"limited_{suffix}@example.test"
    user_id = await create_auth_user(db_conn, username=f"limited_{suffix}", email=email)

    for _ in range(5):
        response = await post_login_email(auth_client, email, "ClaveEquivocada999")
        assert response.status_code == 401

    blocked = await post_login_email(auth_client, email, DEFAULT_PASSWORD)

    assert blocked.status_code == 429
    body = blocked.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "rate_limited"
    assert body["detail"] == "Demasiados intentos fallidos. Intente de nuevo más tarde"
    assert body["errors"] == []

    retry_after = blocked.headers["retry-after"]
    assert 1 <= int(retry_after) <= 900
    assert (await fetch_user(db_conn, user_id))["last_login_at"] is None


async def test_ca12_email_case_and_padding_variants_share_the_counter(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: `EMAIL`, `email` y `"  email  "` comparten el mismo contador."""
    suffix = unique_suffix()
    email = f"variants_{suffix}@example.test"
    await create_auth_user(db_conn, username=f"variants_{suffix}", email=email)

    variants = [email, email.upper(), f"  {email}  ", email.upper(), email]
    for variant in variants:
        assert (
            await post_login_email(auth_client, variant, "ClaveEquivocada999")
        ).status_code == 401

    blocked = await post_login_email(auth_client, f"  {email.upper()} ", DEFAULT_PASSWORD)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"


async def test_ca13_successful_email_login_resets_its_window(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: 4 fallos + exito + 4 fallos + exito ⇒ ambos exitos son 200."""
    suffix = unique_suffix()
    email = f"reset_{suffix}@example.test"
    await create_auth_user(db_conn, username=f"reset_{suffix}", email=email)

    for _ in range(4):
        assert (await post_login_email(auth_client, email, "ClaveEquivocada999")).status_code == 401
    assert (await post_login_email(auth_client, email, DEFAULT_PASSWORD)).status_code == 200

    for _ in range(4):
        assert (await post_login_email(auth_client, email, "ClaveEquivocada999")).status_code == 401
    assert (await post_login_email(auth_client, email, DEFAULT_PASSWORD)).status_code == 200


async def test_ca13_forbidden_and_validation_do_not_consume_email_attempts(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: el 403 de inactivo y el 422 de email invalido no crean ni incrementan claves."""
    suffix = unique_suffix()
    inactive_email = f"inactive_{suffix}@example.test"
    await create_auth_user(
        db_conn,
        username=f"inactive_{suffix}",
        email=inactive_email,
        is_active=False,
    )
    limiter = get_login_rate_limiter()

    for _ in range(5):
        assert (
            await post_login_email(auth_client, inactive_email, DEFAULT_PASSWORD)
        ).status_code == 403
    assert limiter.retry_after(build_key(CLIENT_IP, inactive_email)) is None

    invalid_email = f"sin-arroba-{suffix}"
    for _ in range(5):
        assert (
            await post_login_email(auth_client, invalid_email, DEFAULT_PASSWORD)
        ).status_code == 422
    assert limiter.retry_after(build_key(CLIENT_IP, invalid_email)) is None
    assert len(limiter) == 0


async def test_ca14_email_and_username_use_independent_counters(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-14 (D-4): 5 fallos por email bloquean el email pero no el username."""
    suffix = unique_suffix()
    username = f"dual_{suffix}"
    email = f"dual_{suffix}@example.test"
    await create_auth_user(db_conn, username=username, email=email)
    limiter = get_login_rate_limiter()

    for _ in range(5):
        assert (await post_login_email(auth_client, email, "ClaveEquivocada999")).status_code == 401

    email_key = build_key(CLIENT_IP, email)
    username_key = build_key(CLIENT_IP, username)
    assert limiter.retry_after(email_key) is not None

    assert (await post_login_email(auth_client, email, DEFAULT_PASSWORD)).status_code == 429
    assert limiter.retry_after(email_key) is not None

    by_username = await post_login_username(auth_client, username, DEFAULT_PASSWORD)
    assert by_username.status_code == 200
    assert limiter.retry_after(username_key) is None
    assert limiter.retry_after(email_key) is not None
