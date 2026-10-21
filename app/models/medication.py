"""Modelo de medicamentos del inventario."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Identity,
    Index,
    UniqueConstraint,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UpdatedAtMixin

if TYPE_CHECKING:
    from app.models.brand import Brand
    from app.models.drug_type import DrugType
    from app.models.location import Location
    from app.models.visit_medication import VisitMedication


class Medication(UpdatedAtMixin, Base):
    """Medicamento identificado por descripcion, marca y dosis."""

    __tablename__ = "medications"
    __table_args__ = (
        UniqueConstraint("description", "brand_id", "dose", name="uq_medications_identity"),
        Index("ix_medications_drug_type_id", "drug_type_id"),
        Index("ix_medications_brand_id", "brand_id"),
        Index("ix_medications_location_id", "location_id"),
        Index(
            "ix_medications_description_trgm",
            literal_column("(description::text)"),
            postgresql_using="gin",
            postgresql_ops={"(description::text)": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    description: Mapped[str] = mapped_column(CITEXT, nullable=False)
    drug_type_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("drug_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    brand_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("brands.id", ondelete="RESTRICT"),
        nullable=False,
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dose: Mapped[str] = mapped_column(CITEXT, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    drug_type: Mapped[DrugType] = relationship(back_populates="medications", lazy="raise")
    brand: Mapped[Brand] = relationship(back_populates="medications", lazy="raise")
    location: Mapped[Location] = relationship(back_populates="medications", lazy="raise")
    visit_medications: Mapped[list[VisitMedication]] = relationship(
        back_populates="medication", lazy="raise"
    )
