"""Modelo de marcas de medicamentos."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Identity, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UpdatedAtMixin

if TYPE_CHECKING:
    from app.models.medication import Medication


class Brand(UpdatedAtMixin, Base):
    """Marca comercial de un medicamento."""

    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("description", name="uq_brands_description"),)

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    description: Mapped[str] = mapped_column(CITEXT, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    medications: Mapped[list[Medication]] = relationship(back_populates="brand", lazy="raise")
