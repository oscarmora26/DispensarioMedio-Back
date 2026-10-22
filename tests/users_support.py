"""Utilidades de apoyo para los tests de FEAT-004 (gestion de usuarios)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import (
    DEFAULT_PASSWORD,
    auth_headers,
    cached_password_hash,
    create_auth_user,
    token_for,
)
from tests.db_support import create_doctor, get_role_id, unique_suffix

USERS_PATH = "/api/v1/users"
PASSWORD = DEFAULT_PASSWORD
NEW_PASSWORD = "NuevaClave456"


def user_path(user_id: int) -> str:
    """Ruta de detalle de un usuario."""
    return f"{USERS_PATH}/{user_id}"


def reset_path(user_id: int) -> str:
    """Ruta de restablecimiento de contrasena."""
    return f"{USERS_PATH}/{user_id}/reset-password"


def put_body(
    *,
    username: str,
    email: str,
    role_id: int,
    doctor_id: int | None = None,
    is_active: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    """Body de reemplazo completo para `PUT /users/{id}`."""
    body: dict[str, Any] = {
        "username": username,
        "email": email,
        "role_id": role_id,
        "doctor_id": doctor_id,
        "is_active": is_active,
    }
    body.update(extra)
    return body


def assert_error_envelope(response: Response, status: int, code: str) -> dict[str, Any]:
    """Comprueba el envelope exacto `{detail, code, errors}` sin `details`."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"detail", "code", "errors"}, body
    assert "details" not in body
    assert body["code"] == code, body
    assert isinstance(body["detail"], str) and body["detail"]
    assert isinstance(body["errors"], list)
    return body


def error_fields(response: Response) -> set[str]:
    """Devuelve el conjunto de `field` de una respuesta 422."""
    assert response.status_code == 422, response.text
    return {item["field"] for item in response.json()["errors"]}


async def admin_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un ADMIN activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"actor-{suffix}",
        email=f"actor.{suffix}@example.test",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id))


async def medico_user(
    conn: AsyncConnection,
    *,
    doctor_id: int | None = None,
    is_active: bool = True,
    password: str = PASSWORD,
) -> tuple[int, str, str]:
    """Crea un MEDICO y devuelve `(id, username, token)`."""
    suffix = unique_suffix()
    username = f"medico_{suffix}"
    user_id = await create_auth_user(
        conn,
        username=username,
        email=f"medico_{suffix}@example.test",
        role="MEDICO",
        doctor_id=doctor_id,
        is_active=is_active,
        password=password,
        password_hash=cached_password_hash(password) if password == PASSWORD else None,
    )
    return user_id, username, token_for(user_id, role="MEDICO", doctor_id=doctor_id)


async def fetch_user_row(conn: AsyncConnection, user_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de un usuario, incluidas las fechas."""
    result = await conn.execute(
        text(
            """
            SELECT u.id, u.username, u.email, u.password_hash, u.role_id, r.name AS role,
                   u.doctor_id, u.is_active, u.last_login_at, u.created_at, u.updated_at
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.id = :user_id
            """
        ),
        {"user_id": user_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def count_by_username(conn: AsyncConnection, username: str) -> int:
    """Cuenta filas por `username` (CITEXT: case-insensitive)."""
    result = await conn.execute(
        text("SELECT count(*) FROM users WHERE username = :username"),
        {"username": username},
    )
    return int(result.scalar_one())


async def count_active_admins(conn: AsyncConnection) -> int:
    """Cuenta administradores activos."""
    result = await conn.execute(
        text(
            """
            SELECT count(*)
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE r.name = 'ADMIN' AND u.is_active
            """
        )
    )
    return int(result.scalar_one())


async def nonexistent_user_id(conn: AsyncConnection) -> int:
    """Devuelve un id que no existe en `users`."""
    result = await conn.execute(text("SELECT coalesce(max(id), 0) + 100000 FROM users"))
    return int(result.scalar_one())


async def set_last_login_at(conn: AsyncConnection, user_id: int, value: datetime | None) -> None:
    """Fija `last_login_at` por SQL directo (para el catalogo de orden)."""
    await conn.execute(
        text("UPDATE users SET last_login_at = :value WHERE id = :user_id"),
        {"value": value, "user_id": user_id},
    )


async def disable_other_admins(conn: AsyncConnection, actor_id: int) -> None:
    """Desactiva dentro de la transaccion a cualquier otro ADMIN activo."""
    await conn.execute(
        text(
            """
            UPDATE users
            SET is_active = false
            WHERE role_id = (SELECT id FROM roles WHERE name = 'ADMIN')
              AND id <> :actor_id
            """
        ),
        {"actor_id": actor_id},
    )


def utc_now() -> datetime:
    """Hora UTC actual, para fijar `last_login_at`."""
    return datetime.now(UTC)


def minutes_ago(minutes: int) -> datetime:
    """Instante UTC desplazado hacia atras."""
    return utc_now() - timedelta(minutes=minutes)


__all__ = [
    "NEW_PASSWORD",
    "PASSWORD",
    "USERS_PATH",
    "admin_actor",
    "assert_error_envelope",
    "count_active_admins",
    "count_by_username",
    "create_doctor",
    "disable_other_admins",
    "error_fields",
    "fetch_user_row",
    "get_role_id",
    "medico_user",
    "minutes_ago",
    "nonexistent_user_id",
    "put_body",
    "reset_path",
    "set_last_login_at",
    "user_path",
    "utc_now",
]
