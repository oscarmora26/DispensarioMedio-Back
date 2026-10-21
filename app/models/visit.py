"""Modelo de visitas medicas."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UpdatedAtMixin

if TYPE_CHECKING:
    from app.models.doctor import Doctor
    from app.models.patient import Patient
    from app.models.user import User
    from app.models.visit_medication import VisitMedication


class Visit(UpdatedAtMixin, Base):
    """Atencion registrada por un medico a un paciente."""

    __tablename__ = "visits"
    __table_args__ = (
        CheckConstraint("status IN ('REGISTRADA', 'ANULADA')", name="status_allowed"),
        CheckConstraint(
            "(status = 'REGISTRADA' AND annulled_by IS NULL AND annulled_at IS NULL"
            " AND annulment_reason IS NULL) OR (status = 'ANULADA'"
            " AND annulled_by IS NOT NULL AND annulled_at IS NOT NULL"
            " AND annulment_reason IS NOT NULL"
            " AND btrim(annulment_reason) <> '')",
            name="annulment_consistency",
        ),
        Index("ix_visits_created_by", "created_by"),
        Index("ix_visits_annulled_by", "annulled_by"),
        Index(
            "ix_visits_doctor_visited",
            "doctor_id",
            text("visited_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_visits_patient_visited",
            "patient_id",
            text("visited_at DESC"),
            text("id DESC"),
        ),
        Index("ix_visits_visited", text("visited_at DESC"), text("id DESC")),
        Index(
            "ix_visits_status_visited",
            "status",
            text("visited_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    doctor_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("doctors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    patient_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    visited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symptoms: Mapped[str] = mapped_column(String(2000), nullable=False)
    recommendations: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    annulled_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    annulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    annulment_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    doctor: Mapped[Doctor] = relationship(back_populates="visits", lazy="raise")
    patient: Mapped[Patient] = relationship(back_populates="visits", lazy="raise")
    created_by_user: Mapped[User] = relationship(foreign_keys=[created_by], lazy="raise")
    annulled_by_user: Mapped[User | None] = relationship(foreign_keys=[annulled_by], lazy="raise")
    visit_medications: Mapped[list[VisitMedication]] = relationship(
        back_populates="visit",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
