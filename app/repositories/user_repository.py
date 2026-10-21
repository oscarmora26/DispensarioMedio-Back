"""Acceso a datos de usuarios y roles."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.role import Role
from app.models.user import User


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Busca un usuario por `username` (CITEXT: case-insensitive)."""
    result = await session.execute(
        select(User).options(selectinload(User.role)).where(User.username == username)
    )
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    """Busca un usuario por identificador con su rol cargado."""
    result = await session.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def get_role_by_name(session: AsyncSession, name: str) -> Role | None:
    """Busca un rol por su nombre exacto."""
    result = await session.execute(select(Role).where(Role.name == name))
    return result.scalar_one_or_none()


async def find_user_by_username_or_email(
    session: AsyncSession,
    *,
    username: str,
    email: str,
) -> User | None:
    """Busca un usuario cuyo `username` o `email` coincida (ambos CITEXT).

    `limit(1)` evita `MultipleResultsFound` cuando el `username` coincide con un
    usuario y el `email` con otro distinto: para detectar el duplicado basta con
    que exista una fila.
    """
    result = await session.execute(
        select(User).where(or_(User.username == username, User.email == email)).limit(1)
    )
    return result.scalar_one_or_none()
