"""Acceso a datos de medicos (solo lectura para la gestion de usuarios)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor import Doctor


async def get_doctor_by_id(session: AsyncSession, doctor_id: int) -> Doctor | None:
    """Busca un medico por identificador."""
    result = await session.execute(select(Doctor).where(Doctor.id == doctor_id))
    return result.scalar_one_or_none()
