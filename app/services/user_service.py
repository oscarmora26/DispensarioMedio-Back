"""Servicios de provisionamiento de usuarios."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError
from app.core.security import hash_password
from app.models.user import User
from app.repositories import user_repository


class AdminRoleMissingError(AppError):
    """No existe el rol `ADMIN`; falta ejecutar las migraciones."""

    status_code = 500
    code = "admin_role_missing"


async def create_admin(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    password: str,
) -> User:
    """Crea el administrador inicial. El llamante confirma la transaccion."""
    role = await user_repository.get_role_by_name(session, "ADMIN")
    if role is None:
        raise AdminRoleMissingError(
            "No existe el rol ADMIN. Ejecute 'alembic upgrade head' antes de crear el administrador"
        )

    existing = await user_repository.find_user_by_username_or_email(
        session,
        username=username,
        email=email,
    )
    if existing is not None:
        raise ConflictError("El usuario ya existe", code="user_exists")

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role_id=role.id,
        role=role,
        doctor_id=None,
        is_active=True,
        last_login_at=None,
    )
    session.add(user)
    await session.flush()
    return user
