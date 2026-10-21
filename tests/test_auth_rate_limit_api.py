"""Tests de integracion del rate limiting del login (FEAT-002, CA-30, CA-31, CA-33)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.rate_limit import build_key, get_login_rate_limiter
from tests.auth_support import (
    DEFAULT_PASSWORD,
    LOGIN_PATH,
    create_auth_user,
    fetch_user,
)
from tests.db_support import unique_suffix

CLIENT_IP = "127.0.0.1"


async def post_login(
    client: AsyncClient,
    username: str,
    password: str,
    *,
    headers: dict[str, str] | None = None,
) -> int:
    response = await client.post(
        LOGIN_PATH,
        json={"username": username, "password": password},
        headers=headers,
    )
    return int(response.status_code)


async def test_ca30_fifth_failure_blocks_sixth_correct_attempt(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-30: 5 fallos y el 6.º con contrasena correcta responde 429."""
    username = f"user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username)

    for _ in range(5):
        assert await post_login(auth_client, username, "ClaveEquivocada999") == 401

    blocked = await auth_client.post(
        LOGIN_PATH, json={"username": username, "password": DEFAULT_PASSWORD}
    )

    assert blocked.status_code == 429
    body = blocked.json()
    assert set(body) == {"detail", "code", "errors"}
    assert body["code"] == "rate_limited"
    assert body["detail"] == "Demasiados intentos fallidos. Intente de nuevo más tarde"
    assert body["errors"] == []

    retry_after = blocked.headers["retry-after"]
    assert 1 <= int(retry_after) <= 900
    assert (await fetch_user(db_conn, user_id))["last_login_at"] is None


async def test_ca31a_failures_are_isolated_per_username(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-31(a): los fallos de A no bloquean a B desde la misma IP."""
    user_a = f"user_a_{unique_suffix()}"
    user_b = f"user_b_{unique_suffix()}"
    await create_auth_user(db_conn, username=user_a)
    await create_auth_user(db_conn, username=user_b)

    for _ in range(5):
        assert await post_login(auth_client, user_a, "ClaveEquivocada999") == 401

    assert await post_login(auth_client, user_b, DEFAULT_PASSWORD) == 200


async def test_ca31b_success_resets_the_window(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-31(b): un login correcto reinicia el contador de esa clave."""
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)

    for _ in range(4):
        assert await post_login(auth_client, username, "ClaveEquivocada999") == 401
    assert await post_login(auth_client, username, DEFAULT_PASSWORD) == 200

    for _ in range(4):
        assert await post_login(auth_client, username, "ClaveEquivocada999") == 401
    assert await post_login(auth_client, username, DEFAULT_PASSWORD) == 200


async def test_ca31c_inactive_and_validation_do_not_consume_attempts(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-31(c): 403 y 422 no incrementan el contador."""
    inactive = f"inactivo_{unique_suffix()}"
    await create_auth_user(db_conn, username=inactive, is_active=False)

    for _ in range(5):
        assert await post_login(auth_client, inactive, DEFAULT_PASSWORD) == 403
    assert get_login_rate_limiter().retry_after(build_key(CLIENT_IP, inactive)) is None

    validated = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=validated)
    for _ in range(5):
        response = await auth_client.post(LOGIN_PATH, json={"username": validated})
        assert response.status_code == 422

    assert await post_login(auth_client, validated, DEFAULT_PASSWORD) == 200


async def test_ca31d_username_variants_share_the_counter(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-31(d): mayusculas y espacios comparten contador."""
    username = f"admin_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)

    variants = [username, username.upper(), f" {username} ", username.upper(), username]
    for variant in variants:
        assert await post_login(auth_client, variant, "ClaveEquivocada999") == 401

    blocked = await auth_client.post(
        LOGIN_PATH, json={"username": f"  {username.upper()} ", "password": DEFAULT_PASSWORD}
    )
    assert blocked.status_code == 429


async def test_ca33_singleton_configure_and_forwarded_for_ignored(
    auth_client_factory: Callable[..., Awaitable[AsyncClient]], db_conn: AsyncConnection
) -> None:
    """CA-33: singleton, `configure()` en caliente y `X-Forwarded-For` ignorado."""
    limiter = get_login_rate_limiter()
    assert limiter is get_login_rate_limiter()

    limiter.configure(max_attempts=2, window_seconds=60)
    client = await auth_client_factory()
    username = f"user_{unique_suffix()}"
    await create_auth_user(db_conn, username=username)

    assert await post_login(client, username, "ClaveEquivocada999") == 401
    assert await post_login(client, username, "ClaveEquivocada999") == 401
    third = await post_login(client, username, DEFAULT_PASSWORD)
    assert third == 429

    limiter.configure(max_attempts=5, window_seconds=900)
    forwarded = f"user_xff_{unique_suffix()}"
    await create_auth_user(db_conn, username=forwarded)
    for index in range(5):
        assert (
            await post_login(
                client,
                forwarded,
                "ClaveEquivocada999",
                headers={"X-Forwarded-For": f"10.0.0.{index}"},
            )
            == 401
        )

    blocked = await post_login(
        client,
        forwarded,
        DEFAULT_PASSWORD,
        headers={"X-Forwarded-For": "10.0.0.99"},
    )
    assert blocked == 429
