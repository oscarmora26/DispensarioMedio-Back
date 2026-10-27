"""Modelo de estantes (shelves) del inventario."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Identity, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UpdatedAtMixin

if TYPE_CHECKING:
    from app.models.cell import Cell


class Shelf(UpdatedAtMixin, Base):
    """Estante fisico que agrupa celdas."""

    __tablename__ = "shelves"
    __table_args__ = (UniqueConstraint("name", name="uq_shelves_name"),)

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    name: Mapped[str] = mapped_column(CITEXT, nullable=False)
    description: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    cells: Mapped[list[Cell]] = relationship(back_populates="shelf", lazy="raise")
