"""Acceso a datos de refresh tokens (FEAT-020)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


async def get_by_hash_for_update(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    """Busca el token por hash y bloquea la fila hasta el final de la transaccion.

    El bloqueo serializa dos refrescos concurrentes del mismo token: el segundo
    observa el estado ya rotado en lugar de rotar dos veces (FEAT-020 D-5).
    """
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    return result.scalar_one_or_none()


async def get_by_hash(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    """Busca el token por hash sin bloquear la fila."""
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    return result.scalar_one_or_none()


def add(session: AsyncSession, token: RefreshToken) -> None:
    """Agrega un token nuevo a la sesion (la persistencia la confirma el servicio)."""
    session.add(token)


async def revoke_family(
    session: AsyncSession,
    *,
    family_id: uuid.UUID,
    reason: str,
    now: datetime,
) -> int:
    """Revoca todos los tokens vivos de una familia y devuelve cuantos alcanzo."""
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    return result.rowcount or 0


async def revoke_all_for_user(
    session: AsyncSession,
    *,
    user_id: int,
    reason: str,
    now: datetime,
) -> int:
    """Revoca todas las sesiones vivas de un usuario y devuelve cuantas alcanzo."""
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    return result.rowcount or 0


async def delete_expired_for_user(
    session: AsyncSession,
    *,
    user_id: int,
    now: datetime,
) -> int:
    """Borra los tokens expirados del usuario (limpieza perezosa del login)."""
    result = await session.execute(
        delete(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.expires_at <= now,
        )
    )
    return result.rowcount or 0
