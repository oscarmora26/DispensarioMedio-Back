"""Modelo de ubicaciones fisicas del inventario."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Identity, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UpdatedAtMixin

if TYPE_CHECKING:
    from app.models.medication import Medication


class Location(UpdatedAtMixin, Base):
    """Posicion fisica (estante, seccion, celda) donde se guarda un medicamento."""

    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("shelf", "section", "cell", name="uq_locations_position"),)

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    description: Mapped[str] = mapped_column(CITEXT, nullable=False)
    shelf: Mapped[str] = mapped_column(CITEXT, nullable=False)
    section: Mapped[str] = mapped_column(CITEXT, nullable=False)
    cell: Mapped[str] = mapped_column(CITEXT, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    medications: Mapped[list[Medication]] = relationship(back_populates="location", lazy="raise")
