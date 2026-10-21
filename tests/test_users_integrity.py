"""Concurrencia e integridad de FEAT-004 (CA-34, CA-35)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.repositories import user_repository
from tests.auth_support import create_auth_user
from tests.db_support import get_role_id, unique_suffix
from tests.users_support import (
    PASSWORD,
    USERS_PATH,
    admin_actor,
    assert_error_envelope,
    count_by_username,
    fetch_user_row,
    put_body,
    user_path,
)


async def test_ca34_put_retiring_admin_locks_user_then_role_then_counts(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-34: PUT que retira un ADMIN bloquea usuario → rol → conteo."""
    calls: list[str] = []
    original_get = user_repository.get_user_by_id_for_update
    original_lock = user_repository.lock_admin_role
    original_count = user_repository.count_other_active_admins

    async def spy_get(session, user_id):
        calls.append("get_user_by_id_for_update")
        return await original_get(session, user_id)

    async def spy_lock(session):
        calls.append("lock_admin_role")
        return await original_lock(session)

    async def spy_count(session, user_id):
        calls.append("count_other_active_admins")
        return await original_count(session, user_id)

    monkeypatch.setattr(user_repository, "get_user_by_id_for_update", spy_get)
    monkeypatch.setattr(user_repository, "lock_admin_role", spy_lock)
    monkeypatch.setattr(user_repository, "count_other_active_admins", spy_count)

    _, headers = await admin_actor(db_conn)
    target_id = await create_auth_user(db_conn, role="ADMIN")
    target = await fetch_user_row(db_conn, target_id)
    assert target is not None
    medico_role_id = await get_role_id(db_conn, "MEDICO")

    response = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=target["username"],
            email=target["email"],
            role_id=medico_role_id,
            doctor_id=None,
            is_active=True,
        ),
    )

    assert response.status_code == 200, response.text
    assert calls == [
        "get_user_by_id_for_update",
        "lock_admin_role",
        "count_other_active_admins",
    ]

    # DELETE de un ADMIN activo sigue el mismo orden.
    calls.clear()
    second_target = await create_auth_user(db_conn, role="ADMIN")
    deleted = await auth_client.delete(user_path(second_target), headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert calls == [
        "get_user_by_id_for_update",
        "lock_admin_role",
        "count_other_active_admins",
    ]


async def test_ca34_put_without_retiring_admin_does_not_lock_the_role(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-34: un PUT que no retira un ADMIN no invoca `lock_admin_role`."""
    calls: list[str] = []
    original_get = user_repository.get_user_by_id_for_update
    original_lock = user_repository.lock_admin_role

    async def spy_get(session, user_id):
        calls.append("get_user_by_id_for_update")
        return await original_get(session, user_id)

    async def spy_lock(session):
        calls.append("lock_admin_role")
        return await original_lock(session)

    monkeypatch.setattr(user_repository, "get_user_by_id_for_update", spy_get)
    monkeypatch.setattr(user_repository, "lock_admin_role", spy_lock)

    _, headers = await admin_actor(db_conn)
    target_id = await create_auth_user(db_conn, role="MEDICO")
    target = await fetch_user_row(db_conn, target_id)
    assert target is not None
    medico_role_id = await get_role_id(db_conn, "MEDICO")
    tag = unique_suffix()

    response = await auth_client.put(
        user_path(target_id),
        headers=headers,
        json=put_body(
            username=target["username"],
            email=f"nuevo_{tag}@example.test",
            role_id=medico_role_id,
            doctor_id=None,
            is_active=True,
        ),
    )

    assert response.status_code == 200, response.text
    assert calls == ["get_user_by_id_for_update"]


class _ConstraintViolation(Exception):
    """Imita la excepcion de `asyncpg` con el nombre de la constraint."""

    def __init__(self, constraint: str) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.constraint_name = constraint


@pytest.mark.parametrize(
    ("constraint", "expected_code"),
    [
        ("uq_users_username", "user_exists"),
        ("uq_users_email", "user_exists"),
        ("uq_users_doctor", "doctor_already_assigned"),
    ],
)
async def test_ca35_integrity_error_is_translated_and_session_stays_usable(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    constraint: str,
    expected_code: str,
) -> None:
    """CA-35: `IntegrityError` del commit ⇒ 409 del catalogo y sesion usable."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    username = f"integrity_{tag}"

    async def failing_commit(session: AsyncSession) -> None:
        raise IntegrityError("INSERT INTO users ...", {}, _ConstraintViolation(constraint))

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await auth_client.post(
        USERS_PATH,
        headers=headers,
        json={
            "username": username,
            "email": f"integrity_{tag}@example.test",
            "password": PASSWORD,
            "role_id": await get_role_id(db_conn, "MEDICO"),
        },
    )

    assert_error_envelope(response, 409, expected_code)
    assert await count_by_username(db_conn, username) == 0

    usable = await auth_client.get(USERS_PATH, headers=headers)
    assert usable.status_code == 200, usable.text
    assert "items" in usable.json()
