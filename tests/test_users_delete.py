"""Desactivacion logica de usuarios de FEAT-004 (CA-28, CA-29)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import LOGIN_PATH
from tests.users_support import (
    PASSWORD,
    admin_actor,
    assert_error_envelope,
    fetch_user_row,
    medico_user,
    nonexistent_user_id,
    user_path,
)


def _deactivated_events(caplog: pytest.LogCaptureFixture, user_id: int) -> list[dict]:
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event") == "user.deactivated"
        and record.msg.get("user_id") == user_id
    ]


async def test_ca28_delete_is_a_logical_deactivation(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-28: DELETE ⇒ 204 vacio, fila conservada inactiva y log `user.deactivated`."""
    actor_id, headers = await admin_actor(db_conn)
    target_id, username, _ = await medico_user(db_conn)
    caplog.set_level(logging.INFO)

    response = await auth_client.delete(user_path(target_id), headers=headers)

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert "json" not in response.headers.get("content-type", "")

    row = await fetch_user_row(db_conn, target_id)
    assert row is not None
    assert row["is_active"] is False

    login = await auth_client.post(LOGIN_PATH, json={"username": username, "password": PASSWORD})
    assert_error_envelope(login, 403, "inactive_user")

    events = _deactivated_events(caplog, target_id)
    assert len(events) == 1
    assert events[0]["actor_id"] == actor_id


async def test_ca29_delete_is_idempotent_and_self_delete_is_blocked(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-29: segundo DELETE no escribe ni loguea; 404 y auto-DELETE 400."""
    actor_id, headers = await admin_actor(db_conn)
    target_id, _, _ = await medico_user(db_conn)
    caplog.set_level(logging.INFO)

    first = await auth_client.delete(user_path(target_id), headers=headers)
    assert first.status_code == 204
    after_first = await fetch_user_row(db_conn, target_id)
    assert after_first is not None
    assert after_first["is_active"] is False

    second = await auth_client.delete(user_path(target_id), headers=headers)
    assert second.status_code == 204
    assert second.content == b""
    after_second = await fetch_user_row(db_conn, target_id)
    assert after_second is not None
    assert after_second["is_active"] is False
    assert after_second["updated_at"] == after_first["updated_at"]
    assert len(_deactivated_events(caplog, target_id)) == 1

    missing = await auth_client.delete(
        user_path(await nonexistent_user_id(db_conn)), headers=headers
    )
    assert_error_envelope(missing, 404, "user_not_found")

    self_delete = await auth_client.delete(user_path(actor_id), headers=headers)
    assert_error_envelope(self_delete, 400, "cannot_deactivate_self")
    actor = await fetch_user_row(db_conn, actor_id)
    assert actor is not None
    assert actor["is_active"] is True
