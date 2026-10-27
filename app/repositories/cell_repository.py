"""Acceso a datos de celdas (cells)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cell import Cell
from app.models.medication import Medication


async def get_cell_by_id(session: AsyncSession, cell_id: int) -> Cell | None:
    """Busca una celda por identificador."""
    result = await session.execute(select(Cell).where(Cell.id == cell_id))
    return result.scalar_one_or_none()


async def count_medications_by_cell(session: AsyncSession, cell_id: int) -> int:
    """Cuenta los medicamentos ubicados en una celda."""
    result = await session.execute(
        select(func.count(Medication.id)).where(Medication.cell_id == cell_id)
    )
    return int(result.scalar_one())
