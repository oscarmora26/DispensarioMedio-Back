"""Logs estructurados de FEAT-004 (CA-37)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import create_auth_user
from tests.db_support import get_role_id, unique_suffix
from tests.users_support import (
    NEW_PASSWORD,
    PASSWORD,
    USERS_PATH,
    admin_actor,
    fetch_user_row,
    put_body,
    reset_path,
    user_path,
)


async def test_ca37_all_events_carry_ids_and_never_leak_secrets(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-37: los cuatro eventos con `user_id`/`actor_id` y sin datos sensibles."""
    actor_id, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    username = f"logs_{tag}"
    email = f"logs_{tag}@example.test"

    caplog.set_level(logging.INFO)

    created = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json={
            "username": username,
            "email": email,
            "password": PASSWORD,
            "role_id": await get_role_id(db_conn, "MEDICO"),
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]

    updated = await auth_client.put(
        user_path(user_id),
        headers=headers,
        json=put_body(
            username=username,
            email=f"nuevo_{tag}@example.test",
            role_id=await get_role_id(db_conn, "MEDICO"),
            doctor_id=None,
            is_active=True,
        ),
    )
    assert updated.status_code == 200, updated.text

    deleted = await auth_client.delete(user_path(user_id), headers=headers)
    assert deleted.status_code == 204, deleted.text

    reset = await auth_client.post(
        reset_path(user_id), headers=headers, json={"new_password": NEW_PASSWORD}
    )
    assert reset.status_code == 200, reset.text

    row = await fetch_user_row(db_conn, user_id)
    assert row is not None

    events = {
        record.msg["event"]: record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event")
        in {"user.created", "user.updated", "user.deactivated", "user.password_reset"}
    }
    assert set(events) == {
        "user.created",
        "user.updated",
        "user.deactivated",
        "user.password_reset",
    }
    for event in events.values():
        assert event["user_id"] == user_id
        assert event["actor_id"] == actor_id
        assert set(event) >= {"user_id", "actor_id"}

    logged = caplog.text
    for secret in (
        PASSWORD,
        NEW_PASSWORD,
        "$argon2",
        username,
        email,
        row["password_hash"],
    ):
        assert secret not in logged

    assert "request.completed" in logged


async def test_ca37_put_that_deactivates_emits_user_deactivated(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-37/regla 47: el paso de activo a inactivo por PUT emite `user.deactivated`."""
    _, headers = await admin_actor(db_conn)
    target_id = await create_auth_user(db_conn, role="MEDICO")
    target = await fetch_user_row(db_conn, target_id)
    assert target is not None
    caplog.set_level(logging.INFO)

    response = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=target["username"],
            email=target["email"],
            role_id=await get_role_id(db_conn, "MEDICO"),
            doctor_id=None,
            is_active=False,
        ),
    )
    assert response.status_code == 200, response.text

    names = [record.msg.get("event") for record in caplog.records if isinstance(record.msg, dict)]
    assert "user.deactivated" in names
    assert "user.updated" not in names
