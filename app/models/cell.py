"""Modelo de celdas (cells) del inventario, pertenecientes a un estante."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, ForeignKey, Identity, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UpdatedAtMixin

if TYPE_CHECKING:
    from app.models.medication import Medication
    from app.models.shelf import Shelf


class Cell(UpdatedAtMixin, Base):
    """Celda fisica dentro de un estante."""

    __tablename__ = "cells"
    __table_args__ = (
        UniqueConstraint("shelf_id", "section", "cell", name="uq_cells_position"),
        Index("ix_cells_shelf_id", "shelf_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    shelf_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("shelves.id", ondelete="CASCADE"),
        nullable=False,
    )
    section: Mapped[str] = mapped_column(CITEXT, nullable=False)
    cell: Mapped[str] = mapped_column(CITEXT, nullable=False)
    description: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    shelf: Mapped[Shelf] = relationship(back_populates="cells", lazy="raise")
    medications: Mapped[list[Medication]] = relationship(back_populates="cell", lazy="raise")
