"""Tests de logs del login por email (FEAT-005, CA-15)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import LOGIN_PATH, create_auth_user
from tests.db_support import unique_suffix

PASSWORD = "ContrasenaEmailMuySecreta987"


async def test_ca15_email_login_logs_do_not_leak_identifiers_or_secrets(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-15: los logs conservan los eventos y no filtran email, username ni secretos."""
    suffix = unique_suffix()
    username = f"log_email_user_{suffix}"
    email = f"log_correo_{suffix}@example.test"
    await create_auth_user(db_conn, username=username, email=email, password=PASSWORD)
    caplog.set_level(logging.INFO)

    success = await auth_client.post(LOGIN_PATH, json={"email": email, "password": PASSWORD})
    assert success.status_code == 200
    token: str = success.json()["access_token"]

    missing_email = f"missing_log_{suffix}@example.test"
    for _ in range(5):
        failed = await auth_client.post(
            LOGIN_PATH, json={"email": missing_email, "password": "ClaveEquivocada999"}
        )
        assert failed.status_code == 401
    limited = await auth_client.post(
        LOGIN_PATH, json={"email": missing_email, "password": PASSWORD}
    )
    assert limited.status_code == 429

    text = caplog.text
    assert email not in text
    assert missing_email not in text
    assert username not in text
    assert PASSWORD not in text
    assert "ClaveEquivocada999" not in text
    assert token not in text
    assert "$argon2" not in text
    assert "auth.login_succeeded" in text
    assert "auth.login_failed" in text
    assert "auth.rate_limited" in text
