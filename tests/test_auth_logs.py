"""Tests de logs y no filtracion de datos sensibles (FEAT-002, CA-39 y CA-40)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import (
    CHANGE_PASSWORD_PATH,
    LOGIN_PATH,
    auth_headers,
    create_auth_user,
    fetch_user,
)
from tests.db_support import unique_suffix

PASSWORD = "ContrasenaMuySecreta987"
NEW_PASSWORD = "ContrasenaNueva654"


async def test_ca39_logs_never_contain_secrets_or_identifiers(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-39: ni contrasenas, ni token, ni hash, ni username/email en los logs."""
    username = f"log_user_{unique_suffix()}"
    email = f"{username}@example.test"
    await create_auth_user(db_conn, username=username, email=email, password=PASSWORD)
    caplog.set_level(logging.INFO)

    success = await auth_client.post(LOGIN_PATH, json={"username": username, "password": PASSWORD})
    assert success.status_code == 200
    token: str = success.json()["access_token"]

    assert (
        await auth_client.post(
            LOGIN_PATH, json={"username": username, "password": "ClaveEquivocada999"}
        )
    ).status_code == 401
    for _ in range(4):
        await auth_client.post(
            LOGIN_PATH, json={"username": username, "password": "ClaveEquivocada999"}
        )
    assert (
        await auth_client.post(LOGIN_PATH, json={"username": username, "password": PASSWORD})
    ).status_code == 429

    changed = await auth_client.post(
        CHANGE_PASSWORD_PATH,
        headers=auth_headers(token),
        json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert changed.status_code == 200

    text = caplog.text
    assert PASSWORD not in text
    assert NEW_PASSWORD not in text
    assert token not in text
    assert "$argon2" not in text
    assert username not in text
    assert email not in text
    assert "auth.login_succeeded" in text
    assert "request.completed" in text


async def test_ca40_login_body_is_not_logged(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-40: `request.completed` no serializa body, headers ni credenciales."""
    username = f"log_user_{unique_suffix()}"
    user_id = await create_auth_user(db_conn, username=username, password=PASSWORD)
    caplog.set_level(logging.INFO)

    response = await auth_client.post(
        LOGIN_PATH,
        json={"username": username, "password": PASSWORD},
        headers={"Authorization": "Bearer cabecera-de-prueba", "X-Trace": "traza"},
    )
    assert response.status_code == 200

    events: list[dict[str, Any]] = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event") == "request.completed"
        and record.msg.get("path") == LOGIN_PATH
    ]
    assert events, "no se capturo el evento request.completed del login"

    event = events[-1]
    for key in ("method", "path", "status", "duration_ms", "client_ip"):
        assert key in event
    for forbidden in ("username", "password", "body", "headers", "authorization"):
        assert forbidden not in event

    assert (await fetch_user(db_conn, user_id))["last_login_at"] is not None
