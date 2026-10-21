"""Utilidades de apoyo para los tests de FEAT-002 (autenticacion)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from tests.db_support import get_role_id, insert_id, unique_suffix

DEFAULT_PASSWORD = "ClaveDePrueba123"
LOGIN_PATH = "/api/v1/auth/login"
ME_PATH = "/api/v1/auth/me"
CHANGE_PASSWORD_PATH = "/api/v1/auth/change-password"


async def create_auth_user(
    conn: AsyncConnection,
    *,
    username: str | None = None,
    email: str | None = None,
    password: str | None = DEFAULT_PASSWORD,
    password_hash: str | None = None,
    role: str = "ADMIN",
    doctor_id: int | None = None,
    is_active: bool = True,
) -> int:
    """Inserta un usuario con un hash Argon2id real y devuelve su id."""
    suffix = unique_suffix()
    resolved_hash = password_hash if password_hash is not None else hash_password(password or "")
    return await insert_id(
        conn,
        """
        INSERT INTO users (username, email, password_hash, role_id, doctor_id, is_active)
        VALUES (:username, :email, :password_hash, :role_id, :doctor_id, :is_active)
        RETURNING id
        """,
        {
            "username": username or f"user_{suffix}",
            "email": email or f"{suffix}@example.test",
            "password_hash": resolved_hash,
            "role_id": await get_role_id(conn, role),
            "doctor_id": doctor_id,
            "is_active": is_active,
        },
    )


async def fetch_user(conn: AsyncConnection, user_id: int) -> dict[str, Any] | None:
    """Lee de la base la fila del usuario con el nombre de su rol."""
    result = await conn.execute(
        text(
            """
            SELECT u.id, u.username, u.email, u.password_hash, r.name AS role,
                   u.doctor_id, u.is_active, u.last_login_at
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.id = :user_id
            """
        ),
        {"user_id": user_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def count_users_by_username(conn: AsyncConnection, username: str) -> int:
    """Cuenta las filas cuyo username coincide (CITEXT)."""
    result = await conn.execute(
        text("SELECT count(*) FROM users WHERE username = :username"),
        {"username": username},
    )
    return int(result.scalar_one())


def token_for(
    user_id: int,
    *,
    role: str = "ADMIN",
    doctor_id: int | None = None,
    expires_minutes: int | None = None,
    secret: str | None = None,
) -> str:
    """Emite un token valido para el usuario indicado con los settings actuales."""
    token, _ = create_access_token(
        subject=str(user_id),
        role=role,
        doctor_id=doctor_id,
        expires_minutes=expires_minutes,
        secret=secret,
    )
    return token


def signed_token(payload: dict[str, Any], *, secret: str | None = None) -> str:
    """Firma manualmente un payload con el secreto y algoritmo de la app."""
    settings = get_settings()
    return jwt.encode(
        payload,
        secret if secret is not None else settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def auth_headers(token: str) -> dict[str, str]:
    """Cabecera Bearer para las peticiones autenticadas."""
    return {"Authorization": f"Bearer {token}"}


def now_epoch() -> int:
    """Epoch UTC actual en segundos."""
    return int(datetime.now(UTC).timestamp())


def token_payload(token: str) -> dict[str, Any]:
    """Decodifica sin validar para inspeccionar claims en los tests."""
    settings = get_settings()
    payload: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"verify_exp": False},
    )
    return payload
