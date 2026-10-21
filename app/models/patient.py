"""Modelo de pacientes del dispensario."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Identity,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UpdatedAtMixin

if TYPE_CHECKING:
    from app.models.visit import Visit


class Patient(UpdatedAtMixin, Base):
    """Persona atendida en el dispensario."""

    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint("cedula", name="uq_patients_cedula"),
        CheckConstraint("cedula ~ '^[0-9]{11}$'", name="cedula_format"),
        CheckConstraint(
            "patient_type IN ('ESTUDIANTE', 'EMPLEADO', 'PROFESOR', 'OTROS')",
            name="patient_type_allowed",
        ),
        CheckConstraint(
            "(patient_type = 'ESTUDIANTE' AND carnet IS NOT NULL"
            " AND btrim(carnet) <> '') OR patient_type <> 'ESTUDIANTE'",
            name="carnet_required_for_student",
        ),
        Index(
            "uq_patients_carnet",
            "carnet",
            unique=True,
            postgresql_where=text("carnet IS NOT NULL"),
        ),
        Index(
            "ix_patients_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    cedula: Mapped[str] = mapped_column(String(11), nullable=False)
    carnet: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    patient_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    visits: Mapped[list[Visit]] = relationship(back_populates="patient", lazy="raise")
