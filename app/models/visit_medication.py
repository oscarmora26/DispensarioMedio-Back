"""Modelo de medicamentos entregados en una visita."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from app.models.medication import Medication
    from app.models.visit import Visit


class VisitMedication(CreatedAtMixin, Base):
    """Detalle de un medicamento entregado durante una visita."""

    __tablename__ = "visit_medications"
    __table_args__ = (
        UniqueConstraint("visit_id", "medication_id", name="uq_visit_medications"),
        CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name="quantity_positive",
        ),
        Index("ix_visit_medications_visit_id", "visit_id"),
        Index("ix_visit_medications_medication_id", "medication_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    visit_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("visits.id", ondelete="CASCADE"),
        nullable=False,
    )
    medication_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("medications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    instructions: Mapped[str | None] = mapped_column(String(255), nullable=True)

    visit: Mapped[Visit] = relationship(back_populates="visit_medications", lazy="raise")
    medication: Mapped[Medication] = relationship(back_populates="visit_medications", lazy="raise")
